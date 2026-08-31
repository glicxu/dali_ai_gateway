from __future__ import annotations

import json
from pathlib import Path
from threading import Thread

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_WORKLOAD_GRANTS, Settings
from app.core.policy import PolicyGeneration, PolicyGenerationDocument, PolicyStore


def _generation(generation_id: str, *, model: str) -> PolicyGeneration:
    document = PolicyGenerationDocument.model_validate(
        {
            "generation_id": generation_id,
            "profiles": {
                "classroom.translation.economy": {
                    "capability": "text_generation",
                    "provider": "gemini",
                    "model": model,
                }
            },
            "grants": {
                "dali_classroom_server": {
                    "products": ["classroom"],
                    "profiles": ["classroom.translation.economy"],
                    "capabilities": ["text_generation"],
                }
            },
        }
    )
    return PolicyGeneration.from_document(document)


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "system_prompt",
        "instruction",
        "terminology",
        "workflow",
        "plan",
        "tier",
        "product_content",
        "user_id",
        "account_id",
        "mcp_server",
        "durable_state",
        "retention",
    ],
)
def test_profile_policy_rejects_product_owned_fields(forbidden_field: str) -> None:
    configured = {
        "classroom.translation.economy": {
            "capability": "text_generation",
            "provider": "gemini",
            "model": "gemini-test",
            forbidden_field: "must-stay-in-product-service",
        }
    }
    settings = Settings(model_profiles_json=json.dumps(configured))

    with pytest.raises(ValidationError):
        settings.policy_generation()


def test_grant_must_reference_known_profile() -> None:
    settings = Settings(
        workload_grants_json=json.dumps(
            {
                "dali_classroom_server": {
                    "products": ["classroom"],
                    "profiles": ["classroom.unknown"],
                    "capabilities": ["text_generation"],
                }
            }
        )
    )

    with pytest.raises(ValidationError):
        settings.policy_generation()


def test_dali_chat_has_an_independent_optional_demo_grant() -> None:
    settings = Settings()
    generation = settings.policy_generation()
    grant = generation.grants["dali_chat_server"]

    assert not grant.enabled
    assert grant.products == frozenset({"dali_chat"})
    assert grant.capabilities == frozenset(
        {
            "text_generation",
                "audio_transcription",
                "realtime_transcription",
                "realtime_translation",
                "speech_synthesis",
            "image_analysis",
            "video_analysis",
        }
    )
    assert set(DEFAULT_WORKLOAD_GRANTS["dali_chat_server"]["profiles"]) == set(
        grant.profiles
    )
    assert all(
        not generation.profiles[name].required_for_readiness for name in grant.profiles
    )
    assert settings.caller_limits()["dali_chat_server"] == 2


def test_admission_lease_ttl_is_explicitly_bounded() -> None:
    assert Settings(admission_lease_ttl_seconds=30).admission_lease_ttl_seconds == 30
    with pytest.raises(ValidationError):
        Settings(admission_lease_ttl_seconds=29)
    with pytest.raises(ValidationError):
        Settings(admission_lease_ttl_seconds=1801)


def test_reviewed_aws_us2_generation_enables_only_two_product_workloads() -> None:
    path = Path(__file__).parents[1] / "deploy" / "aws-us2" / "two-product.env.example"
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    settings = Settings(
        policy_generation_id=values["AI_GATEWAY_POLICY_GENERATION_ID"],
        workload_grants_json=values["AI_GATEWAY_WORKLOAD_GRANTS_JSON"],
    )

    generation = settings.policy_generation()
    assert generation.generation_id == "aws-us2-classroom-chat-v4"
    assert {
        "dali_chat.text.openai.gpt-5-6-sol",
        "dali_chat.text.openai.gpt-5-6-terra",
        "dali_chat.text.openai.gpt-5-6-luna",
    }.issubset(generation.grants["dali_chat_server"].profiles)
    assert {name for name, grant in generation.grants.items() if grant.enabled} == {
        "dali_classroom_server",
        "dali_chat_server",
    }
    assert generation.grants["dali_classroom_server"].products == frozenset(
        {"classroom"}
    )
    assert generation.grants["dali_chat_server"].products == frozenset({"dali_chat"})


