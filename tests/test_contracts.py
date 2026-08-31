from __future__ import annotations

import json
from pathlib import Path

from app.core.measurement import UsageMeasurementEnvelope
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
