from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

import boto3


class DynamoDbCircuitStore:
    """Content-free circuit state shared by every Gateway replica."""

    def __init__(
        self,
        *,
        table_name: str,
        region_name: str,
        failure_threshold: int,
        open_seconds: float,
        client: Any | None = None,
    ) -> None:
        if (
            not table_name
            or not region_name
            or failure_threshold < 1
            or open_seconds <= 0
        ):
            raise ValueError("shared circuit configuration is invalid")
        self._table_name = table_name
        self._failure_threshold = failure_threshold
        self._open_milliseconds = max(1, int(open_seconds * 1000))
        self._client = client or boto3.client("dynamodb", region_name=region_name)

    async def allow(self, route_id: str) -> bool:
        response = await asyncio.to_thread(
            self._client.get_item,
            TableName=self._table_name,
            Key={"state_key": {"S": self._key(route_id)}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item or item.get("state", {}).get("S") != "open":
            return True
        return int(item.get("open_until_ms", {"N": "0"})["N"]) <= self._now_ms()

    async def record_success(self, route_id: str) -> None:
        await asyncio.to_thread(
            self._client.update_item,
            TableName=self._table_name,
            Key={"state_key": {"S": self._key(route_id)}},
            UpdateExpression=(
                "SET #state = :healthy, consecutive_failures = :zero, "
                "open_until_ms = :zero, updated_at_ms = :now"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":healthy": {"S": "healthy"},
                ":zero": {"N": "0"},
                ":now": {"N": str(self._now_ms())},
            },
        )

    async def record_failure(self, route_id: str) -> None:
        now = self._now_ms()
        response = await asyncio.to_thread(
            self._client.update_item,
            TableName=self._table_name,
            Key={"state_key": {"S": self._key(route_id)}},
            UpdateExpression=(
                "ADD consecutive_failures :one "
                "SET #state = :degraded, updated_at_ms = :now"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":one": {"N": "1"},
                ":degraded": {"S": "degraded"},
                ":now": {"N": str(now)},
            },
            ReturnValues="ALL_NEW",
        )
        failures = int(response["Attributes"]["consecutive_failures"]["N"])
        if failures >= self._failure_threshold:
            await asyncio.to_thread(
                self._client.update_item,
                TableName=self._table_name,
                Key={"state_key": {"S": self._key(route_id)}},
                UpdateExpression=(
                    "SET #state = :open, open_until_ms = :until, updated_at_ms = :now"
                ),
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues={
                    ":open": {"S": "open"},
                    ":until": {"N": str(now + self._open_milliseconds)},
                    ":now": {"N": str(now)},
                },
            )

    @staticmethod
    def _key(route_id: str) -> str:
        digest = hashlib.sha256(route_id.encode("utf-8")).hexdigest()
        return f"circuit#{digest}"

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
