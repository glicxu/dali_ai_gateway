from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_source_contains_no_credential_literals_or_content_logging() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "app").rglob("*.py")
    )
    forbidden = (
        "sk-" + "proj-",
        "AIza" + "Sy",
        'logging.info("User input',
        'logging.info(f"Prompt:',
        "Raw LLM response",
    )
    for value in forbidden:
        assert value not in source
