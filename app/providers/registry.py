from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from app.core.config import Settings
from app.core.errors import PROFILE_NOT_ALLOWED, PROVIDER_NOT_CONFIGURED
from app.core.secrets import ProviderCredentials, resolve_provider_credentials
from app.providers.base import (
    AudioTranscriptionProvider,
    RealtimeTranscriptionProvider,
    RealtimeTranslationProvider,
    TextProvider,
)
from app.providers.gemini import GeminiProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai import OpenAIProvider


Capability = Literal[
    "text_generation",
    "audio_transcription",
    "realtime_transcription",
    "realtime_translation",
]


@dataclass(frozen=True, slots=True)
class ModelProfile:
    name: str
    capability: Capability
    provider: str
    model: str


class ProviderRegistry:
    def __init__(
        self,
        settings: Settings,
        *,
        providers: dict[str, object] | None = None,
    ) -> None:
        self._caller_products = settings.caller_products()
        self._profiles = _profiles(settings.model_profiles())
        self._providers = providers if providers is not None else _providers(settings)

    @property
    def configured(self) -> bool:
        return bool(self._providers)

    def resolve(
        self,
        *,
        caller: str,
        product: str,
        profile_name: str,
        capability: Capability,
    ) -> tuple[ModelProfile, object]:
        allowed_products = self._caller_products.get(caller, frozenset())
        if product not in allowed_products:
            raise PROFILE_NOT_ALLOWED
        profile = self._profiles.get(profile_name)
        if (
            profile is None
            or profile.capability != capability
            or not (
                profile.name.startswith(f"{product}.")
                or profile.name.startswith("shared.")
            )
        ):
            raise PROFILE_NOT_ALLOWED
        provider = self._providers.get(profile.provider)
        if provider is None:
            raise PROVIDER_NOT_CONFIGURED
        return profile, provider

    def text_provider(self, provider: object) -> TextProvider:
        if not callable(getattr(provider, "generate", None)):
            raise PROVIDER_NOT_CONFIGURED
        return cast(TextProvider, provider)

    def transcription_provider(self, provider: object) -> AudioTranscriptionProvider:
        if not callable(getattr(provider, "transcribe", None)):
            raise PROVIDER_NOT_CONFIGURED
        return cast(AudioTranscriptionProvider, provider)

    def realtime_provider(self, provider: object) -> RealtimeTranscriptionProvider:
        if not callable(getattr(provider, "open_realtime", None)):
            raise PROVIDER_NOT_CONFIGURED
        return cast(RealtimeTranscriptionProvider, provider)

    def realtime_translation_provider(
        self, provider: object
    ) -> RealtimeTranslationProvider:
        if not callable(getattr(provider, "open_realtime_translation", None)):
            raise PROVIDER_NOT_CONFIGURED
        return cast(RealtimeTranslationProvider, provider)

    async def close(self) -> None:
        for provider in self._providers.values():
            close = getattr(provider, "close", None)
            if callable(close):
                await close()


def _profiles(values: dict[str, dict[str, str]]) -> dict[str, ModelProfile]:
    profiles: dict[str, ModelProfile] = {}
    capabilities = {
        "text_generation",
        "audio_transcription",
        "realtime_transcription",
        "realtime_translation",
    }
    for name, value in values.items():
        capability = value.get("capability", "")
        provider = value.get("provider", "")
        model = value.get("model", "")
        if capability not in capabilities or not provider or not model:
            raise ValueError(f"invalid model profile {name!r}")
        profiles[name] = ModelProfile(
            name=name,
            capability=cast(Capability, capability),
            provider=provider,
            model=model,
        )
    return profiles


def _providers(settings: Settings) -> dict[str, object]:
    credentials = resolve_provider_credentials(settings)
    return _providers_with_credentials(settings, credentials)


def _providers_with_credentials(
    settings: Settings,
    credentials: ProviderCredentials,
) -> dict[str, object]:
    providers: dict[str, object] = {
        "ollama": OllamaProvider(
            base_url=str(settings.ollama_base_url),
            timeout_seconds=settings.request_timeout_seconds,
        )
    }
    if credentials.openai:
        providers["openai"] = OpenAIProvider(
            api_key=credentials.openai,
            base_url=str(settings.openai_base_url),
            timeout_seconds=settings.request_timeout_seconds,
        )
    if credentials.gemini:
        providers["gemini"] = GeminiProvider(
            api_key=credentials.gemini,
            base_url=str(settings.gemini_base_url),
            timeout_seconds=settings.request_timeout_seconds,
            realtime_session_max_seconds=(settings.gemini_realtime_session_max_seconds),
        )
    return providers
