from __future__ import annotations

import asyncio

import pytest

from app.core.errors import GatewayError
from app.services import AdmissionController, InMemoryAdmissionStore


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
        assert await store.acquire(("dali_chat_server", "text_generation"), 1, 1) is not None
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
