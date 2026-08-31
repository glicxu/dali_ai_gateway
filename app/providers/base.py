from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import UsageMeasurement


@dataclass(frozen=True, slots=True)
class TextResult:
    output: str
    usage: UsageMeasurement


@dataclass(frozen=True, slots=True)
class MediaResult:
    output: str
    usage: UsageMeasurement


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    detected_language: str | None
    usage: UsageMeasurement


@dataclass(frozen=True, slots=True)
class SpeechResult:
    audio: bytes
    content_type: str
    usage: UsageMeasurement


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    type: str
    text: str | None = None
    item_id: str | None = None
    code: str | None = None
    audio: str | None = None
    content_type: str | None = None
    sample_rate_hz: int | None = None
    channels: int | None = None


class TextProvider(Protocol):
    async def probe(self) -> None: ...

    async def generate(
        self,
        *,
        model: str,
        system_instruction: str,
        input_text: str,
        response_format: str,
        temperature: float,
    ) -> TextResult: ...

    async def close(self) -> None: ...


class AudioTranscriptionProvider(Protocol):
    async def transcribe(
        self,
        *,
        model: str,
        audio: bytes,
        filename: str,
        content_type: str,
        source_language: str,
        terminology_prompt: str,
    ) -> TranscriptionResult: ...


class SpeechSynthesisProvider(Protocol):
    async def synthesize(
        self,
        *,
        model: str,
        input_text: str,
        voice: str,
        instructions: str,
    ) -> SpeechResult: ...


class MediaAnalysisProvider(Protocol):
    async def analyze_media(
        self,
        *,
        model: str,
        system_instruction: str,
        prompt: str,
        media: bytes,
        content_type: str,
        media_kind: str,
        temperature: float,
    ) -> MediaResult: ...


class RealtimeTranscriptionSession(Protocol):
    async def append(self, audio_base64: str) -> None: ...

    async def commit(self) -> None: ...

    async def clear(self) -> None: ...

    async def next_event(self) -> RealtimeEvent: ...

    async def close(self) -> None: ...


class RealtimeTranscriptionProvider(Protocol):
    async def open_realtime(
        self,
        *,
        model: str,
        source_language: str,
        terminology_prompt: str,
        terminology_keywords: tuple[str, ...],
        audio_sample_rate_hz: int,
    ) -> RealtimeTranscriptionSession: ...


class RealtimeTranslationProvider(Protocol):
    async def open_realtime_translation(
        self,
        *,
        model: str,
        target_language: str,
        instructions: str,
        audio_sample_rate_hz: int,
    ) -> RealtimeTranscriptionSession: ...
