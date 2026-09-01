from __future__ import annotations

import asyncio
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from contextlib import suppress
from dataclasses import dataclass
from threading import Lock
from typing import AsyncIterator, Protocol
from uuid import UUID, uuid4
from datetime import datetime, timezone

from app.core.circuit import CircuitRegistry
from app.core.errors import (
    CAPACITY_EXCEEDED,
    GatewayError,
    PROFILE_NOT_ALLOWED,
    PROVIDER_OUTCOME_AMBIGUOUS,
    REQUEST_INVALID,
    USAGE_DELIVERY_UNCONFIRMED,
)
from app.core.measurement import MeasurementAccumulator, MeasurementDisposition
from app.core.usage_delivery import UsageDelivery, UsageDeliveryError
from app.models import (
    AudioTranscriptionResponse,
    MediaAnalysisResponse,
    RealtimeStart,
    RealtimeTranslationStart,
    SpeechSynthesisRequest,
    TextGenerationRequest,
    TextGenerationResponse,
)
from app.providers.base import RealtimeTranscriptionSession, SpeechResult
from app.providers.registry import ProviderRegistry


class AdmissionStore(Protocol):
    """Atomic lease boundary for process-local or shared implementations."""

    async def acquire(
        self, key: tuple[str, ...], limit: int, ttl: float
    ) -> str | None: ...

    async def renew(self, lease_id: str, ttl: float) -> bool: ...

    async def release(self, lease_id: str) -> None: ...

    async def recover_expired(self) -> int: ...

    async def inspect(self) -> dict[tuple[str, ...], int]: ...


@dataclass(frozen=True, slots=True)
class _Lease:
    key: tuple[str, ...]
    expires_at: float


class InMemoryAdmissionStore:
    """Atomic process-local store used until an approved shared store exists."""

    def __init__(self) -> None:
        self._leases: dict[str, _Lease] = {}
        self._lock = asyncio.Lock()

    async def recover_expired(self) -> int:
        now = time.monotonic()
        async with self._lock:
            expired = [
                lease_id
                for lease_id, lease in self._leases.items()
                if lease.expires_at <= now
            ]
            for lease_id in expired:
                del self._leases[lease_id]
            return len(expired)

    async def acquire(self, key: tuple[str, ...], limit: int, ttl: float) -> str | None:
        if limit < 1 or ttl <= 0:
            raise ValueError("admission limit and lease TTL must be positive")
        await self.recover_expired()
        async with self._lock:
            active = sum(lease.key == key for lease in self._leases.values())
            if active >= limit:
                return None
            lease_id = str(uuid4())
            self._leases[lease_id] = _Lease(key=key, expires_at=time.monotonic() + ttl)
            return lease_id

    async def renew(self, lease_id: str, ttl: float) -> bool:
        if ttl <= 0:
            raise ValueError("lease TTL must be positive")
        await self.recover_expired()
        async with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None:
                return False
            self._leases[lease_id] = _Lease(
                key=lease.key, expires_at=time.monotonic() + ttl
            )
            return True

    async def release(self, lease_id: str) -> None:
        async with self._lock:
            self._leases.pop(lease_id, None)

    async def inspect(self) -> dict[tuple[str, ...], int]:
        await self.recover_expired()
        async with self._lock:
            counts: defaultdict[tuple[str, ...], int] = defaultdict(int)
            for lease in self._leases.values():
                counts[lease.key] += 1
            return dict(counts)


