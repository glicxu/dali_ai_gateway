from __future__ import annotations

import asyncio
from threading import Lock

import pytest
from botocore.exceptions import ClientError

from app.core.errors import GatewayError
from app.core.shared_admission import DynamoDbAdmissionStore
from app.core.execution_claims import DynamoDbExecutionClaimStore
from app.services import AdmissionController, InMemoryAdmissionStore


class _FakeDynamoDb:
    def __init__(self) -> None:
        self.items = {}
        self.lock = Lock()

    @staticmethod
    def _conditional() -> ClientError:
        return ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "write"
        )

    def put_item(self, **values) -> None:
        item = values["Item"]
        key = item["state_key"]["S"]
        now = int(values["ExpressionAttributeValues"][":now"]["N"])
        with self.lock:
            current = self.items.get(key)
            if current is not None and int(current["expires_at"]["N"]) >= now:
                raise self._conditional()
            self.items[key] = item

    def update_item(self, **values) -> None:
        key = values["Key"]["state_key"]["S"]
        expected = values["ExpressionAttributeValues"][":lease_id"]["S"]
        with self.lock:
            current = self.items.get(key)
            if current is None or current["lease_id"]["S"] != expected:
                raise self._conditional()
            current["expires_at"] = values["ExpressionAttributeValues"][":expires_at"]

    def delete_item(self, **values) -> None:
        key = values["Key"]["state_key"]["S"]
        expected = values["ExpressionAttributeValues"][":lease_id"]["S"]
        with self.lock:
            current = self.items.get(key)
            if current is None or current["lease_id"]["S"] != expected:
                raise self._conditional()
            del self.items[key]

    def scan(self, **_values):
        with self.lock:
            return {"Items": list(self.items.values())}


def test_per_caller_capacity_rejects_before_provider_work() -> None:
    async def exercise() -> None:
        controller = AdmissionController({"dali_classroom_server": 1})
        async with controller.lease("dali_classroom_server"):
            with pytest.raises(GatewayError) as captured:
                async with controller.lease("dali_classroom_server"):
                    pass
            assert captured.value.code == "ai_gateway_capacity_exceeded"

    asyncio.run(exercise())


def test_capabilities_have_independent_capacity_pools() -> None:
    async def exercise() -> None:
        controller = AdmissionController({"dali_classroom_server": 1})
        async with controller.lease("dali_classroom_server", "realtime_transcription"):
            async with controller.lease("dali_classroom_server", "text_generation"):
                pass

    asyncio.run(exercise())


def test_injected_store_recovers_expired_leases_and_supports_inspection() -> None:
    async def exercise() -> None:
        store = InMemoryAdmissionStore()
        lease_id = await store.acquire(("dali_chat_server", "text_generation"), 1, 0.01)
        assert lease_id is not None
        assert await store.inspect() == {("dali_chat_server", "text_generation"): 1}
        await asyncio.sleep(0.02)
        assert (
            await store.acquire(("dali_chat_server", "text_generation"), 1, 1)
            is not None
        )
        assert await store.renew(lease_id, 1) is False

    asyncio.run(exercise())


def test_controller_uses_injected_store_atomically() -> None:
    async def exercise() -> None:
        store = InMemoryAdmissionStore()
        controller = AdmissionController(
            {"dali_chat_server": 1}, store=store, lease_ttl_seconds=1
        )
        async with controller.lease("dali_chat_server", "text_generation"):
            assert await store.inspect() == {("dali_chat_server", "text_generation"): 1}
            with pytest.raises(GatewayError):
                async with controller.lease("dali_chat_server", "text_generation"):
                    pass
        assert await store.inspect() == {}

    asyncio.run(exercise())


def test_controller_renews_long_running_lease_until_context_exit() -> None:
    async def exercise() -> None:
        store = InMemoryAdmissionStore()
        controller = AdmissionController(
            {"dali_chat_server": 1}, store=store, lease_ttl_seconds=0.1
        )
        async with controller.lease("dali_chat_server", "realtime_translation"):
            await asyncio.sleep(0.25)
            assert await store.inspect() == {
                ("dali_chat_server", "realtime_translation"): 1
            }
        assert await store.inspect() == {}

    asyncio.run(exercise())


def test_product_and_profile_dimensions_can_have_precise_limits() -> None:
    async def exercise() -> None:
        controller = AdmissionController(
            {
                "dali_chat_server": 2,
                "dali_chat_server:chat:chat.text.standard:text_generation": 1,
            }
        )
        async with controller.lease(
            "dali_chat_server",
            "text_generation",
            product="chat",
            profile="chat.text.standard",
        ):
            with pytest.raises(GatewayError):
                async with controller.lease(
                    "dali_chat_server",
                    "text_generation",
                    product="chat",
                    profile="chat.text.standard",
                ):
                    pass
            async with controller.lease(
                "dali_chat_server",
                "text_generation",
                product="other",
                profile="other.text.standard",
            ):
                pass

    asyncio.run(exercise())


def test_named_capacity_pool_is_shared_across_workloads_and_profiles() -> None:
    async def exercise() -> None:
        controller = AdmissionController(
            {"shared-realtime:priority:realtime_translation": 1}
        )
        async with controller.lease(
            "workload-a",
            "realtime_translation",
            product="product-a",
            profile="a.live",
            capacity_pool="shared-realtime",
            traffic_class="priority",
        ):
            with pytest.raises(GatewayError) as captured:
                async with controller.lease(
                    "workload-b",
                    "realtime_translation",
                    product="product-b",
                    profile="b.live",
                    capacity_pool="shared-realtime",
                    traffic_class="priority",
                ):
                    pass
            assert captured.value.code == "ai_gateway_capacity_exceeded"
            async with controller.lease(
                "workload-b",
                "realtime_translation",
                product="product-b",
                profile="b.live",
                capacity_pool="reserved-realtime",
                traffic_class="priority",
            ):
                pass

    asyncio.run(exercise())


def test_dynamodb_store_enforces_one_atomic_limit_across_replicas() -> None:
    async def exercise() -> None:
        client = _FakeDynamoDb()
        first = DynamoDbAdmissionStore(
            table_name="gateway-state", region_name="us-west-2", client=client
        )
        second = DynamoDbAdmissionStore(
            table_name="gateway-state", region_name="us-west-2", client=client
        )
        key = ("interpreter_server_ai", "interprete", "text_generation")
        lease = await first.acquire(key, 1, 30)
        assert lease is not None
        assert await second.acquire(key, 1, 30) is None
        assert await second.inspect() == {key: 1}
        assert await first.renew(lease, 30)
        await first.release(lease)
        assert await second.acquire(key, 1, 30) is not None

    asyncio.run(exercise())


def test_dynamodb_execution_claim_prevents_cross_replica_replay() -> None:
    async def exercise() -> None:
        client = _FakeDynamoDb()
        first = DynamoDbExecutionClaimStore(
            table_name="gateway-state", region_name="us-west-2", client=client
        )
        second = DynamoDbExecutionClaimStore(
            table_name="gateway-state", region_name="us-west-2", client=client
        )
        assert await first.claim("request-1", "text_generation", 300)
        assert not await second.claim("request-1", "text_generation", 300)
        assert await second.claim("request-1", "audio_transcription", 300)

    asyncio.run(exercise())
