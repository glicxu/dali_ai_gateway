from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.measurement import (
    MeasuredCount,
    MeasurementAccumulator,
    UsageMeasurementEnvelope,
    measurement_event_id,
)


def _measurement(**overrides: object) -> UsageMeasurementEnvelope:
    request_id = overrides.pop("request_id", uuid4())
    assert isinstance(request_id, type(uuid4()))
    started_at = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    values = {
        "event_id": measurement_event_id(
            request_id=request_id,
            capability="text_generation",
        ),
        "request_id": request_id,
        "workload_id": "dali_classroom_server",
        "product": "classroom",
        "capability": "text_generation",
        "profile": "classroom.translation.economy",
        "route_id": "gemini.primary",
        "started_at": started_at,
        "finished_at": started_at + timedelta(milliseconds=25),
        "disposition": "complete",
        "input_tokens": {
            "value": 7,
            "source": "provider_reported",
        },
        "output_tokens": {
            "value": 3,
            "source": "provider_reported",
        },
        **overrides,
    }
    return UsageMeasurementEnvelope.model_validate(values)


def test_measurement_event_id_is_stable_and_capability_scoped() -> None:
    request_id = uuid4()
    first = measurement_event_id(
        request_id=request_id,
        capability="text_generation",
    )
    assert first == measurement_event_id(
        request_id=request_id,
        capability="text_generation",
    )
    assert first != measurement_event_id(
        request_id=request_id,
        capability="audio_transcription",
    )


def test_measurement_distinguishes_exact_estimated_and_unavailable_values() -> None:
    assert MeasuredCount().source == "unavailable"
    estimated = MeasuredCount(
        value=320,
        source="estimated",
        estimation_method="pcm16.v1",
    )
    assert estimated.value == 320

    with pytest.raises(ValidationError, match="requires a method"):
        MeasuredCount(value=320, source="estimated")
    with pytest.raises(ValidationError, match="cannot contain a value"):
        MeasuredCount(value=1, source="unavailable")


def test_measurement_has_separate_received_accepted_and_generated_audio() -> None:
    measurement = _measurement(
        source_audio_received_bytes={"value": 1000, "source": "gateway_observed"},
        source_audio_accepted_bytes={"value": 800, "source": "gateway_observed"},
        generated_audio_bytes={"value": 600, "source": "provider_reported"},
        forwarded_audio_bytes={"value": 400, "source": "gateway_observed"},
    )
    assert measurement.source_audio_received_bytes.value == 1000
    assert measurement.source_audio_accepted_bytes.value == 800
    assert measurement.generated_audio_bytes.value == 600
    assert measurement.forwarded_audio_bytes.value == 400


def test_measurement_rejects_content_and_account_fields() -> None:
    for forbidden in ("user_id", "account_id", "prompt", "transcript", "audio"):
        with pytest.raises(ValidationError):
            _measurement(**{forbidden: "private-value"})


def test_measurement_rejects_noncanonical_event_id_and_naive_time() -> None:
    with pytest.raises(ValidationError, match="canonical"):
        _measurement(event_id=uuid4())
    with pytest.raises(ValidationError, match="timezone-aware"):
        _measurement(started_at=datetime(2026, 8, 31, 12))


def test_accumulator_finalizes_partial_disconnect_without_fabricating_counts() -> None:
    request_id = uuid4()
    started = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    accumulator = MeasurementAccumulator(
        request_id=request_id,
        workload_id="dali_chat_server",
        product="chat",
        capability="realtime_transcription",
        profile="chat.realtime.standard",
        route_id="openai.realtime.primary",
        started_at=started,
    )
    accumulator.record_count(
        "source_audio_received_bytes", 1200, source="gateway_observed"
    )
    final = accumulator.finalize(
        "disconnected",
        finished_at=started + timedelta(seconds=2),
    )
    assert final.disposition == "disconnected"
    assert final.source_audio_received_bytes.value == 1200
    assert final.source_audio_accepted_bytes.source == "unavailable"
    assert final.input_tokens.source == "unavailable"


def test_accumulator_finalization_is_idempotent_but_conflicts_are_rejected() -> None:
    started = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    accumulator = MeasurementAccumulator(
        request_id=uuid4(),
        workload_id="dali_classroom_server",
        product="classroom",
        capability="text_generation",
        profile="classroom.summary.standard",
        route_id="gemini.primary",
        started_at=started,
    )
    first = accumulator.finalize("ambiguous", finished_at=started)
    assert accumulator.finalize("ambiguous", finished_at=started) is first
    with pytest.raises(RuntimeError, match="already finalized"):
        accumulator.finalize("complete", finished_at=started)
    with pytest.raises(RuntimeError, match="already finalized"):
        accumulator.record_count("output_tokens", 1, source="provider_reported")
