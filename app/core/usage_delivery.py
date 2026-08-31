from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol

from app.core.measurement import UsageMeasurementEnvelope


UsagePutResult = Literal["accepted", "duplicate"]


class UsageConflictError(Exception):
    """The event ID already exists with a different content-free payload."""


class UsageDeliveryError(Exception):
    """The configured durable sink did not confirm the measurement."""


class DurableUsageSink(Protocol):
    async def put(self, measurement: UsageMeasurementEnvelope) -> UsagePutResult: ...


class SqsClient(Protocol):
    def send_message(self, **kwargs: object) -> dict[str, Any]: ...


class SqsUsageSink:
    """Content-free durable delivery to an AWS SQS Standard queue.

    SQS Standard provides at-least-once delivery. The downstream product-owned
    relay must therefore deduplicate by the canonical event_id in the envelope.
    """

    def __init__(
        self,
        *,
        queue_url: str,
        region_name: str,
        client: SqsClient | None = None,
    ) -> None:
        if not queue_url.startswith("https://") or queue_url.endswith(".fifo"):
            raise ValueError("usage sink requires an HTTPS SQS Standard queue URL")
        if not region_name.strip():
            raise ValueError("usage sink requires an AWS region")
        self._queue_url = queue_url
        self._client = client or _new_sqs_client(region_name)

    async def put(self, measurement: UsageMeasurementEnvelope) -> UsagePutResult:
        body = json.dumps(
            measurement.model_dump(mode="json"),
            separators=(",", ":"),
            sort_keys=True,
        )
        response = await asyncio.to_thread(
            self._client.send_message,
            QueueUrl=self._queue_url,
            MessageBody=body,
            MessageAttributes={
                "event_id": {
                    "DataType": "String",
                    "StringValue": str(measurement.event_id),
                },
                "schema_version": {
                    "DataType": "String",
                    "StringValue": measurement.version,
                },
            },
        )
        metadata = response.get("ResponseMetadata")
        status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
        if status != 200 or not response.get("MessageId"):
            raise UsageDeliveryError("SQS did not confirm usage measurement")
        return "accepted"


def _new_sqs_client(region_name: str) -> SqsClient:
    import boto3

    return boto3.client("sqs", region_name=region_name)


@dataclass(frozen=True, slots=True)
class UsageDeliveryOutcome:
    status: Literal["accepted", "duplicate"]
    attempts: int


class UsageDelivery:
    """Bounded idempotent delivery; it never buffers measurements durably."""

    def __init__(
        self,
        sink: DurableUsageSink,
        *,
        max_attempts: int,
        retry_delay_seconds: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("usage delivery attempts must be between one and five")
        if retry_delay_seconds < 0 or retry_delay_seconds > 10:
            raise ValueError("usage delivery retry delay is invalid")
        self._sink = sink
        self._max_attempts = max_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._sleep = sleep

    async def deliver(
        self, measurement: UsageMeasurementEnvelope
    ) -> UsageDeliveryOutcome:
        for attempt in range(1, self._max_attempts + 1):
            try:
                result = await self._sink.put(measurement)
                if result not in ("accepted", "duplicate"):
                    raise UsageDeliveryError("usage sink returned an invalid outcome")
                return UsageDeliveryOutcome(status=result, attempts=attempt)
            except UsageConflictError:
                raise
            except Exception as error:
                if attempt == self._max_attempts:
                    raise UsageDeliveryError(
                        "usage delivery was not confirmed"
                    ) from error
                await self._sleep(self._retry_delay_seconds)
        raise AssertionError("bounded usage delivery loop did not terminate")
