from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, Protocol

import boto3
from botocore.exceptions import ClientError


class ExecutionClaimStore(Protocol):
    async def claim(
        self, request_id: str, capability: str, ttl_seconds: int
    ) -> bool: ...


class InMemoryExecutionClaimStore:
    def __init__(self) -> None:
        self._claims: dict[tuple[str, str], float] = {}
        self._lock = asyncio.Lock()

    async def claim(self, request_id: str, capability: str, ttl_seconds: int) -> bool:
        now = time.monotonic()
        key = (request_id, capability)
        async with self._lock:
            self._claims = {
                item: expiry for item, expiry in self._claims.items() if expiry > now
            }
            if key in self._claims:
                return False
            self._claims[key] = now + ttl_seconds
            return True


class DynamoDbExecutionClaimStore:
    """Content-free request claims preventing duplicate provider execution."""

    def __init__(
        self, *, table_name: str, region_name: str, client: Any | None = None
    ) -> None:
        if not table_name or not region_name:
            raise ValueError("execution claim table and region are required")
        self._table = table_name
        self._client = client or boto3.client("dynamodb", region_name=region_name)

    async def claim(self, request_id: str, capability: str, ttl_seconds: int) -> bool:
        digest = hashlib.sha256(f"{request_id}:{capability}".encode()).hexdigest()
        now = int(time.time())
        try:
            await asyncio.to_thread(
                self._client.put_item,
                TableName=self._table,
                Item={
                    "state_key": {"S": f"execution#{digest}"},
                    "expires_at": {"N": str(now + ttl_seconds)},
                },
                ConditionExpression="attribute_not_exists(state_key) OR expires_at < :now",
                ExpressionAttributeValues={":now": {"N": str(now)}},
            )
            return True
        except ClientError as error:
            if (
                error.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                return False
            raise
