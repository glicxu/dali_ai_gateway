from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "contracts" / "openapi" / "ai-gateway-v1.json"


def rendered() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    value = rendered()
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != value:
            raise SystemExit("OpenAPI artifact is out of date")
        return
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(value, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