class AdmissionController:
    def __init__(
        self,
        caller_limits: dict[str, int],
        *,
        store: AdmissionStore | None = None,
        lease_ttl_seconds: float = 300,
    ) -> None:
        self._limits = dict(caller_limits)
        if lease_ttl_seconds <= 0:
            raise ValueError("admission lease TTL must be positive")
        self._store = store or InMemoryAdmissionStore()
        self._lease_ttl_seconds = lease_ttl_seconds

    @asynccontextmanager
    async def lease(
        self,
        caller: str,
        capability: str = "default",
        *,
        product: str | None = None,
        profile: str | None = None,
        route: str | None = None,
    ) -> AsyncIterator[None]:
        key = tuple(
            value
            for value in (caller, product, profile, route, capability)
            if value is not None
        )
        candidates = [
            ":".join(key),
            ":".join(value for value in (caller, product, capability) if value),
            f"{caller}:{capability}",
            caller,
        ]
        limit = next(
            (self._limits[name] for name in candidates if name in self._limits), 1
        )
        lease_id = await self._store.acquire(key, limit, self._lease_ttl_seconds)
        if lease_id is None:
            raise CAPACITY_EXCEEDED
        heartbeat = asyncio.create_task(
            self._heartbeat(lease_id), name="admission-lease-heartbeat"
        )
        try:
            yield
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            await self._store.release(lease_id)

    async def _heartbeat(self, lease_id: str) -> None:
        interval = max(0.01, self._lease_ttl_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            if not await self._store.renew(lease_id, self._lease_ttl_seconds):
                return

    async def active_count(self) -> int:
        return sum((await self._store.inspect()).values())


class GatewayService:
    def __init__(
        self,
        registry: ProviderRegistry,
        admission: AdmissionController,
        circuits: CircuitRegistry | None = None,
        usage_delivery: UsageDelivery | None = None,
    ) -> None:
        self.registry = registry
        self.admission = admission
        self.circuits = circuits or CircuitRegistry(enabled=False)
        self.usage_delivery = usage_delivery
        self._metric_counts: Counter[str] = Counter()
        self._metric_lock = Lock()

    def _record_metric(self, name: str) -> None:
        with self._metric_lock:
            self._metric_counts[name] += 1

    def safe_metric_counts(self) -> dict[str, int]:
        with self._metric_lock:
            return dict(self._metric_counts)

    async def _deliver_batch_usage(
        self,
        *,
        request_id: UUID,
        caller: str,
        product: str,
        capability: str,
        profile: str,
        route_id: str,
        started_at: datetime,
        disposition: str,
        counts: dict[str, tuple[int | None, str]],
    ) -> None:
        if self.usage_delivery is None:
            self._record_metric("usage_delivery_unconfigured")
            return
        accumulator = MeasurementAccumulator(
            request_id=request_id,
            workload_id=caller,
            product=product,
            capability=capability,
            profile=profile,
            route_id=route_id,
            started_at=started_at,
        )
        for field, (value, source) in counts.items():
            if value is not None:
                accumulator.record_count(field, value, source=source)
        try:
            outcome = await self.usage_delivery.deliver(
                accumulator.finalize(disposition)
            )
            self._record_metric(f"usage_delivery_{outcome.status}")
        except UsageDeliveryError as error:
            self._record_metric("usage_delivery_failed")
            raise USAGE_DELIVERY_UNCONFIRMED from error

    async def _record_ambiguous_batch_usage(
        self,
        *,
        request_id: UUID,
        caller: str,
        product: str,
        capability: str,
        profile: str,
        route_id: str,
        started_at: datetime,
    ) -> None:
        try:
            await self._deliver_batch_usage(
                request_id=request_id,
                caller=caller,
                product=product,
                capability=capability,
                profile=profile,
                route_id=route_id,
                started_at=started_at,
                disposition="ambiguous",
                counts={},
            )
        except GatewayError:
            # The caller must still receive the stronger do-not-retry outcome.
            # A configured durable sink owns its own bounded retry/DLQ path.
            pass
        self._record_metric("batch_provider_outcome_ambiguous")

    async def generate_text(
        self, *, caller: str, request: TextGenerationRequest
    ) -> TextGenerationResponse:
        started_at = datetime.now(timezone.utc)
        profile, raw_provider = self.registry.resolve(
            caller=caller,
            product=request.product,
            profile_name=request.profile,
            capability="text_generation",
        )
        provider = self.registry.text_provider(raw_provider)
        route_id = f"{profile.provider}.{profile.model}"
        try:
            async with self.admission.lease(
                caller,
                "text_generation",
                product=request.product,
                profile=request.profile,
                route=route_id,
            ):
                async with self.circuits.call(route_id):
                    result = await provider.generate(
                        model=profile.model,
                        system_instruction=request.system_instruction,
                        input_text=request.input,
                        response_format=request.response_format,
                        temperature=request.temperature,
                    )
        except GatewayError:
            raise
        except Exception as error:
            await self._record_ambiguous_batch_usage(
                request_id=request.request_id,
                caller=caller,
                product=request.product,
                capability="text_generation",
                profile=request.profile,
                route_id=route_id,
                started_at=started_at,
            )
            raise PROVIDER_OUTCOME_AMBIGUOUS from error
        await self._deliver_batch_usage(
            request_id=request.request_id,
            caller=caller,
            product=request.product,
            capability="text_generation",
            profile=request.profile,
            route_id=route_id,
            started_at=started_at,
            disposition="complete",
            counts={
                "input_tokens": (result.usage.input_tokens, "provider_reported"),
                "output_tokens": (result.usage.output_tokens, "provider_reported"),
            },
        )
        return TextGenerationResponse(
            request_id=request.request_id,
            output=result.output,
            provider=profile.provider,
            model=profile.model,
            usage=result.usage,
        )

    async def transcribe_audio(
        self,
        *,
        caller: str,
        request_id: UUID,
        product: str,
        profile_name: str,
        audio: bytes,
        filename: str,
        content_type: str,
        source_language: str,
        terminology_prompt: str,
    ) -> AudioTranscriptionResponse:
        started_at = datetime.now(timezone.utc)
        if not audio:
            raise REQUEST_INVALID
        profile, raw_provider = self.registry.resolve(
            caller=caller,
            product=product,
            profile_name=profile_name,
            capability="audio_transcription",
        )
        provider = self.registry.transcription_provider(raw_provider)
        route_id = f"{profile.provider}.{profile.model}"
        try:
            async with self.admission.lease(
                caller,
                "audio_transcription",
                product=product,
                profile=profile_name,
                route=route_id,
            ):
                async with self.circuits.call(route_id):
                    result = await provider.transcribe(
                        model=profile.model,
                        audio=audio,
                        filename=filename,
                        content_type=content_type,
                        source_language=source_language,
                        terminology_prompt=terminology_prompt,
                    )
        except GatewayError:
            raise
        except Exception as error:
            await self._record_ambiguous_batch_usage(
                request_id=request_id,
                caller=caller,
                product=product,
                capability="audio_transcription",
                profile=profile_name,
                route_id=route_id,
                started_at=started_at,
            )
            raise PROVIDER_OUTCOME_AMBIGUOUS from error
        await self._deliver_batch_usage(
            request_id=request_id,
            caller=caller,
            product=product,
            capability="audio_transcription",
            profile=profile_name,
            route_id=route_id,
            started_at=started_at,
            disposition="complete",
            counts={
                "source_audio_received_bytes": (len(audio), "gateway_observed"),
                "source_audio_accepted_bytes": (len(audio), "gateway_observed"),
                "source_audio_accepted_ms": (
                    result.usage.audio_ms,
                    "provider_reported",
                ),
            },
        )
        return AudioTranscriptionResponse(
            request_id=request_id,
            text=result.text,
            provider=profile.provider,
            model=profile.model,
            detected_language=result.detected_language,
            usage=result.usage,
        )

    async def synthesize_speech(
        self, *, caller: str, request: SpeechSynthesisRequest
    ) -> tuple[SpeechResult, str, str]:
        started_at = datetime.now(timezone.utc)
        profile, raw_provider = self.registry.resolve(
            caller=caller,
            product=request.product,
            profile_name=request.profile,
            capability="speech_synthesis",
        )
        provider = self.registry.speech_provider(raw_provider)
        route_id = f"{profile.provider}.{profile.model}"
        try:
            async with self.admission.lease(
                caller,
                "speech_synthesis",
                product=request.product,
                profile=request.profile,
                route=route_id,
            ):
                async with self.circuits.call(route_id):
                    result = await provider.synthesize(
                        model=profile.model,
                        input_text=request.input,
                        voice=request.voice,
                        instructions=request.instructions,
                    )
        except GatewayError:
            raise
        except Exception as error:
            await self._record_ambiguous_batch_usage(
                request_id=request.request_id,
                caller=caller,
                product=request.product,
                capability="speech_synthesis",
                profile=request.profile,
                route_id=route_id,
                started_at=started_at,
            )
            raise PROVIDER_OUTCOME_AMBIGUOUS from error
        await self._deliver_batch_usage(
            request_id=request.request_id,
            caller=caller,
            product=request.product,
            capability="speech_synthesis",
            profile=request.profile,
            route_id=route_id,
            started_at=started_at,
            disposition="complete",
            counts={
                "input_tokens": (result.usage.input_tokens, "provider_reported"),
                "generated_audio_bytes": (len(result.audio), "gateway_observed"),
            },
        )
        return result, profile.provider, profile.model

    async def analyze_media(
        self,
        *,
        caller: str,
        request_id: UUID,
        product: str,
        profile_name: str,
        system_instruction: str,
        prompt: str,
        media: bytes,
        content_type: str,
        media_kind: str,
        temperature: float,
    ) -> MediaAnalysisResponse:
        started_at = datetime.now(timezone.utc)
        if not media or media_kind not in {"image", "video"}:
            raise REQUEST_INVALID
        capability = f"{media_kind}_analysis"
        profile, raw_provider = self.registry.resolve(
            caller=caller,
            product=product,
            profile_name=profile_name,
            capability=capability,
        )
        provider = self.registry.media_provider(raw_provider)
        route_id = f"{profile.provider}.{profile.model}"
        try:
            async with self.admission.lease(
                caller,
                capability,
                product=product,
                profile=profile_name,
                route=route_id,
            ):
                async with self.circuits.call(route_id):
                    result = await provider.analyze_media(
                        model=profile.model,
                        system_instruction=system_instruction,
                        prompt=prompt,
                        media=media,
                        content_type=content_type,
                        media_kind=media_kind,
                        temperature=temperature,
                    )
        except GatewayError:
            raise
        except Exception as error:
            await self._record_ambiguous_batch_usage(
                request_id=request_id,
                caller=caller,
                product=product,
                capability=capability,
                profile=profile_name,
                route_id=route_id,
                started_at=started_at,
            )
            raise PROVIDER_OUTCOME_AMBIGUOUS from error
        await self._deliver_batch_usage(
            request_id=request_id,
            caller=caller,
            product=product,
            capability=capability,
            profile=profile_name,
            route_id=route_id,
            started_at=started_at,
            disposition="complete",
            counts={
                "input_tokens": (result.usage.input_tokens, "provider_reported"),
                "output_tokens": (result.usage.output_tokens, "provider_reported"),
            },
        )
        return MediaAnalysisResponse(
            request_id=request_id,
            output=result.output,
            provider=profile.provider,
            model=profile.model,
            usage=result.usage,
        )

    async def open_realtime(
        self, *, caller: str, request: RealtimeStart
    ) -> RealtimeTranscriptionSession:
        profile, raw_provider = self.registry.resolve(
            caller=caller,
            product=request.product,
            profile_name=request.profile,
            capability="realtime_transcription",
        )
        provider = self.registry.realtime_provider(raw_provider)
        return await provider.open_realtime(
            model=profile.model,
            source_language=request.source_language,
            terminology_prompt=request.terminology_prompt,
            terminology_keywords=tuple(request.terminology_keywords),
            audio_sample_rate_hz=request.audio_sample_rate_hz,
        )

    async def open_realtime_translation(
        self, *, caller: str, request: RealtimeTranslationStart
    ) -> RealtimeTranscriptionSession:
        profile, raw_provider = self.registry.resolve(
            caller=caller,
            product=request.product,
            profile_name=request.profile,
            capability="realtime_translation",
        )
        if profile.supported_outputs is not None and not frozenset(
            request.outputs
        ).issubset(profile.supported_outputs):
            raise PROFILE_NOT_ALLOWED
        if not _target_language_supported(
            profile.supported_target_languages, request.target_language
        ):
            raise PROFILE_NOT_ALLOWED
        if (
            profile.supported_audio_sample_rates_hz is not None
            and request.audio_sample_rate_hz
            not in profile.supported_audio_sample_rates_hz
        ):
            raise PROFILE_NOT_ALLOWED
        provider = self.registry.realtime_translation_provider(raw_provider)
        route_id = f"{profile.provider}.{profile.model}"
        self._ensure_realtime_route_available(route_id)
        async with self.circuits.call(route_id):
            return await provider.open_realtime_translation(
                model=profile.model,
                target_language=request.target_language,
                instructions=request.instructions,
                audio_sample_rate_hz=request.audio_sample_rate_hz,
                outputs=frozenset(request.outputs),
            )

    def validate_realtime_translation_route(
        self,
        *,
        caller: str,
        request: RealtimeTranslationStart,
        primary_profile_name: str,
        profile_name: str,
    ) -> None:
        """Preflight a fallback route without opening a provider session."""
        profile, raw_provider = self.registry.resolve(
            caller=caller,
            product=request.product,
            profile_name=profile_name,
            capability="realtime_translation",
        )
        primary_profile, _ = self.registry.resolve(
            caller=caller,
            product=request.product,
            profile_name=primary_profile_name,
            capability="realtime_translation",
        )
        if (
            profile.provider == primary_profile.provider
            and profile.model == primary_profile.model
        ):
            raise PROFILE_NOT_ALLOWED
        if profile.privacy_class != primary_profile.privacy_class:
            raise PROFILE_NOT_ALLOWED
        if profile.usage_authority != primary_profile.usage_authority:
            raise PROFILE_NOT_ALLOWED
        self.registry.realtime_translation_provider(raw_provider)
        if profile.supported_outputs is not None and not frozenset(
            request.outputs
        ).issubset(profile.supported_outputs):
            raise PROFILE_NOT_ALLOWED
        if not _target_language_supported(
            profile.supported_target_languages, request.target_language
        ):
            raise PROFILE_NOT_ALLOWED
        if (
            profile.supported_audio_sample_rates_hz is not None
            and request.audio_sample_rate_hz
            not in profile.supported_audio_sample_rates_hz
        ):
            raise PROFILE_NOT_ALLOWED
        self._ensure_realtime_route_available(f"{profile.provider}.{profile.model}")

    def _ensure_realtime_route_available(self, route_id: str) -> None:
        if self.circuits.allow(route_id):
            return
        raise GatewayError(
            503,
            "ai_gateway_provider_unavailable",
            "The AI provider is temporarily unavailable.",
            retryable=True,
            retry_after_ms=self.circuits.retry_after_ms(route_id),
        )

    def record_realtime_route_failure(
        self, *, caller: str, request: RealtimeTranslationStart, profile_name: str
    ) -> None:
        profile, _ = self.registry.resolve(
            caller=caller,
            product=request.product,
            profile_name=profile_name,
            capability="realtime_translation",
        )
        self.circuits.record_failure(f"{profile.provider}.{profile.model}")

    async def deliver_realtime_usage(
        self,
        *,
        request_id: UUID,
        caller: str,
        request: RealtimeTranslationStart,
        selected_profile: str,
        started_at: datetime,
        finished_at: datetime,
        disposition: MeasurementDisposition,
        source_audio_received_bytes: int,
        source_audio_accepted_bytes: int,
        fallback_count: int,
        rotation_count: int,
    ) -> None:
        """Deliver one terminal realtime measurement through the same boundary."""
        if self.usage_delivery is None:
            self._record_metric("usage_delivery_unconfigured")
            return
        selected, _ = self.registry.resolve(
            caller=caller,
            product=request.product,
            profile_name=selected_profile,
            capability="realtime_translation",
        )
        accumulator = MeasurementAccumulator(
            request_id=request_id,
            workload_id=caller,
            product=request.product,
            capability="realtime_translation",
            profile=selected_profile,
            route_id=f"{selected.provider}.{selected.model}",
            started_at=started_at,
        )
        accumulator.record_count(
            "source_audio_received_bytes",
            source_audio_received_bytes,
            source="gateway_observed",
        )
        accumulator.record_count(
            "source_audio_accepted_bytes",
            source_audio_accepted_bytes,
            source="gateway_observed",
        )
        accepted_ms = int(
            source_audio_accepted_bytes * 1000 / (request.audio_sample_rate_hz * 2)
        )
        accumulator.record_count(
            "source_audio_accepted_ms",
            accepted_ms,
            source="estimated",
            estimation_method="pcm16.v1",
        )
        try:
            outcome = await self.usage_delivery.deliver(
                accumulator.finalize(
                    disposition,
                    finished_at=finished_at,
                    fallback_count=fallback_count,
                    rotation_count=rotation_count,
                )
            )
            self._record_metric(f"usage_delivery_{outcome.status}")
            self._record_metric(f"realtime_terminal_{disposition}")
        except UsageDeliveryError as error:
            self._record_metric("usage_delivery_failed")
            raise USAGE_DELIVERY_UNCONFIRMED from error


def _target_language_supported(
    supported: frozenset[str] | None, requested: str
) -> bool:
    if supported is None or "*" in supported:
        return True
    language = requested.lower()
    base = language.split("-", 1)[0]
    return language in supported or base in supported
