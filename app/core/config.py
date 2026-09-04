from __future__ import annotations

import json
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.policy import PolicyGeneration, PolicyGenerationDocument


DEFAULT_PROFILES: dict[str, dict[str, object]] = {
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
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
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
        "required_for_readiness": False,
    },
    "shared.text.ollama": {
        "capability": "text_generation",
        "provider": "ollama",
        "model": "mistral",
        "required_for_readiness": False,
    },
    "dali_chat.text.openai": {
        "capability": "text_generation",
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "required_for_readiness": False,
    },
    "dali_chat.text.openai.gpt-5-6-sol": {
        "capability": "text_generation",
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "required_for_readiness": False,
    },
    "dali_chat.text.openai.gpt-5-6-terra": {
        "capability": "text_generation",
        "provider": "openai",
        "model": "gpt-5.6-terra",
        "required_for_readiness": False,
    },
    "dali_chat.text.openai.gpt-5-6-luna": {
        "capability": "text_generation",
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "required_for_readiness": False,
    },
    "dali_chat.text.gemini": {
        "capability": "text_generation",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "required_for_readiness": False,
    },
    "dali_chat.text.ollama": {
        "capability": "text_generation",
        "provider": "ollama",
        "model": "mistral",
        "required_for_readiness": False,
    },
    "dali_chat.transcription.openai": {
        "capability": "audio_transcription",
        "provider": "openai",
        "model": "gpt-4o-mini-transcribe",
        "required_for_readiness": False,
    },
    "dali_chat.transcription.gemini": {
        "capability": "audio_transcription",
        "provider": "gemini",
        "model": "gemini-3.5-transcribe",
        "required_for_readiness": False,
    },
    "dali_chat.transcription.stream.openai": {
        "capability": "realtime_transcription",
        "provider": "openai",
        "model": "gpt-4o-mini-transcribe",
        "required_for_readiness": False,
    },
    "dali_chat.transcription.stream.gemini": {
        "capability": "realtime_transcription",
        "provider": "gemini",
        "model": "gemini-3.5-transcribe-live",
        "required_for_readiness": False,
    },
    "dali_chat.interpret.openai": {
        "capability": "realtime_translation",
        "provider": "openai",
        "model": "gpt-realtime-translate",
        "required_for_readiness": False,
    },
    "dali_chat.interpret.gemini": {
        "capability": "realtime_translation",
        "provider": "gemini",
        "model": "gemini-3.5-live-translate-preview",
        "required_for_readiness": False,
    },
    "dali_chat.translation.openai": {
        "capability": "text_generation",
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "required_for_readiness": False,
    },
    "dali_chat.translation.gemini": {
        "capability": "text_generation",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "required_for_readiness": False,
    },
    "dali_chat.speech.openai": {
        "capability": "speech_synthesis",
        "provider": "openai",
        "model": "gpt-4o-mini-tts",
        "required_for_readiness": False,
    },
    "dali_chat.speech.gemini": {
        "capability": "speech_synthesis",
        "provider": "gemini",
        "model": "gemini-3.1-flash-tts-preview",
        "required_for_readiness": False,
    },
    "dali_chat.image.openai": {
        "capability": "image_analysis",
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "required_for_readiness": False,
    },
    "dali_chat.image.gemini": {
        "capability": "image_analysis",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "required_for_readiness": False,
    },
    "dali_chat.video.gemini": {
        "capability": "video_analysis",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "required_for_readiness": False,
    },
    "interprete.live_summary": {
        "capacity_pool": "interprete",
        "capability": "text_generation",
        "provider": "openai",
        "model": "gpt-5-mini",
        "required_for_readiness": False,
        "max_input_bytes": 250000,
    },
    "interprete.translation.text": {
        "capacity_pool": "interprete",
        "capability": "text_generation",
        "provider": "openai",
        "model": "gpt-5-mini",
        "required_for_readiness": False,
        "max_input_bytes": 250000,
    },
    "interprete.transcription.batch": {
        "capacity_pool": "interprete",
        "capability": "audio_transcription",
        "provider": "openai",
        "model": "gpt-4o-mini-transcribe",
        "required_for_readiness": False,
        "max_audio_bytes": 10485760,
    },
    "interprete.transcription.realtime": {
        "capacity_pool": "interprete_realtime",
        "capability": "realtime_transcription",
        "provider": "openai",
        "model": "gpt-4o-mini-transcribe",
        "required_for_readiness": False,
        "max_chunk_bytes": 262144,
        "max_session_seconds": 600,
        "max_accepted_input_bytes": 62914560,
        "max_provider_buffer_bytes": 262144,
        "max_outbound_events": 1,
    },
    "interprete.translation.realtime": {
        "capacity_pool": "interprete_realtime",
        "capability": "realtime_translation",
        "provider": "openai",
        "model": "gpt-realtime-translate",
        "required_for_readiness": False,
        "max_chunk_bytes": 262144,
        "max_session_seconds": 600,
        "max_accepted_input_bytes": 62914560,
        "max_provider_buffer_bytes": 262144,
        "max_outbound_events": 1,
    },
    "interprete.speech.standard": {
        "capacity_pool": "interprete",
        "capability": "speech_synthesis",
        "provider": "openai",
        "model": "gpt-4o-mini-tts",
        "required_for_readiness": False,
        "max_input_bytes": 16384,
        "voice_routes": {"neutral": "alloy", "warm": "coral"},
    },
    "scribe.transcription.live": {
        "capacity_pool": "scribe_realtime",
        "capability": "realtime_transcription",
        "provider": "gemini",
        "model": "gemini-3.5-transcribe-live",
        "required_for_readiness": False,
        "max_chunk_bytes": 262144,
        "max_session_seconds": 3600,
        "max_accepted_input_bytes": 67108864,
        "max_provider_buffer_bytes": 262144,
        "max_outbound_events": 1,
    },
    "scribe.transcription.live.openai": {
        "capacity_pool": "scribe_realtime",
        "capability": "realtime_transcription",
        "provider": "openai",
        "model": "gpt-4o-mini-transcribe",
        "required_for_readiness": False,
        "max_chunk_bytes": 262144,
        "max_session_seconds": 3600,
        "max_accepted_input_bytes": 67108864,
        "max_provider_buffer_bytes": 262144,
        "max_outbound_events": 1,
    },
    "scribe.summary.text": {
        "capacity_pool": "scribe",
        "capability": "text_generation",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "required_for_readiness": False,
        "max_input_bytes": 250000,
    },
}

