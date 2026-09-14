from __future__ import annotations

import pytest

from app.core.config import DEFAULT_WORKLOAD_GRANTS, Settings
from app.core.errors import GatewayError
from app.providers.registry import ProviderRegistry


def test_dalibible_has_one_disabled_bounded_profile() -> None:
    settings = Settings()
    generation = settings.policy_generation()
    grant = generation.grants["dali_bible_server_ai"]
    profile = generation.profiles["dalibible.study.standard"]

    assert not grant.enabled
    assert grant.products == frozenset({"dalibible"})
    assert grant.profiles == frozenset({"dalibible.study.standard"})
    assert grant.capabilities == frozenset({"text_generation"})
    assert DEFAULT_WORKLOAD_GRANTS["dali_bible_server_ai"]["enabled"] is False
    assert profile.capability == "text_generation"
    assert profile.capacity_pool == "dalibible"
    assert profile.max_input_bytes == 65536
    assert not profile.required_for_readiness
    assert settings.caller_limits()["dali_bible_server_ai"] == 1


def test_disabled_dalibible_grant_denies_execution_before_provider_work() -> None:
    registry = ProviderRegistry(Settings(), providers={"openai": object()})

    with pytest.raises(GatewayError) as captured:
        registry.resolve(
            caller="dali_bible_server_ai",
            product="dalibible",
            profile_name="dalibible.study.standard",
            capability="text_generation",
        )

    assert captured.value.code == "ai_gateway_profile_not_allowed"


@pytest.mark.parametrize(
    ("product", "profile"),
    [
        ("classroom", "dalibible.study.standard"),
        ("dalibible", "classroom.translation.economy"),
    ],
)
def test_dalibible_grant_cannot_cross_product_or_profile_namespace(
    product: str, profile: str
) -> None:
    settings = Settings()
    grants = settings.policy_generation().grants
    assert product not in grants["dali_bible_server_ai"].products or profile not in grants[
        "dali_bible_server_ai"
    ].profiles
