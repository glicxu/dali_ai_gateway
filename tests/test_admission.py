from __future__ import annotations

import asyncio

import pytest

from app.core.errors import GatewayError
from app.services import AdmissionController


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
        async with controller.lease(
            "dali_classroom_server", "realtime_transcription"
        ):
            async with controller.lease(
                "dali_classroom_server", "text_generation"
            ):
                pass

    asyncio.run(exercise())
