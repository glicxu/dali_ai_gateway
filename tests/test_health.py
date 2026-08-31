from __future__ import annotations

import asyncio

from app.core.health import ProviderHealthMonitor


class _Probe:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def probe(self) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("probe failed")


def test_health_monitor_tracks_independent_state_and_staleness() -> None:
    async def exercise() -> None:
        now = [100.0]
        healthy = _Probe()
        degraded = _Probe(fail=True)
        monitor = ProviderHealthMonitor(
            {"healthy": healthy, "degraded": degraded},
            timeout_seconds=0.1,
            interval_seconds=30,
            max_staleness_seconds=60,
            max_concurrency=1,
            clock=lambda: now[0],
        )

        assert monitor.status("healthy") == "unknown"
        await monitor.refresh()
        assert monitor.status("healthy") == "healthy"
        assert monitor.status("degraded") == "degraded"
        assert monitor.all_healthy({"healthy"})
        assert not monitor.all_healthy({"healthy", "degraded"})

        now[0] += 61
        assert monitor.status("healthy") == "stale"
        assert not monitor.all_healthy({"healthy"})

    asyncio.run(exercise())


def test_health_monitor_bounds_probe_timeout() -> None:
    class BlockingProbe:
        async def probe(self) -> None:
            await asyncio.Event().wait()

    async def exercise() -> None:
        monitor = ProviderHealthMonitor(
            {"blocked": BlockingProbe()},
            timeout_seconds=0.01,
            interval_seconds=30,
            max_staleness_seconds=60,
            max_concurrency=1,
        )
        await asyncio.wait_for(monitor.refresh(), timeout=0.5)
        assert monitor.status("blocked") == "degraded"

    asyncio.run(exercise())
