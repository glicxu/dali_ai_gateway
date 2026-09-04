from __future__ import annotations

import time
from math import ceil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol

from app.core.errors import PROVIDER_UNAVAILABLE


CircuitState = Literal["configured", "healthy", "degraded", "open", "disabled"]


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    state: CircuitState
    consecutive_failures: int
    open_until: float | None


class SharedCircuitStore(Protocol):
    async def allow(self, route_id: str) -> bool: ...

    async def record_success(self, route_id: str) -> None: ...

    async def record_failure(self, route_id: str) -> None: ...


class RouteCircuit:
    """Small deterministic route circuit; no provider payloads are retained."""

    def __init__(self, *, failure_threshold: int = 3, open_seconds: float = 30) -> None:
        if failure_threshold < 1 or open_seconds <= 0:
            raise ValueError("circuit thresholds must be positive")
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds
        self._state: CircuitState = "configured"
        self._consecutive_failures = 0
        self._open_until: float | None = None

    def allow(self, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        if self._state == "disabled":
            return False
        if self._state == "open":
            if self._open_until is None or current < self._open_until:
                return False
            self._state = "degraded"
            self._open_until = None
        return True

    def record_success(self) -> None:
        if self._state == "disabled":
            return
        self._state = "healthy"
        self._consecutive_failures = 0
        self._open_until = None

    def record_failure(self, *, now: float | None = None) -> None:
        if self._state == "disabled":
            return
        self._consecutive_failures = min(
            self._failure_threshold, self._consecutive_failures + 1
        )
        current = time.monotonic() if now is None else now
        if self._consecutive_failures >= self._failure_threshold:
            self._state = "open"
            self._open_until = current + self._open_seconds
        else:
            self._state = "degraded"

    def disable(self) -> None:
        self._state = "disabled"
        self._open_until = None

    def enable(self) -> None:
        if self._state == "disabled":
            self._state = "configured"
            self._consecutive_failures = 0
            self._open_until = None

    def snapshot(self) -> CircuitSnapshot:
        return CircuitSnapshot(
            state=self._state,
            consecutive_failures=self._consecutive_failures,
            open_until=self._open_until,
        )


class CircuitRegistry:
    """Isolates circuit state by normalized provider route identifier."""

    def __init__(
        self,
        *,
        enabled: bool,
        failure_threshold: int = 3,
        open_seconds: float = 30,
        disabled_routes: frozenset[str] = frozenset(),
        shared_store: SharedCircuitStore | None = None,
    ) -> None:
        if failure_threshold < 1 or open_seconds <= 0:
            raise ValueError("circuit thresholds must be positive")
        self._enabled = enabled
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds
        self._circuits: dict[str, RouteCircuit] = {}
        self._shared_store = shared_store
        for route_id in disabled_routes:
            self._circuit(route_id).disable()

    def allow(self, route_id: str) -> bool:
        if not self._enabled:
            return True
        return self._circuit(route_id).allow()

    def record_success(self, route_id: str) -> None:
        if self._enabled:
            self._circuit(route_id).record_success()

    def record_failure(self, route_id: str) -> None:
        if self._enabled:
            self._circuit(route_id).record_failure()

    def snapshot(self, route_id: str) -> CircuitSnapshot:
        return self._circuit(route_id).snapshot()

    def retry_after_ms(self, route_id: str) -> int | None:
        """Return a safe local retry delay without exposing provider details."""
        if not self._enabled:
            return None
        open_until = self.snapshot(route_id).open_until
        if open_until is None:
            return None
        return max(0, ceil((open_until - time.monotonic()) * 1000))

    @asynccontextmanager
    async def call(self, route_id: str) -> AsyncIterator[None]:
        if not self.allow(route_id) or (
            self._shared_store is not None
            and not await self._shared_store.allow(route_id)
        ):
            raise PROVIDER_UNAVAILABLE
        try:
            yield
        except Exception:
            self.record_failure(route_id)
            if self._shared_store is not None:
                await self._shared_store.record_failure(route_id)
            raise
        else:
            self.record_success(route_id)
            if self._shared_store is not None:
                await self._shared_store.record_success(route_id)

    async def record_shared_failure(self, route_id: str) -> None:
        self.record_failure(route_id)
        if self._shared_store is not None:
            await self._shared_store.record_failure(route_id)

    async def check_shared_ready(self) -> bool:
        if self._shared_store is None:
            return True
        return await self._shared_store.allow("gateway.readiness.probe")

    def _circuit(self, route_id: str) -> RouteCircuit:
        if route_id not in self._circuits:
            self._circuits[route_id] = RouteCircuit(
                failure_threshold=self._failure_threshold,
                open_seconds=self._open_seconds,
            )
        return self._circuits[route_id]
