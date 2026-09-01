from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.realtime_policy import RealtimeRoutePolicy


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


class MediaAnalysisResponse(StrictModel):
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


class SpeechSynthesisRequest(StrictModel):
    request_id: UUID
    product: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    profile: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    input: str = Field(min_length=1, max_length=4_096)
    voice: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    instructions: str = Field(default="", max_length=1_000)


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
    outputs: list[
        Literal["source_transcript", "target_transcript", "translated_audio"]
    ] = Field(
        default_factory=lambda: ["target_transcript", "translated_audio"],
        min_length=1,
        max_length=3,
    )

    @field_validator("outputs")
    @classmethod
    def outputs_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("translation outputs must be unique")
        return value


class RealtimeAudioAppend(StrictModel):
    type: Literal["audio.append"]
    audio: str = Field(min_length=1)


class RealtimeCommand(StrictModel):
    type: Literal["audio.commit", "audio.clear", "session.stop"]


class RealtimeV2TranslationStart(StrictModel):
    type: Literal["session.start"]
    request_id: UUID
    product: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    profile: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    target_language: str = Field(min_length=2, max_length=35)
    fallback_profile: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_.-]{2,127}$"
    )
    compare_profile: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_.-]{2,127}$"
    )
    policy: Literal["single", "compare", "windowed_failover"] = "single"
    window_seconds: Literal[60, 90, 120] = 90
    instructions: str = Field(default="", max_length=20_000)
    audio_sample_rate_hz: Literal[16000, 24000] = 24000
    outputs: list[
        Literal["source_transcript", "target_transcript", "translated_audio"]
    ] = Field(default_factory=lambda: ["target_transcript", "translated_audio"])

    @field_validator("outputs")
    @classmethod
    def outputs_are_unique(cls, value: list[str]) -> list[str]:
        if not value or len(value) != len(set(value)):
            raise ValueError("translation outputs must be non-empty and unique")
        return value

    @model_validator(mode="after")
    def validate_route_policy(self) -> RealtimeV2TranslationStart:
        RealtimeRoutePolicy(
            mode=self.policy,
            primary_profile=self.profile,
            fallback_profile=self.fallback_profile,
            compare_profile=self.compare_profile,
            window_seconds=self.window_seconds,
        )
        return self


class RealtimeV2AudioAppend(StrictModel):
    type: Literal["audio.append"]
    sequence: int = Field(ge=1)
    audio: str = Field(min_length=1)
