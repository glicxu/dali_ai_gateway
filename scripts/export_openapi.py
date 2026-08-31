from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.measurement import UsageMeasurementEnvelope
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts" / "openapi" / "ai-gateway-v1.json"
MEASUREMENT_TARGET = ROOT / "contracts" / "schemas" / "usage-measurement-v1.schema.json"


def rendered() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def rendered_measurement_schema() -> str:
    value = UsageMeasurementEnvelope.model_json_schema(mode="serialization")
    value["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    value["$id"] = "https://schemas.dalifin.com/ai/usage-measurement-v1.schema.json"
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    value = rendered()
    measurement_value = rendered_measurement_schema()
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != value:
            raise SystemExit("OpenAPI artifact is out of date")
        if (
            not MEASUREMENT_TARGET.exists()
            or MEASUREMENT_TARGET.read_text(encoding="utf-8") != measurement_value
        ):
            raise SystemExit("usage measurement schema artifact is out of date")
        return
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(value, encoding="utf-8", newline="\n")
    MEASUREMENT_TARGET.parent.mkdir(parents=True, exist_ok=True)
    MEASUREMENT_TARGET.write_text(
        measurement_value,
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