def test_configuration_rejects_duplicate_keys() -> None:
    with pytest.raises(ValidationError, match="duplicate key"):
        Settings(
            workload_grants_json=(
                '{"dali_classroom_server":{"products":["classroom"],'
                '"products":["interprete"],"profiles":["shared.text.gemini"],'
                '"capabilities":["text_generation"]}}'
            )
        )


def test_policy_activation_is_atomic_and_supports_validated_rollback() -> None:
    first = _generation("generation-001", model="model-a")
    second = _generation("generation-002", model="model-b")
    store = PolicyStore(first)

    assert store.activate(second) is second
    assert store.current.generation_id == "generation-002"
    assert store.rollback("generation-001") is first
    assert store.current.generation_id == "generation-001"

    conflicting = _generation("generation-002", model="model-c")
    with pytest.raises(ValueError, match="conflicts"):
        store.activate(conflicting)
    assert store.current is first


def test_policy_cannot_rollback_to_unvalidated_generation() -> None:
    store = PolicyStore(_generation("generation-001", model="model-a"))

    with pytest.raises(ValueError, match="has not been validated"):
        store.rollback("generation-999")


def test_rejected_runtime_load_retains_last_known_good_and_degrades_state() -> None:
    first = _generation("generation-001", model="model-a")
    store = PolicyStore(first)

    assert not store.load_document(
        {
            "generation_id": "generation-002",
            "profiles": {},
            "grants": {},
        }
    )
    assert store.current is first
    assert not store.load_healthy
    assert store.load_outcome == "rejected"

    assert store.rollback("generation-001") is first
    assert store.load_healthy
    assert store.load_outcome == "rollback"


def test_kill_switches_must_be_scoped_to_existing_grants() -> None:
    value = {
        "generation_id": "generation-001",
        "profiles": {
            "classroom.translation.economy": {
                "capability": "text_generation",
                "provider": "gemini",
                "model": "model-a",
            }
        },
        "grants": {
            "dali_classroom_server": {
                "products": ["classroom"],
                "profiles": ["classroom.translation.economy"],
                "capabilities": ["text_generation"],
                "disabled_products": ["interprete"],
            }
        },
    }

    with pytest.raises(ValidationError, match="disabled products must be granted"):
        PolicyGenerationDocument.model_validate(value)


def test_evaluation_profiles_require_a_dedicated_evaluator_workload() -> None:
    value = {
        "generation_id": "generation-001",
        "profiles": {
            "shared.evaluation.text": {
                "capability": "text_generation",
                "provider": "gemini",
                "model": "evaluation-model",
                "usage": "evaluation",
            }
        },
        "grants": {
            "dali_classroom_server": {
                "products": ["classroom"],
                "profiles": ["shared.evaluation.text"],
                "capabilities": ["text_generation"],
            }
        },
    }
    with pytest.raises(ValidationError, match="dedicated evaluator"):
        PolicyGenerationDocument.model_validate(value)

    value["grants"] = {
        "quality_evaluator": {
            "workload_type": "evaluator",
            "products": ["evaluation"],
            "profiles": ["shared.evaluation.text"],
            "capabilities": ["text_generation"],
        }
    }
    document = PolicyGenerationDocument.model_validate(value)
    assert document.grants["quality_evaluator"].workload_type == "evaluator"


def test_concurrent_policy_reads_observe_only_complete_generations() -> None:
    first = _generation("generation-001", model="model-a")
    second = _generation("generation-002", model="model-b")
    expected_models = {
        first.generation_id: "model-a",
        second.generation_id: "model-b",
    }
    store = PolicyStore(first)
    failures: list[str] = []

    def activate_repeatedly() -> None:
        for _ in range(500):
            store.activate(second)
            store.activate(first)

    def read_repeatedly() -> None:
        for _ in range(2_000):
            current = store.current
            model = current.profiles["classroom.translation.economy"].model
            if model != expected_models[current.generation_id]:
                failures.append(f"{current.generation_id}:{model}")

    threads = [Thread(target=activate_repeatedly)] + [
        Thread(target=read_repeatedly) for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
