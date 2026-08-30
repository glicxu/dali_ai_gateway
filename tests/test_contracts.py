from __future__ import annotations

import json
from pathlib import Path

from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_openapi_is_31_and_contains_public_http_operations() -> None:
    value = create_app().openapi()
    assert value["openapi"].startswith("3.1")
    assert "/ai/v1/text/generations" in value["paths"]
    assert "/ai/v1/audio/transcriptions" in value["paths"]


def test_realtime_schemas_and_examples_parse() -> None:
    for path in (ROOT / "contracts").rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
