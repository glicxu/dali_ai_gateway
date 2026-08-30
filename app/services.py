from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

from app.core.errors import CAPACITY_EXCEEDED, REQUEST_INVALID
from app.models import (
    AudioTranscriptionResponse,
    RealtimeStart,
    RealtimeTranslationStart,
    TextGenerationRequest,
    TextGenerationResponse,
)
from app.providers.base import RealtimeTranscriptionSession
from app.providers.registry import ProviderRegistry


class AdmissionController:
    def __init__(self, caller_limits: dict[str, int]) -> None:
        self._limits = dict(caller_limits)
        self._active: defaultdict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def lease(self, caller: str) -> AsyncIterator[None]:
        async with self._lock:
            limit = self._limits.get(caller, 1)
            if self._active[caller] >= limit:
                raise CAPACITY_EXCEEDED
            self._active[caller] += 1
        try:
            yield
        finally:
            async with self._lock:
                self._active[caller] = max(0, self._active[caller] - 1)


class GatewayService:
    def __init__(
        self,
        registry: ProviderRegistry,
        admission: AdmissionController,
    ) -> None:
        self.registry = registry
        self.admission = admission

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
        async with self.admission.lease(caller):
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
        async with self.admission.lease(caller):
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
