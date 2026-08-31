from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from threading import Lock
from types import MappingProxyType
from typing import Callable, Literal, Mapping


ProviderHealthStatus = Literal["unknown", "healthy", "degraded", "stale"]


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    status: ProviderHealthStatus
    checked_at: float | None


class ProviderHealthMonitor:
    """Maintains content-free provider health outside request/readiness paths."""

    def __init__(
        self,
        providers: Mapping[str, object],
        *,
        timeout_seconds: float,
        interval_seconds: float,
        max_staleness_seconds: float,
        max_concurrency: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = dict(providers)
        self._timeout_seconds = timeout_seconds
        self._interval_seconds = interval_seconds
        self._max_staleness_seconds = max_staleness_seconds
        self._max_concurrency = max_concurrency
        self._clock = clock
        self._lock = Lock()
        self._states: Mapping[str, ProviderHealth] = MappingProxyType(
            {provider: ProviderHealth("unknown", None) for provider in self._providers}
        )
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        await self.refresh()
        self._task = asyncio.create_task(self._run(), name="provider-health-monitor")

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def refresh(self) -> None:
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def check(
            provider_name: str, provider: object
        ) -> tuple[str, ProviderHealth]:
            probe = getattr(provider, "probe", None)
            healthy = False
            if callable(probe):
                try:
                    async with semaphore:
                        await asyncio.wait_for(probe(), timeout=self._timeout_seconds)
                    healthy = True
                except Exception:
                    healthy = False
            return provider_name, ProviderHealth(
                "healthy" if healthy else "degraded",
                self._clock(),
            )

        results = await asyncio.gather(
            *(check(name, provider) for name, provider in self._providers.items())
        )
        with self._lock:
            self._states = MappingProxyType(dict(results))

    def status(self, provider: str) -> ProviderHealthStatus:
        with self._lock:
            state = self._states.get(provider)
        return self._effective_status(state, self._clock())

    def all_healthy(self, providers: set[str]) -> bool:
        now = self._clock()
        with self._lock:
            states = self._states
        return all(
            self._effective_status(states.get(provider), now) == "healthy"
            for provider in providers
        )

    def safe_counts(self) -> dict[ProviderHealthStatus, int]:
        counts: dict[ProviderHealthStatus, int] = {
            "unknown": 0,
            "healthy": 0,
            "degraded": 0,
            "stale": 0,
        }
        now = self._clock()
        with self._lock:
            states = self._states
        for provider in self._providers:
            counts[self._effective_status(states.get(provider), now)] += 1
        return counts

    def _effective_status(
        self, state: ProviderHealth | None, now: float
    ) -> ProviderHealthStatus:
        if state is None or state.checked_at is None:
            return "unknown"
        if now - state.checked_at > self._max_staleness_seconds:
            return "stale"
        return state.status

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval_seconds)
                await self.refresh()
        except asyncio.CancelledError:
            raise
