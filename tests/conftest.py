from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app
from app.models import UsageMeasurement
from app.providers.base import (
    MediaResult,
    RealtimeEvent,
    SpeechResult,
    TextResult,
    TranscriptionResult,
)


@dataclass
class FakeRealtimeSession:
    event_prefix: str = "transcript"
    appended: list[str] = field(default_factory=list)
    committed: int = 0
    cleared: int = 0
    closed: bool = False
    closed_event: threading.Event = field(default_factory=threading.Event)
    events: asyncio.Queue[RealtimeEvent] = field(default_factory=asyncio.Queue)

    async def append(self, audio_base64: str) -> None:
        self.appended.append(audio_base64)
        await self.events.put(
            RealtimeEvent(
                f"{self.event_prefix}.delta", text="Lecture", item_id="item-1"
            )
        )

    async def commit(self) -> None:
        self.committed += 1
        await self.events.put(
            RealtimeEvent(
                f"{self.event_prefix}.final",
                text="Lecture text.",
                item_id="item-1",
            )
        )

    async def clear(self) -> None:
        self.cleared += 1

    async def next_event(self) -> RealtimeEvent:
        return await self.events.get()

    async def close(self) -> None:
        self.closed = True
        self.closed_event.set()


@dataclass
class FakeProvider:
    realtime: FakeRealtimeSession = field(default_factory=FakeRealtimeSession)
    realtime_translation: FakeRealtimeSession = field(
        default_factory=lambda: FakeRealtimeSession(event_prefix="translation")
    )
    generated_inputs: list[str] = field(default_factory=list)
    transcribed_audio: list[bytes] = field(default_factory=list)
    analyzed_media: list[tuple[str, bytes]] = field(default_factory=list)
    synthesized_text: list[str] = field(default_factory=list)
    probe_calls: int = 0
    probe_error: bool = False

    async def probe(self) -> None:
        self.probe_calls += 1
        if self.probe_error:
            raise RuntimeError("normalized fake probe failure")

    async def generate(self, **kwargs) -> TextResult:
        self.generated_inputs.append(kwargs["input_text"])
        return TextResult(
            output="Generated result.",
            usage=UsageMeasurement(input_tokens=7, output_tokens=3),
        )

    async def transcribe(self, **kwargs) -> TranscriptionResult:
        self.transcribed_audio.append(kwargs["audio"])
        return TranscriptionResult(
            text="Captured lecture.",
            detected_language="en",
            usage=UsageMeasurement(audio_ms=250),
        )

    async def analyze_media(self, **kwargs) -> MediaResult:
        self.analyzed_media.append((kwargs["media_kind"], kwargs["media"]))
        return MediaResult(
            output="Analyzed media.",
            usage=UsageMeasurement(input_tokens=11, output_tokens=5),
        )

    async def synthesize(self, **kwargs) -> SpeechResult:
        self.synthesized_text.append(kwargs["input_text"])
        return SpeechResult(
            audio=b"RIFF-test-audio",
            content_type="audio/wav",
            usage=UsageMeasurement(input_tokens=4, output_tokens=8),
        )

    async def open_realtime(self, **kwargs) -> FakeRealtimeSession:
        return self.realtime

    async def open_realtime_translation(self, **kwargs) -> FakeRealtimeSession:
        return self.realtime_translation

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        service_tokens_json=SecretStr(
            json.dumps({"dali_classroom_server": "service-test-token"})
        ),
        caller_limits_json=json.dumps({"dali_classroom_server": 1}),
    )


@pytest.fixture
def client(settings: Settings, fake_provider: FakeProvider):
    application = create_app(
        settings,
        providers={"openai": fake_provider, "gemini": fake_provider},
    )
    with TestClient(application) as value:
        yield value


@pytest.fixture
def headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer service-test-token",
        "X-Dali-Caller": "dali_classroom_server",
    }
