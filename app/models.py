from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextGenerationRequest(StrictModel):
    request_id: UUID
    product: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    profile: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    system_instruction: str = Field(min_length=1, max_length=20_000)
    input: str = Field(min_length=1, max_length=200_000)
    response_format: Literal["text", "json"] = "text"
    temperature: float = Field(default=0, ge=0, le=2)


class UsageMeasurement(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    audio_ms: int | None = Field(default=None, ge=0)


class TextGenerationResponse(StrictModel):
    request_id: UUID
    output: str
    provider: str
    model: str
    usage: UsageMeasurement = Field(default_factory=UsageMeasurement)


class AudioTranscriptionResponse(StrictModel):
    request_id: UUID
    text: str
    provider: str
    model: str
    detected_language: str | None = None
    usage: UsageMeasurement = Field(default_factory=UsageMeasurement)


class RealtimeStart(StrictModel):
    type: Literal["session.start"]
    request_id: UUID
    product: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    profile: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    source_language: str = Field(default="auto", min_length=2, max_length=35)
    terminology_prompt: str = Field(default="", max_length=2_000)
    terminology_keywords: list[str] = Field(default_factory=list, max_length=100)
    audio_sample_rate_hz: Literal[16000, 24000] = 24000


class RealtimeTranslationStart(StrictModel):
    type: Literal["session.start"]
    request_id: UUID
    product: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    profile: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    target_language: str = Field(min_length=2, max_length=35)
    instructions: str = Field(default="", max_length=20_000)
    audio_sample_rate_hz: Literal[16000, 24000] = 24000


class RealtimeAudioAppend(StrictModel):
    type: Literal["audio.append"]
    audio: str = Field(min_length=1)


class RealtimeCommand(StrictModel):
    type: Literal["audio.commit", "audio.clear", "session.stop"]
