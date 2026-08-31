from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.core.measurement import UsageMeasurementEnvelope, measurement_event_id
from app.core.usage_delivery import (
    SqsUsageSink,
    UsageConflictError,
    UsageDelivery,
    UsageDeliveryError,
)


def _measurement(*, output_tokens: int = 3) -> UsageMeasurementEnvelope:
    request_id = UUID("5a3f02f8-e7e7-4af7-b70f-f8ba8cb53d87")
    now = datetime(2026, 8, 31, 19, tzinfo=timezone.utc)
    return UsageMeasurementEnvelope(
        event_id=measurement_event_id(
            request_id=request_id,
            capability="text_generation",
        ),
        request_id=request_id,
        workload_id="dali_classroom_server",
        product="classroom",
        capability="text_generation",
        profile="classroom.translation.economy",
        route_id="gemini.primary",
        started_at=now,
        finished_at=now,
        disposition="complete",
        output_tokens={"value": output_tokens, "source": "provider_reported"},
    )


class _IdempotentSink:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.failures_remaining = 0

    async def put(self, measurement: UsageMeasurementEnvelope):
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("temporary sink failure")
        event_id = str(measurement.event_id)
        payload = json.dumps(
            measurement.model_dump(mode="json"),
            separators=(",", ":"),
            sort_keys=True,
        )
        existing = self.values.get(event_id)
        if existing is None:
            self.values[event_id] = payload
            return "accepted"
        if existing == payload:
            return "duplicate"
        raise UsageConflictError("same event ID has a conflicting payload")


def test_usage_delivery_is_idempotent_and_conflict_safe() -> None:
    async def exercise() -> None:
        sink = _IdempotentSink()
        delivery = UsageDelivery(sink, max_attempts=2, retry_delay_seconds=0)
        measurement = _measurement()

        assert (await delivery.deliver(measurement)).status == "accepted"
        assert (await delivery.deliver(measurement)).status == "duplicate"
        with pytest.raises(UsageConflictError):
            await delivery.deliver(_measurement(output_tokens=4))

    asyncio.run(exercise())


class _SqsClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def send_message(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return self.response


def test_sqs_sink_sends_canonical_content_free_measurement() -> None:
    async def exercise() -> None:
        client = _SqsClient(
            {"MessageId": "message-1", "ResponseMetadata": {"HTTPStatusCode": 200}}
        )
        sink = SqsUsageSink(
            queue_url="https://sqs.us-west-2.amazonaws.com/123456789012/dali-usage",
            region_name="us-west-2",
            client=client,
        )

        assert await sink.put(_measurement()) == "accepted"
        payload = json.loads(str(client.calls[0]["MessageBody"]))
        assert payload["event_id"] == str(_measurement().event_id)
        assert set(payload).isdisjoint({"account_id", "user_id", "prompt", "audio"})
        assert "MessageGroupId" not in client.calls[0]

    asyncio.run(exercise())


def test_sqs_sink_requires_delivery_confirmation() -> None:
    async def exercise() -> None:
        sink = SqsUsageSink(
            queue_url="https://sqs.us-west-2.amazonaws.com/123456789012/dali-usage",
            region_name="us-west-2",
            client=_SqsClient({"ResponseMetadata": {"HTTPStatusCode": 500}}),
        )
        with pytest.raises(UsageDeliveryError, match="did not confirm"):
            await sink.put(_measurement())

    asyncio.run(exercise())


def test_usage_delivery_retries_boundedly_and_never_silently_succeeds() -> None:
    async def exercise() -> None:
        sink = _IdempotentSink()
        sink.failures_remaining = 1
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)

        delivery = UsageDelivery(
            sink,
            max_attempts=2,
            retry_delay_seconds=0.25,
            sleep=sleep,
        )
        outcome = await delivery.deliver(_measurement())
        assert outcome.attempts == 2
        assert delays == [0.25]

        sink.failures_remaining = 3
        with pytest.raises(UsageDeliveryError, match="not confirmed"):
            await delivery.deliver(_measurement(output_tokens=5))

    asyncio.run(exercise())
