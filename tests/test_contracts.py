from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from app.core.measurement import UsageMeasurementEnvelope
from app.models import RealtimeV2TranslationStart
from app.main import create_app
from scripts.export_openapi import rendered_measurement_schema


ROOT = Path(__file__).resolve().parents[1]


def test_openapi_is_31_and_contains_public_http_operations() -> None:
    value = create_app().openapi()
    assert value["openapi"].startswith("3.1")
    assert "/ai/v1/text/generations" in value["paths"]
    assert "/ai/v1/audio/transcriptions" in value["paths"]
    assert "/ai/v1/audio/speech" in value["paths"]


def test_realtime_schemas_and_examples_parse() -> None:
    for path in (ROOT / "contracts").rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))


def test_usage_measurement_schema_matches_model_and_example() -> None:
    schema_path = ROOT / "contracts" / "schemas" / "usage-measurement-v1.schema.json"
    assert schema_path.read_text(encoding="utf-8") == rendered_measurement_schema()
    example_path = ROOT / "contracts" / "examples" / "usage-measurement-v1.json"
    UsageMeasurementEnvelope.model_validate_json(
        example_path.read_text(encoding="utf-8")
    )


def test_windowed_alternate_v2_example_matches_model() -> None:
    example_path = (
        ROOT
        / "contracts"
        / "examples"
        / "realtime-translation-v2-windowed-alternate-session-start.json"
    )
    value = RealtimeV2TranslationStart.model_validate_json(
        example_path.read_text(encoding="utf-8")
    )
    assert value.policy == "windowed_alternate"
    assert value.window_seconds == 120
    assert value.outputs == [
        "source_transcript",
        "target_transcript",
        "translated_audio",
    ]


def test_v2_server_schema_declares_audio_completion_event() -> None:
    schema = json.loads(
        (
            ROOT / "contracts" / "schemas" / "realtime-server-event-v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    event_types = {
        branch["properties"]["type"].get("const")
        for branch in schema["oneOf"]
        if "const" in branch["properties"]["type"]
    }
    assert "translation.audio.final" in event_types
    assert "session.rotation_required" in event_types


def test_v2_server_event_examples_validate_against_schema() -> None:
    schema = json.loads(
        (
            ROOT / "contracts" / "schemas" / "realtime-server-event-v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    base = {
        "session_id": "1f6f1f8f-6b2d-4bb8-9a1d-1f8c9a9f4f5e",
        "window_id": "w-1",
        "lane_id": "primary",
        "provider_ref": "dali_chat.interpret.openai",
        "sequence": 1,
    }
    events = [
        base
        | {
            "type": "session.ready",
            "request_id": base["session_id"],
            "profile": base["provider_ref"],
        },
        base | {"type": "audio.accepted", "accepted_sequence": 1},
        base | {"type": "translation.delta", "text": "hello"},
        base | {"type": "translation.audio.delta", "audio": "AQI="},
        base | {"type": "translation.audio.final"},
        base | {"type": "usage.final", "duration_ms": 1000, "accepted_input_chunks": 1},
        base
        | {
            "type": "session.rotation_required",
            "deadline_ms": 1000,
            "accepted_sequence": 1,
        },
        base
        | {
            "type": "session.closed",
            "disposition": "cancelled",
            "accepted_sequence": 1,
            "output_sequence": 1,
        },
        base
        | {
            "type": "provider.switched",
            "from_provider": "dali_chat.interpret.openai",
            "to_provider": "dali_chat.interpret.gemini",
            "reason": "scheduled_alternate",
        },
        {
            "type": "error",
            "window_id": "w-1",
            "sequence": 1,
            "error": {"code": "provider_failed", "retryable": True},
        },
    ]
    for event in events:
        errors = list(validator.iter_errors(event))
        assert not errors, errors

    early_error = {
        "type": "error",
        "error": {"code": "request_invalid", "retryable": False},
    }
    assert not list(validator.iter_errors(early_error))


def test_v2_client_session_start_accepts_outputs_and_rejects_unknown_fields() -> None:
    schema = json.loads(
        (
            ROOT / "contracts" / "schemas" / "realtime-client-event-v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    event = {
        "type": "session.start",
        "request_id": "1f6f1f8f-6b2d-4bb8-9a1d-1f8c9a9f4f5e",
        "product": "dali_chat",
        "profile": "dali_chat.interpret.openai",
        "target_language": "zh",
        "policy": "windowed_alternate",
        "window_seconds": 120,
        "outputs": ["source_transcript", "target_transcript", "translated_audio"],
    }
    assert not list(validator.iter_errors(event))
    assert list(validator.iter_errors(event | {"prompt": "must not cross the gateway"}))


def test_v2_client_session_start_example_validates_against_schema() -> None:
    schema = json.loads(
        (
            ROOT / "contracts" / "schemas" / "realtime-client-event-v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    example = json.loads(
        (
            ROOT
            / "contracts"
            / "examples"
            / "realtime-translation-v2-session-start.json"
        ).read_text(encoding="utf-8")
    )
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            example
        )
    )
    assert not errors, errors
