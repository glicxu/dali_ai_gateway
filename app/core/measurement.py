from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.policy import Capability


MEASUREMENT_VERSION = "dali.ai.usage.v1"
_MEASUREMENT_NAMESPACE = UUID("6ec01334-d2ab-5fac-a3a6-796290cc093c")

MeasurementSource = Literal[
    "provider_reported",
    "gateway_observed",
    "estimated",
    "unavailable",
]
MeasurementDisposition = Literal[
    "complete",
    "partial",
    "cancelled",
    "timed_out",
    "disconnected",
    "provider_failed",
    "ambiguous",
]


class _MeasurementModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MeasuredCount(_MeasurementModel):
    value: int | None = Field(default=None, ge=0)
    source: MeasurementSource = "unavailable"
    estimation_method: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.-]{2,127}$",
    )

    @model_validator(mode="after")
    def source_matches_value(self) -> MeasuredCount:
        if self.source == "unavailable":
            if self.value is not None or self.estimation_method is not None:
                raise ValueError("unavailable measurement cannot contain a value")
            return self
        if self.value is None:
            raise ValueError("available measurement requires a value")
        if self.source == "estimated" and self.estimation_method is None:
            raise ValueError("estimated measurement requires a method")
        if self.source != "estimated" and self.estimation_method is not None:
            raise ValueError("exact measurement cannot contain an estimation method")
        return self


class UsageMeasurementEnvelope(_MeasurementModel):
    version: Literal["dali.ai.usage.v1"] = MEASUREMENT_VERSION
    event_id: UUID
    request_id: UUID
    workload_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    product: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    capability: Capability
    profile: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    route_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    started_at: datetime
    finished_at: datetime
    disposition: MeasurementDisposition
    input_tokens: MeasuredCount = Field(default_factory=MeasuredCount)
    output_tokens: MeasuredCount = Field(default_factory=MeasuredCount)
    source_audio_received_bytes: MeasuredCount = Field(default_factory=MeasuredCount)
    source_audio_accepted_bytes: MeasuredCount = Field(default_factory=MeasuredCount)
    source_audio_received_ms: MeasuredCount = Field(default_factory=MeasuredCount)
    source_audio_accepted_ms: MeasuredCount = Field(default_factory=MeasuredCount)
    generated_audio_bytes: MeasuredCount = Field(default_factory=MeasuredCount)
    forwarded_audio_bytes: MeasuredCount = Field(default_factory=MeasuredCount)
    generated_audio_ms: MeasuredCount = Field(default_factory=MeasuredCount)
    forwarded_audio_ms: MeasuredCount = Field(default_factory=MeasuredCount)
    fallback_count: int = Field(default=0, ge=0, le=16)
    rotation_count: int = Field(default=0, ge=0, le=128)

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("measurement timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> UsageMeasurementEnvelope:
        if self.finished_at < self.started_at:
            raise ValueError("measurement finish precedes start")
        expected = measurement_event_id(
            request_id=self.request_id,
            capability=self.capability,
            version=self.version,
        )
        if self.event_id != expected:
            raise ValueError("measurement event ID is not canonical")
        return self


_COUNT_FIELDS = {
    "input_tokens",
    "output_tokens",
    "source_audio_received_bytes",
    "source_audio_accepted_bytes",
    "source_audio_received_ms",
    "source_audio_accepted_ms",
    "generated_audio_bytes",
    "forwarded_audio_bytes",
    "generated_audio_ms",
    "forwarded_audio_ms",
}


@dataclass
class MeasurementAccumulator:
    """Build exactly one terminal, content-free usage measurement.

    The accumulator is intentionally independent of HTTP/WebSocket lifecycle
    code. Callers may record provider-reported or Gateway-observed counts as
    they become available, then finalize on every terminal path, including a
    disconnect or ambiguous provider outcome. It never stores prompts, output,
    audio, transcripts, or account identifiers.
    """

    request_id: UUID
    workload_id: str
    product: str
    capability: Capability
    profile: str
    route_id: str
    started_at: datetime
    _counts: dict[str, MeasuredCount] = field(default_factory=dict)
    _final: UsageMeasurementEnvelope | None = None

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("measurement timestamps must be timezone-aware")

    def record_count(
        self,
        field: str,
        value: int,
        *,
        source: MeasurementSource,
        estimation_method: str | None = None,
    ) -> None:
        if self._final is not None:
            raise RuntimeError("measurement is already finalized")
        if field not in _COUNT_FIELDS:
            raise ValueError("unsupported measurement count")
        self._counts[field] = MeasuredCount(
            value=value, source=source, estimation_method=estimation_method
        )

    def finalize(
        self,
        disposition: MeasurementDisposition,
        *,
        finished_at: datetime | None = None,
        fallback_count: int = 0,
        rotation_count: int = 0,
    ) -> UsageMeasurementEnvelope:
        """Return the terminal event; repeated finalization is idempotent.

        A second call with a different terminal disposition is rejected to
        prevent conflicting usage records for one request/session.
        """
        if self._final is not None:
            if self._final.disposition != disposition:
                raise RuntimeError("measurement already finalized")
            return self._final
        end = finished_at or datetime.now(timezone.utc)
        values: dict[str, object] = {
            "event_id": measurement_event_id(
                request_id=self.request_id,
                capability=self.capability,
            ),
            "request_id": self.request_id,
            "workload_id": self.workload_id,
            "product": self.product,
            "capability": self.capability,
            "profile": self.profile,
            "route_id": self.route_id,
            "started_at": self.started_at,
            "finished_at": end,
            "disposition": disposition,
            "fallback_count": fallback_count,
            "rotation_count": rotation_count,
        }
        values.update(self._counts)
        self._final = UsageMeasurementEnvelope.model_validate(values)
        return self._final


def measurement_event_id(
    *,
    request_id: UUID,
    capability: Capability,
    version: str = MEASUREMENT_VERSION,
) -> UUID:
    return uuid5(_MEASUREMENT_NAMESPACE, f"{version}:{request_id}:{capability}")
