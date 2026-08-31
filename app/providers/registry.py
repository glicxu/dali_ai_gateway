from __future__ import annotations

from typing import Mapping, cast

from app.core.config import Settings
from app.core.errors import PROFILE_NOT_ALLOWED, PROVIDER_NOT_CONFIGURED
from app.core.health import ProviderHealthMonitor, ProviderHealthStatus
from app.core.policy import Capability, ModelProfile, PolicyStore
from app.core.secrets import ProviderCredentials, resolve_provider_credentials
from app.providers.base import (
    AudioTranscriptionProvider,
    MediaAnalysisProvider,
    RealtimeTranscriptionProvider,
    RealtimeTranslationProvider,
    SpeechSynthesisProvider,
    TextProvider,
)
from app.providers.gemini import GeminiProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai import OpenAIProvider


class ProviderRegistry:
    def __init__(
        self,
        settings: Settings,
        *,
        providers: dict[str, object] | None = None,
        policy_store: PolicyStore | None = None,
    ) -> None:
        self._policy_store = policy_store or PolicyStore(settings.policy_generation())
        self._providers = providers if providers is not None else _providers(settings)
        self._health = ProviderHealthMonitor(
            self._providers,
            timeout_seconds=settings.provider_probe_timeout_seconds,
            interval_seconds=settings.provider_probe_interval_seconds,
            max_staleness_seconds=settings.provider_probe_max_staleness_seconds,
            max_concurrency=settings.provider_probe_max_concurrency,
        )

    @property
    def configured(self) -> bool:
        enabled_callers = frozenset(
            workload_id
            for workload_id, grant in self._policy_store.current.grants.items()
            if grant.enabled
        )
        return self.is_ready_for_workloads(enabled_callers)

    @property
    def policy_generation_id(self) -> str:
        return self._policy_store.current.generation_id

    def is_ready_for_callers(self, callers: frozenset[str]) -> bool:
        """Compatibility alias for the original caller terminology."""
        return self.is_ready_for_workloads(callers)

    def is_ready_for_workloads(self, workload_ids: frozenset[str]) -> bool:
        policy, load_healthy, _ = self._policy_store.snapshot()
        if not load_healthy:
            return False
        enabled_grants = {
            workload_id for workload_id, grant in policy.grants.items() if grant.enabled
        }
        if not workload_ids or enabled_grants != set(workload_ids):
            return False
        active_profile_names = {
            profile_name
            for grant in policy.grants.values()
            if grant.enabled and grant.products - grant.disabled_products
            for profile_name in grant.profiles - grant.disabled_profiles
            if policy.profiles[profile_name].capability
            not in grant.disabled_capabilities
        }
        required_providers = {
            profile.provider
            for profile_name, profile in policy.profiles.items()
            if profile_name in active_profile_names
            and profile.enabled
            and profile.required_for_readiness
        }
        return required_providers.issubset(
            self._providers
        ) and self._health.all_healthy(required_providers)

    def profile_health(self, profile_name: str) -> ProviderHealthStatus:
        profile = self._policy_store.current.profiles.get(profile_name)
        if profile is None or not profile.enabled:
            return "unknown"
        return self._health.status(profile.provider)

    def safe_readiness_details(self) -> dict[str, object]:
        policy, _, load_outcome = self._policy_store.snapshot()
        return {
            "generation_id": policy.generation_id,
            "policy_load": load_outcome,
            "provider_counts": self._health.safe_counts(),
        }

    def load_policy_document(self, value: Mapping[str, object]) -> bool:
        return self._policy_store.load_document(value)

    def resolve(
        self,
        *,
        caller: str,
        product: str,
        profile_name: str,
        capability: Capability,
    ) -> tuple[ModelProfile, object]:
        policy = self._policy_store.current
        grant = policy.grants.get(caller)
        if (
            grant is None
            or not grant.enabled
            or product not in grant.products
            or product in grant.disabled_products
            or profile_name not in grant.profiles
            or profile_name in grant.disabled_profiles
            or capability not in grant.capabilities
            or capability in grant.disabled_capabilities
        ):
            raise PROFILE_NOT_ALLOWED
        profile = policy.profiles.get(profile_name)
        if profile is None or not profile.enabled or profile.capability != capability:
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

    def speech_provider(self, provider: object) -> SpeechSynthesisProvider:
        if not callable(getattr(provider, "synthesize", None)):
            raise PROVIDER_NOT_CONFIGURED
        return cast(SpeechSynthesisProvider, provider)

    def media_provider(self, provider: object) -> MediaAnalysisProvider:
        if not callable(getattr(provider, "analyze_media", None)):
            raise PROVIDER_NOT_CONFIGURED
        return cast(MediaAnalysisProvider, provider)

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
        await self._health.close()
        for provider in self._providers.values():
            close = getattr(provider, "close", None)
            if callable(close):
                await close()

    async def start(self) -> None:
        await self._health.start()


def _providers(settings: Settings) -> dict[str, object]:
    credentials = resolve_provider_credentials(settings)
    return _providers_with_credentials(settings, credentials)


def _providers_with_credentials(
    settings: Settings,
    credentials: ProviderCredentials,
) -> dict[str, object]:
    providers: dict[str, object] = {}
    if settings.ollama_enabled:
        providers["ollama"] = OllamaProvider(
            base_url=str(settings.ollama_base_url),
            timeout_seconds=settings.request_timeout_seconds,
        )
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
