from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from contextlib import suppress
from dataclasses import dataclass
from typing import AsyncIterator, Protocol
from uuid import UUID, uuid4

from app.core.circuit import CircuitRegistry
from app.core.errors import CAPACITY_EXCEEDED, REQUEST_INVALID
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

    async def acquire(self, key: tuple[str, ...], limit: int, ttl: float) -> str | None: ...

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
            expired = [lease_id for lease_id, lease in self._leases.items() if lease.expires_at <= now]
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


class GatewayService:
    def __init__(
        self,
        registry: ProviderRegistry,
        admission: AdmissionController,
        circuits: CircuitRegistry | None = None,
    ) -> None:
        self.registry = registry
        self.admission = admission
        self.circuits = circuits or CircuitRegistry(enabled=False)

    async def generate_text(
        self, *, caller: str, request: TextGenerationRequest
    ) -> TextGenerationResponse:
        profile, raw_provider = self.registry.resolve(
            caller=caller,
            product=request.product,
            profile_name=request.profile,
            capability="text_generation",
        )
        provider = self.registry.text_provider(raw_provider)
        route_id = f"{profile.provider}.{profile.model}"
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
        profile, raw_provider = self.registry.resolve(
            caller=caller,
            product=request.product,
            profile_name=request.profile,
            capability="speech_synthesis",
        )
        provider = self.registry.speech_provider(raw_provider)
        route_id = f"{profile.provider}.{profile.model}"
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
        provider = self.registry.realtime_translation_provider(raw_provider)
        return await provider.open_realtime_translation(
            model=profile.model,
            target_language=request.target_language,
            instructions=request.instructions,
            audio_sample_rate_hz=request.audio_sample_rate_hz,
        )
