from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from collections import defaultdict
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError


class DynamoDbAdmissionStore:
    """Replica-safe bounded-slot leases backed by one DynamoDB table.

    The table needs a string partition key named ``state_key`` and DynamoDB TTL
    enabled on ``expires_at``. Conditional writes provide atomic admission;
    expiration checks do not depend on the asynchronous TTL deletion process.
    """

    _PREFIX = "admission#"

    def __init__(
        self,
        *,
        table_name: str,
        region_name: str,
        client: Any | None = None,
    ) -> None:
        if not table_name or not region_name:
            raise ValueError("shared admission table and region are required")
        self._table_name = table_name
        self._client = client or boto3.client("dynamodb", region_name=region_name)

    async def acquire(self, key: tuple[str, ...], limit: int, ttl: float) -> str | None:
        if limit < 1 or limit > 10_000 or ttl <= 0:
            raise ValueError("admission limit and lease TTL are invalid")
        lease_token = str(uuid4())
        now = int(time.time())
        expires_at = now + max(1, int(ttl + 0.999))
        dimensions = json.dumps(key, separators=(",", ":"))
        digest = hashlib.sha256(dimensions.encode("utf-8")).hexdigest()
        start = int(hashlib.sha256(lease_token.encode()).hexdigest(), 16) % limit
        for offset in range(limit):
            slot = (start + offset) % limit
            state_key = f"{self._PREFIX}{digest}#{slot}"
            try:
                await asyncio.to_thread(
                    self._client.put_item,
                    TableName=self._table_name,
                    Item={
                        "state_key": {"S": state_key},
                        "lease_id": {"S": lease_token},
                        "dimensions": {"S": dimensions},
                        "expires_at": {"N": str(expires_at)},
                    },
                    ConditionExpression=(
                        "attribute_not_exists(state_key) OR expires_at < :now"
                    ),
                    ExpressionAttributeValues={":now": {"N": str(now)}},
                )
                return self._encode_lease(state_key, lease_token)
            except ClientError as error:
                if self._error_code(error) != "ConditionalCheckFailedException":
                    raise
        return None

    async def renew(self, lease_id: str, ttl: float) -> bool:
        if ttl <= 0:
            raise ValueError("lease TTL must be positive")
        state_key, lease_token = self._decode_lease(lease_id)
        expires_at = int(time.time()) + max(1, int(ttl + 0.999))
        try:
            await asyncio.to_thread(
                self._client.update_item,
                TableName=self._table_name,
                Key={"state_key": {"S": state_key}},
                UpdateExpression="SET expires_at = :expires_at",
                ConditionExpression="lease_id = :lease_id",
                ExpressionAttributeValues={
                    ":expires_at": {"N": str(expires_at)},
                    ":lease_id": {"S": lease_token},
                },
            )
            return True
        except ClientError as error:
            if self._error_code(error) == "ConditionalCheckFailedException":
                return False
            raise

    async def release(self, lease_id: str) -> None:
        state_key, lease_token = self._decode_lease(lease_id)
        try:
            await asyncio.to_thread(
                self._client.delete_item,
                TableName=self._table_name,
                Key={"state_key": {"S": state_key}},
                ConditionExpression="lease_id = :lease_id",
                ExpressionAttributeValues={":lease_id": {"S": lease_token}},
            )
        except ClientError as error:
            if self._error_code(error) != "ConditionalCheckFailedException":
                raise

    async def recover_expired(self) -> int:
        now = int(time.time())
        items = await self._scan(now=None)
        expired = [item for item in items if int(item["expires_at"]["N"]) <= now]
        recovered = 0
        for item in expired:
            lease_id = self._encode_lease(item["state_key"]["S"], item["lease_id"]["S"])
            await self.release(lease_id)
            recovered += 1
        return recovered

    async def inspect(self) -> dict[tuple[str, ...], int]:
        now = int(time.time())
        counts: defaultdict[tuple[str, ...], int] = defaultdict(int)
        for item in await self._scan(now=now):
            dimensions = json.loads(item["dimensions"]["S"])
            if isinstance(dimensions, list) and all(
                isinstance(value, str) for value in dimensions
            ):
                counts[tuple(dimensions)] += 1
        return dict(counts)

    async def _scan(self, *, now: int | None) -> list[dict[str, Any]]:
        def scan() -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            start_key = None
            while True:
                values: dict[str, Any] = {":prefix": {"S": self._PREFIX}}
                expression = "begins_with(state_key, :prefix)"
                if now is not None:
                    expression += " AND expires_at > :now"
                    values[":now"] = {"N": str(now)}
                arguments: dict[str, Any] = {
                    "TableName": self._table_name,
                    "FilterExpression": expression,
                    "ExpressionAttributeValues": values,
                    "ProjectionExpression": (
                        "state_key, lease_id, dimensions, expires_at"
                    ),
                }
                if start_key is not None:
                    arguments["ExclusiveStartKey"] = start_key
                response = self._client.scan(**arguments)
                items.extend(response.get("Items", []))
                start_key = response.get("LastEvaluatedKey")
                if not start_key:
                    return items

        return await asyncio.to_thread(scan)

    @staticmethod
    def _encode_lease(state_key: str, lease_token: str) -> str:
        value = json.dumps([state_key, lease_token], separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode_lease(lease_id: str) -> tuple[str, str]:
        try:
            padding = "=" * (-len(lease_id) % 4)
            value = json.loads(base64.urlsafe_b64decode(lease_id + padding))
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid admission lease") from exc
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(isinstance(item, str) and item for item in value)
            or not value[0].startswith(DynamoDbAdmissionStore._PREFIX)
        ):
            raise ValueError("invalid admission lease")
        return value[0], value[1]

    @staticmethod
    def _error_code(error: ClientError) -> str:
        return str(error.response.get("Error", {}).get("Code", ""))
