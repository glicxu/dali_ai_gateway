from __future__ import annotations

import asyncio

import pytest

from app.core.circuit import CircuitRegistry, RouteCircuit
from app.core.errors import GatewayError


def test_circuit_opens_after_threshold_and_recovers_at_deadline() -> None:
    circuit = RouteCircuit(failure_threshold=2, open_seconds=10)
    assert circuit.allow(now=100)
    circuit.record_failure(now=100)
    assert circuit.snapshot().state == "degraded"
    circuit.record_failure(now=101)
    assert circuit.snapshot().state == "open"
    assert not circuit.allow(now=109)
    assert circuit.allow(now=111)
    assert circuit.snapshot().state == "degraded"


def test_success_closes_degraded_state_and_disable_is_explicit() -> None:
    circuit = RouteCircuit()
    circuit.record_failure()
    circuit.record_success()
    assert circuit.snapshot().state == "healthy"
    circuit.disable()
    assert not circuit.allow()
    circuit.record_success()
    assert circuit.snapshot().state == "disabled"
    circuit.enable()
    assert circuit.allow()
    assert circuit.snapshot().state == "configured"


def test_circuit_configuration_is_bounded() -> None:
    with pytest.raises(ValueError):
        RouteCircuit(failure_threshold=0)
    with pytest.raises(ValueError):
        RouteCircuit(open_seconds=0)


def test_registry_isolates_routes_and_blocks_disabled_route() -> None:
    async def exercise() -> None:
        registry = CircuitRegistry(
            enabled=True,
            failure_threshold=1,
            disabled_routes=frozenset({"gemini.disabled"}),
        )
        with pytest.raises(GatewayError) as captured:
            async with registry.call("gemini.disabled"):
                pass
        assert captured.value.code == "ai_gateway_provider_unavailable"

        with pytest.raises(RuntimeError):
            async with registry.call("openai.primary"):
                raise RuntimeError("normalized provider failure")
        assert registry.snapshot("openai.primary").state == "open"
        assert registry.snapshot("gemini.other").state == "configured"

    asyncio.run(exercise())
