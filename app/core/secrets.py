from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text

from app.core.config import Settings


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ProviderCredentials:
    openai: str | None
    gemini: str | None


def resolve_provider_credentials(settings: Settings) -> ProviderCredentials:
    """Resolve provider credentials without exposing them to logs or errors."""
    if settings.credential_database_url is None:
        return ProviderCredentials(
            openai=_secret_value(settings.openai_api_key),
            gemini=_secret_value(settings.gemini_api_key),
        )

    engine = create_engine(
        settings.credential_database_url.get_secret_value(),
        pool_pre_ping=True,
    )
    try:
        return _resolve_from_database(engine, settings)
    finally:
        engine.dispose()


def _resolve_from_database(
    engine: Engine,
    settings: Settings,
) -> ProviderCredentials:
    schema = _validated_identifier(settings.credential_schema)
    table = _validated_identifier(settings.credential_table)
    query = text(
        f"SELECT access_id, JSON_UNQUOTE(credential) AS credential "
        f"FROM `{schema}`.`{table}` "
        "WHERE access_id IN (:openai_access_id, :gemini_access_id)"
    )
    with engine.connect() as connection:
        rows = connection.execute(
            query,
            {
                "openai_access_id": settings.openai_credential_access_id,
                "gemini_access_id": settings.gemini_credential_access_id,
            },
        ).mappings()
        values = {str(row["access_id"]): row["credential"] for row in rows}
    return ProviderCredentials(
        openai=_credential_value(
            values.get(settings.openai_credential_access_id),
            field="OPENAI_API_KEY",
        ),
        gemini=_credential_value(
            values.get(settings.gemini_credential_access_id),
            field="GEMINI_API_KEY",
        ),
    )


def _validated_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("credential database identifier is invalid")
    return value


def _secret_value(value) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value.get_secret_value())


def _nonempty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _credential_value(value: object, *, field: str) -> str | None:
    """Resolve plain or JSON-wrapped credentials without exposing their value."""
    current = value
    for _ in range(3):
        if isinstance(current, dict):
            current = current.get(field)
            continue
        text_value = _nonempty_string(current)
        if text_value is None:
            return None
        try:
            decoded = json.loads(text_value)
        except (TypeError, ValueError):
            return text_value
        if isinstance(decoded, (str, dict)):
            current = decoded
            continue
        return None
    return _nonempty_string(current)
