from __future__ import annotations

from unittest.mock import Mock, patch

from pydantic import SecretStr

from app.core.config import Settings
from app.core.secrets import resolve_provider_credentials


def test_environment_credentials_are_local_fallback() -> None:
    credentials = resolve_provider_credentials(
        Settings(
            openai_api_key=SecretStr("openai-local"),
            gemini_api_key=SecretStr("gemini-local"),
        )
    )

    assert credentials.openai == "openai-local"
    assert credentials.gemini == "gemini-local"


def test_database_credentials_take_precedence_without_secret_output() -> None:
    settings = Settings(
        credential_database_url=SecretStr("mysql+pymysql://user:password@db/app"),
        openai_api_key=SecretStr("openai-local"),
        gemini_api_key=SecretStr("gemini-local"),
    )
    connection = Mock()
    connection.execute.return_value.mappings.return_value = [
        {"access_id": "openai", "credential": "openai-database"},
        {"access_id": "gemini", "credential": "gemini-database"},
    ]
    context = Mock()
    context.__enter__ = Mock(return_value=connection)
    context.__exit__ = Mock(return_value=False)
    engine = Mock()
    engine.connect.return_value = context

    with patch("app.core.secrets.create_engine", return_value=engine):
        credentials = resolve_provider_credentials(settings)

    assert credentials.openai == "openai-database"
    assert credentials.gemini == "gemini-database"
    engine.dispose.assert_called_once_with()
    query_text = str(connection.execute.call_args.args[0])
    assert "credential" in query_text
    assert "openai-database" not in query_text
    assert "gemini-database" not in query_text


def test_database_json_object_credentials_extract_provider_fields() -> None:
    settings = Settings(
        credential_database_url=SecretStr("mysql+pymysql://user:password@db/app"),
    )
    connection = Mock()
    connection.execute.return_value.mappings.return_value = [
        {
            "access_id": "openai",
            "credential": '{"OPENAI_API_KEY":"openai-from-json"}',
        },
        {
            "access_id": "gemini",
            "credential": {"GEMINI_API_KEY": "gemini-from-json"},
        },
    ]
    context = Mock()
    context.__enter__ = Mock(return_value=connection)
    context.__exit__ = Mock(return_value=False)
    engine = Mock()
    engine.connect.return_value = context

    with patch("app.core.secrets.create_engine", return_value=engine):
        credentials = resolve_provider_credentials(settings)

    assert credentials.openai == "openai-from-json"
    assert credentials.gemini == "gemini-from-json"


def test_database_double_encoded_json_credential_is_supported() -> None:
    settings = Settings(
        credential_database_url=SecretStr("mysql+pymysql://user:password@db/app"),
    )
    connection = Mock()
    connection.execute.return_value.mappings.return_value = [
        {
            "access_id": "openai",
            "credential": '"{\\"OPENAI_API_KEY\\":\\"openai-double-json\\"}"',
        },
    ]
    context = Mock()
    context.__enter__ = Mock(return_value=connection)
    context.__exit__ = Mock(return_value=False)
    engine = Mock()
    engine.connect.return_value = context

    with patch("app.core.secrets.create_engine", return_value=engine):
        credentials = resolve_provider_credentials(settings)

    assert credentials.openai == "openai-double-json"
    assert credentials.gemini is None


def test_database_json_credential_without_expected_field_is_rejected() -> None:
    settings = Settings(
        credential_database_url=SecretStr("mysql+pymysql://user:password@db/app"),
    )
    connection = Mock()
    connection.execute.return_value.mappings.return_value = [
        {"access_id": "openai", "credential": '{"OTHER_KEY":"not-openai"}'},
    ]
    context = Mock()
    context.__enter__ = Mock(return_value=connection)
    context.__exit__ = Mock(return_value=False)
    engine = Mock()
    engine.connect.return_value = context

    with patch("app.core.secrets.create_engine", return_value=engine):
        credentials = resolve_provider_credentials(settings)

    assert credentials.openai is None


def test_invalid_database_identifier_is_rejected() -> None:
    settings = Settings(
        credential_database_url=SecretStr("mysql+pymysql://user:password@db/app"),
        credential_schema="secret; DROP DATABASE secret",
    )
    engine = Mock()

    with patch("app.core.secrets.create_engine", return_value=engine):
        try:
            resolve_provider_credentials(settings)
        except ValueError as error:
            assert str(error) == "credential database identifier is invalid"
        else:
            raise AssertionError("invalid identifier was accepted")

    engine.dispose.assert_called_once_with()
