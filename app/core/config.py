from __future__ import annotations

import json
from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_PROFILES: dict[str, dict[str, str]] = {
    "classroom.translation.economy": {
        "capability": "text_generation",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
    },
    "classroom.summary.economy": {
        "capability": "text_generation",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
    },
    "classroom.transcription.economy": {
        "capability": "audio_transcription",
        "provider": "openai",
        "model": "gpt-4o-mini-transcribe",
    },
    "classroom.transcription.live": {
        "capability": "realtime_transcription",
        "provider": "gemini",
        "model": "gemini-3.5-transcribe-live",
    },
    "classroom.translation.live": {
        "capability": "realtime_translation",
        "provider": "openai",
        "model": "gpt-realtime-translate",
    },
    "shared.text.gemini": {
        "capability": "text_generation",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
    },
    "shared.text.ollama": {
        "capability": "text_generation",
        "provider": "ollama",
        "model": "mistral",
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AI_GATEWAY_",
        extra="ignore",
    )

    env: str = "development"
    credential_database_url: SecretStr | None = None
    credential_schema: str = "secret"
    credential_table: str = "key_store"
    openai_credential_access_id: str = "openai"
    gemini_credential_access_id: str = "gemini"
    service_tokens_json: SecretStr = SecretStr("{}")
    caller_limits_json: str = '{"dali_classroom_server":1}'
    caller_products_json: str = '{"dali_classroom_server":["classroom"]}'
    model_profiles_json: str = "{}"
    openai_base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")
    openai_api_key: SecretStr | None = None
    gemini_base_url: AnyHttpUrl = AnyHttpUrl(
        "https://generativelanguage.googleapis.com/v1beta"
    )
    gemini_api_key: SecretStr | None = None
    ollama_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:11434")
    request_timeout_seconds: float = Field(default=60, gt=0, le=120)
    max_audio_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)

    @field_validator(
        "caller_limits_json", "caller_products_json", "model_profiles_json"
    )
    @classmethod
    def _valid_json_object(cls, value: str) -> str:
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError("configuration must be a JSON object")
        return value

    def service_tokens(self) -> dict[str, str]:
        value = json.loads(self.service_tokens_json.get_secret_value())
        if not isinstance(value, dict):
            raise ValueError("AI_GATEWAY_SERVICE_TOKENS_JSON must be an object")
        return {
            str(caller): str(token)
            for caller, token in value.items()
            if str(caller) and str(token)
        }

    def caller_limits(self) -> dict[str, int]:
        value = json.loads(self.caller_limits_json)
        return {str(caller): max(1, int(limit)) for caller, limit in value.items()}

    def caller_products(self) -> dict[str, frozenset[str]]:
        value = json.loads(self.caller_products_json)
        return {
            str(caller): frozenset(str(product) for product in products)
            for caller, products in value.items()
            if isinstance(products, list)
        }

    def model_profiles(self) -> dict[str, dict[str, str]]:
        configured = json.loads(self.model_profiles_json)
        merged = {name: dict(value) for name, value in DEFAULT_PROFILES.items()}
        for name, value in configured.items():
            if not isinstance(value, dict):
                raise ValueError(f"model profile {name!r} must be an object")
            merged[str(name)] = {str(key): str(item) for key, item in value.items()}
        return merged


@lru_cache
def get_settings() -> Settings:
    return Settings()