DEFAULT_WORKLOAD_GRANTS: dict[str, dict[str, object]] = {
    "dali_classroom_server": {
        "products": ["classroom"],
        "profiles": [
            "classroom.translation.economy",
            "classroom.summary.economy",
            "classroom.transcription.economy",
            "classroom.transcription.live",
            "classroom.translation.live",
        ],
        "capabilities": [
            "text_generation",
            "audio_transcription",
            "realtime_transcription",
            "realtime_translation",
        ],
    },
    "dali_chat_server": {
        "enabled": False,
        "products": ["dali_chat"],
        "profiles": [
            "dali_chat.text.openai",
            "dali_chat.text.openai.gpt-5-6-sol",
            "dali_chat.text.openai.gpt-5-6-terra",
            "dali_chat.text.openai.gpt-5-6-luna",
            "dali_chat.text.gemini",
            "dali_chat.text.ollama",
            "dali_chat.transcription.openai",
            "dali_chat.transcription.gemini",
            "dali_chat.transcription.stream.openai",
            "dali_chat.transcription.stream.gemini",
            "dali_chat.interpret.openai",
            "dali_chat.interpret.gemini",
            "dali_chat.translation.openai",
            "dali_chat.translation.gemini",
            "dali_chat.speech.openai",
            "dali_chat.speech.gemini",
            "dali_chat.image.openai",
            "dali_chat.image.gemini",
            "dali_chat.video.gemini",
        ],
        "capabilities": [
            "text_generation",
            "audio_transcription",
            "realtime_transcription",
            "realtime_translation",
            "speech_synthesis",
            "image_analysis",
            "video_analysis",
        ],
    },
    "interpreter_server_ai": {
        "enabled": False,
        "products": ["interprete"],
        "profiles": [
            "interprete.live_summary",
            "interprete.translation.text",
            "interprete.transcription.batch",
            "interprete.transcription.realtime",
            "interprete.translation.realtime",
            "interprete.speech.standard",
        ],
        "capabilities": [
            "text_generation",
            "audio_transcription",
            "realtime_transcription",
            "realtime_translation",
            "speech_synthesis",
        ],
    },
    "dali_scribe_server_ai": {
        "enabled": False,
        "products": ["scribe"],
        "profiles": [
            "scribe.transcription.live",
            "scribe.transcription.live.openai",
            "scribe.summary.text",
        ],
        "capabilities": [
            "realtime_transcription",
            "text_generation",
        ],
    },
}
DEFAULT_WORKLOAD_GRANTS_JSON = json.dumps(
    DEFAULT_WORKLOAD_GRANTS, separators=(",", ":")
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AI_GATEWAY_",
        env_ignore_empty=True,
        extra="ignore",
    )

    env: str = "development"
    credential_database_url: SecretStr | None = None
    credential_schema: str = "secret"
    credential_table: str = "key_store"
    openai_credential_access_id: str = "openai"
    gemini_credential_access_id: str = "gemini"
    service_tokens_json: SecretStr = SecretStr("{}")
    caller_limits_json: str = (
        '{"dali_classroom_server":1,"dali_chat_server":2,"interpreter_server_ai":2,"dali_scribe_server_ai":2}'
    )
    model_profiles_json: str = "{}"
    policy_generation_id: str = "builtin-classroom-v1"
    workload_grants_json: str = DEFAULT_WORKLOAD_GRANTS_JSON
    scribe_ai_enabled: bool = False
    legacy_auth_workload_ids_json: str = '["dali_classroom_server"]'
    platform_workload_auth_enabled: bool = False
    platform_workload_auth_required_for_readiness: bool = False
    platform_workload_issuer: str | None = None
    platform_workload_audience: str | None = None
    platform_workload_required_scope: str | None = None
    platform_workload_jwks_url: AnyHttpUrl | None = None
    platform_workload_ids_json: str = "[]"
    platform_workload_max_token_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    platform_workload_clock_skew_seconds: int = Field(default=30, ge=0, le=120)
    platform_jwks_timeout_seconds: float = Field(default=3, gt=0, le=10)
    platform_jwks_refresh_interval_seconds: float = Field(default=60, ge=5, le=600)
    platform_jwks_max_staleness_seconds: float = Field(default=300, ge=30, le=3600)
    platform_jwks_unknown_key_cooldown_seconds: float = Field(default=5, ge=1, le=60)
    usage_sqs_queue_url: str | None = None
    usage_sqs_region: str | None = None
    usage_delivery_required: bool = False
    usage_delivery_max_attempts: int = Field(default=3, ge=1, le=5)
    usage_delivery_retry_delay_seconds: float = Field(default=0.25, ge=0, le=10)
    admission_lease_ttl_seconds: float = Field(default=300, ge=30, le=1800)
    admission_dynamodb_table: str | None = None
    admission_dynamodb_region: str | None = None
    shared_admission_required: bool = False
    circuit_dynamodb_table: str | None = None
    circuit_dynamodb_region: str | None = None
    shared_circuit_required: bool = False
    execution_claim_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    shutdown_drain_seconds: float = Field(default=10, ge=0, le=60)
    provider_circuit_enabled: bool = False
    provider_circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    provider_circuit_open_seconds: float = Field(default=30, ge=1, le=300)
    provider_circuit_disabled_routes_json: str = "[]"
    openai_base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")
    openai_api_key: SecretStr | None = None
    gemini_base_url: AnyHttpUrl = AnyHttpUrl(
        "https://generativelanguage.googleapis.com/v1beta"
    )
    gemini_api_key: SecretStr | None = None
    ollama_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:11434")
    ollama_enabled: bool = False
    request_timeout_seconds: float = Field(default=60, gt=0, le=120)
    provider_probe_timeout_seconds: float = Field(default=3, gt=0, le=10)
    provider_probe_interval_seconds: float = Field(default=30, ge=5, le=300)
    provider_probe_max_staleness_seconds: float = Field(default=90, ge=10, le=900)
    provider_probe_max_concurrency: int = Field(default=4, ge=1, le=16)
    gemini_realtime_session_max_seconds: float = Field(
        default=9 * 60,
        gt=0,
        le=10 * 60,
    )
    realtime_hedge_buffer_seconds: int = Field(default=60, ge=5, le=60)
    max_audio_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)
    max_media_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)

    @model_validator(mode="after")
    def _probe_staleness_exceeds_refresh_window(self) -> Settings:
        minimum = (
            self.provider_probe_interval_seconds + self.provider_probe_timeout_seconds
        )
        if self.provider_probe_max_staleness_seconds <= minimum:
            raise ValueError(
                "provider probe maximum staleness must exceed refresh interval and timeout"
            )
        jwks_minimum = (
            self.platform_jwks_refresh_interval_seconds
            + self.platform_jwks_timeout_seconds
        )
        if self.platform_jwks_max_staleness_seconds <= jwks_minimum:
            raise ValueError(
                "JWKS maximum staleness must exceed refresh interval and timeout"
            )
        if self.platform_workload_auth_enabled and not all(
            (
                self.platform_workload_issuer,
                self.platform_workload_audience,
                self.platform_workload_required_scope,
                self.platform_workload_jwks_url,
                self.platform_workload_ids(),
            )
        ):
            raise ValueError(
                "enabled Platform workload authentication requires complete configuration"
            )
        if self.platform_workload_auth_enabled:
            issuer = urlsplit(str(self.platform_workload_issuer))
            if not issuer.scheme or not issuer.netloc:
                raise ValueError("Platform workload issuer must be an absolute URL")
            if self.env not in {"development", "test"} and (
                issuer.scheme != "https"
                or self.platform_workload_jwks_url is None
                or self.platform_workload_jwks_url.scheme != "https"
            ):
                raise ValueError(
                    "Platform workload issuer and JWKS must use HTTPS outside development"
                )
        if bool(self.usage_sqs_queue_url) != bool(self.usage_sqs_region):
            raise ValueError(
                "SQS usage sink URL and region must be configured together"
            )
        if self.usage_sqs_queue_url and (
            not self.usage_sqs_queue_url.startswith("https://")
            or self.usage_sqs_queue_url.endswith(".fifo")
        ):
            raise ValueError("usage sink requires an HTTPS SQS Standard queue URL")
        if self.usage_delivery_required and not self.usage_sqs_queue_url:
            raise ValueError("durable usage delivery is required but not configured")
        if bool(self.admission_dynamodb_table) != bool(self.admission_dynamodb_region):
            raise ValueError(
                "DynamoDB admission table and region must be configured together"
            )
        if self.shared_admission_required and not self.admission_dynamodb_table:
            raise ValueError("shared admission is required but not configured")
        if bool(self.circuit_dynamodb_table) != bool(self.circuit_dynamodb_region):
            raise ValueError(
                "DynamoDB circuit table and region must be configured together"
            )
        if self.shared_circuit_required and not self.circuit_dynamodb_table:
            raise ValueError("shared circuit is required but not configured")
        return self

    @field_validator(
        "caller_limits_json",
        "model_profiles_json",
        "workload_grants_json",
    )
    @classmethod
    def _valid_json_object(cls, value: str) -> str:
        decoded = _json_object(value)
        if not isinstance(decoded, dict):
            raise ValueError("configuration must be a JSON object")
        return value

    @field_validator(
        "legacy_auth_workload_ids_json",
        "platform_workload_ids_json",
        "provider_circuit_disabled_routes_json",
    )
    @classmethod
    def _valid_json_string_list(cls, value: str) -> str:
        _json_string_list(value)
        return value

    def service_tokens(self) -> dict[str, tuple[str, ...]]:
        value = _json_object(self.service_tokens_json.get_secret_value())
        tokens: dict[str, tuple[str, ...]] = {}
        for caller, configured in value.items():
            values = configured if isinstance(configured, list) else [configured]
            normalized = tuple(str(token) for token in values if str(token))
            if (
                not normalized
                or len(normalized) > 2
                or len(set(normalized)) != len(normalized)
            ):
                raise ValueError(
                    "each service identity requires one or two unique tokens"
                )
            tokens[str(caller)] = normalized
        return tokens

    def caller_limits(self) -> dict[str, int]:
        value = _json_object(self.caller_limits_json)
        return {str(caller): max(1, int(limit)) for caller, limit in value.items()}

    def provider_circuit_disabled_routes(self) -> frozenset[str]:
        value = _json_string_list(self.provider_circuit_disabled_routes_json)
        if len(value) != len(set(value)):
            raise ValueError("provider circuit disabled routes must be unique")
        return frozenset(value)

    def legacy_auth_workload_ids(self) -> frozenset[str]:
        return frozenset(_json_string_list(self.legacy_auth_workload_ids_json))

    def platform_workload_ids(self) -> frozenset[str]:
        return frozenset(_json_string_list(self.platform_workload_ids_json))

    def model_profiles(self) -> dict[str, dict[str, object]]:
        configured = _json_object(self.model_profiles_json)
        merged = {name: dict(value) for name, value in DEFAULT_PROFILES.items()}
        for name, value in configured.items():
            if not isinstance(value, dict):
                raise ValueError(f"model profile {name!r} must be an object")
            merged[str(name)] = {str(key): item for key, item in value.items()}
        return merged

    def policy_generation(self) -> PolicyGeneration:
        grants = _json_object(self.workload_grants_json)
        if self.scribe_ai_enabled and "dali_scribe_server_ai" in grants:
            scribe_grant = dict(grants["dali_scribe_server_ai"])  # type: ignore[arg-type]
            scribe_grant["enabled"] = True
            grants["dali_scribe_server_ai"] = scribe_grant
        document = PolicyGenerationDocument.model_validate(
            {
                "generation_id": self.policy_generation_id,
                "profiles": self.model_profiles(),
                "grants": grants,
            }
        )
        return PolicyGeneration.from_document(document)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _json_object(value: str) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        decoded: dict[str, object] = {}
        for key, item in pairs:
            if key in decoded:
                raise ValueError("configuration contains a duplicate key")
            decoded[key] = item
        return decoded

    decoded = json.loads(value, object_pairs_hook=reject_duplicates)
    if not isinstance(decoded, dict):
        raise ValueError("configuration must be a JSON object")
    return decoded


def _json_string_list(value: str) -> list[str]:
    decoded = json.loads(value)
    if not isinstance(decoded, list) or any(
        not isinstance(item, str) or not item for item in decoded
    ):
        raise ValueError("configuration must be a JSON string array")
    if len(decoded) > 128 or len(decoded) != len(set(decoded)):
        raise ValueError("configuration string array is invalid")
    return decoded
