from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Mapping
from threading import Lock
from types import MappingProxyType
from typing import Callable

import httpx
import jwt

from app.core.errors import (
    AUTHENTICATION_INVALID,
    AUTHENTICATION_REQUIRED,
    GatewayError,
)
from app.core.security import (
    CALLER_PATTERN,
    ServiceAuthenticator,
    WorkloadPrincipal,
)


_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,127}$")


class JwksCache:
    """Bounded last-known-good JWKS cache with unknown-key refresh coalescing."""

    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: float,
        refresh_interval_seconds: float,
        max_staleness_seconds: float,
        unknown_key_cooldown_seconds: float,
        max_response_bytes: int = 256 * 1024,
        max_keys: int = 8,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._refresh_interval_seconds = refresh_interval_seconds
        self._max_staleness_seconds = max_staleness_seconds
        self._unknown_key_cooldown_seconds = unknown_key_cooldown_seconds
        self._max_response_bytes = max_response_bytes
        self._max_keys = max_keys
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._clock = clock
        self._state_lock = Lock()
        self._refresh_lock = asyncio.Lock()
        self._keys: Mapping[str, object] = MappingProxyType({})
        self._refreshed_at: float | None = None
        self._last_forced_refresh_at: float | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def ready(self) -> bool:
        with self._state_lock:
            return bool(self._keys) and self._is_fresh(self._refreshed_at)

    async def start(self) -> None:
        if self._task is not None:
            return
        await self.refresh()
        self._task = asyncio.create_task(self._run(), name="platform-jwks-cache")

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._owns_client:
            await self._client.aclose()

    async def refresh(self) -> bool:
        async with self._refresh_lock:
            return await self._refresh_locked()

    async def key_for(self, key_id: str) -> object | None:
        with self._state_lock:
            key = self._keys.get(key_id)
            fresh = self._is_fresh(self._refreshed_at)
        if key is not None and fresh:
            return key

        async with self._refresh_lock:
            with self._state_lock:
                key = self._keys.get(key_id)
                fresh = self._is_fresh(self._refreshed_at)
            if key is not None and fresh:
                return key

            now = self._clock()
            last_attempt = self._last_forced_refresh_at
            if (
                last_attempt is None
                or now - last_attempt >= self._unknown_key_cooldown_seconds
            ):
                self._last_forced_refresh_at = now
                await self._refresh_locked()

            with self._state_lock:
                key = self._keys.get(key_id)
                fresh = self._is_fresh(self._refreshed_at)
            return key if fresh else None

    async def _refresh_locked(self) -> bool:
        try:
            content = bytearray()
            async with self._client.stream(
                "GET",
                self._url,
                headers={"Accept": "application/json"},
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._max_response_bytes:
                        raise ValueError("JWKS response exceeds configured limit")
            value = json.loads(content, object_pairs_hook=_reject_duplicate_object)
            keys = self._validated_keys(value)
        except (httpx.HTTPError, ValueError, TypeError, jwt.PyJWTError):
            return False

        with self._state_lock:
            self._keys = MappingProxyType(keys)
            self._refreshed_at = self._clock()
        return True

    def _validated_keys(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("JWKS must be an object")
        raw_keys = value.get("keys")
        if (
            not isinstance(raw_keys, list)
            or not raw_keys
            or len(raw_keys) > self._max_keys
        ):
            raise ValueError("JWKS key count is invalid")
        keys: dict[str, object] = {}
        for raw in raw_keys:
            if not isinstance(raw, dict):
                raise ValueError("JWK must be an object")
            key_id = raw.get("kid")
            if not isinstance(key_id, str) or not _KEY_ID_PATTERN.fullmatch(key_id):
                raise ValueError("JWK key ID is invalid")
            if key_id in keys:
                raise ValueError("JWK key ID is duplicated")
            if raw.get("kty") != "RSA" or raw.get("alg") not in (None, "RS256"):
                raise ValueError("JWK algorithm is invalid")
            if raw.get("use") not in (None, "sig"):
                raise ValueError("JWK use is invalid")
            keys[key_id] = jwt.algorithms.RSAAlgorithm.from_jwk(
                json.dumps(raw, separators=(",", ":"))
            )
        return keys

    def _is_fresh(self, refreshed_at: float | None) -> bool:
        return (
            refreshed_at is not None
            and self._clock() - refreshed_at <= self._max_staleness_seconds
        )

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._refresh_interval_seconds)
                await self.refresh()
        except asyncio.CancelledError:
            raise


class PlatformWorkloadAuthenticator:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        required_scope: str,
        workload_ids: frozenset[str],
        max_token_ttl_seconds: int,
        clock_skew_seconds: int,
        jwks: JwksCache,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._required_scope = required_scope
        self._workload_ids = workload_ids
        self._max_token_ttl_seconds = max_token_ttl_seconds
        self._clock_skew_seconds = clock_skew_seconds
        self._jwks = jwks

    @property
    def configured(self) -> bool:
        return bool(self._workload_ids)

    @property
    def ready(self) -> bool:
        return self.configured and self._jwks.ready

    @property
    def workload_ids(self) -> frozenset[str]:
        return self._workload_ids

    async def start(self) -> None:
        await self._jwks.start()

    async def close(self) -> None:
        await self._jwks.close()

    async def refresh_keys(self) -> bool:
        return await self._jwks.refresh()

    async def authenticate_workload(
        self, caller_hint: str | None, authorization: str | None
    ) -> WorkloadPrincipal:
        del caller_hint
        token = _bearer_token(authorization)
        try:
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(key_id, str):
                raise ValueError("token header is invalid")
            if not _KEY_ID_PATTERN.fullmatch(key_id):
                raise ValueError("token key ID is invalid")
            key = await self._jwks.key_for(key_id)
            if key is None:
                raise ValueError("token key is unavailable")
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._clock_skew_seconds,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "sub",
                        "iat",
                        "nbf",
                        "exp",
                        "jti",
                        "scope",
                        "principal_type",
                        "token_use",
                        "workload_id",
                    ]
                },
            )
            workload_id = _validated_workload_claims(
                claims,
                audience=self._audience,
                required_scope=self._required_scope,
                allowed_workloads=self._workload_ids,
                max_token_ttl_seconds=self._max_token_ttl_seconds,
            )
        except (jwt.PyJWTError, ValueError, TypeError) as error:
            raise AUTHENTICATION_INVALID from error
        return WorkloadPrincipal(
            workload_id=workload_id,
            credential_kind="workload_token",
        )


class CutoverWorkloadAuthenticator:
    """Routes credentials to Platform JWT or explicitly enabled legacy paths."""

    def __init__(
        self,
        *,
        legacy: ServiceAuthenticator,
        legacy_workload_ids: frozenset[str],
        platform: PlatformWorkloadAuthenticator | None = None,
        platform_required_for_readiness: bool = False,
    ) -> None:
        self._legacy = legacy
        self._legacy_workload_ids = legacy_workload_ids & legacy.workload_ids
        self._platform = platform
        self._platform_required_for_readiness = platform_required_for_readiness

    @property
    def configured(self) -> bool:
        return bool(self.workload_ids)

    @property
    def ready(self) -> bool:
        if not self.configured:
            return False
        if self._platform is None:
            return True
        platform_only = self._platform.workload_ids - self._legacy_workload_ids
        return self._platform.ready or (
            not platform_only and not self._platform_required_for_readiness
        )

    @property
    def workload_ids(self) -> frozenset[str]:
        platform_ids = (
            self._platform.workload_ids if self._platform is not None else frozenset()
        )
        return self._legacy_workload_ids | platform_ids

    async def start(self) -> None:
        if self._platform is not None:
            await self._platform.start()

    async def close(self) -> None:
        if self._platform is not None:
            await self._platform.close()

    async def authenticate_workload(
        self, caller_hint: str | None, authorization: str | None
    ) -> WorkloadPrincipal:
        if caller_hint in self._legacy_workload_ids:
            try:
                return await self._legacy.authenticate_workload(
                    caller_hint, authorization
                )
            except GatewayError:
                pass
        if self._platform is not None:
            return await self._platform.authenticate_workload(
                caller_hint, authorization
            )
        if not authorization:
            raise AUTHENTICATION_REQUIRED
        raise AUTHENTICATION_INVALID


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise AUTHENTICATION_REQUIRED
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        raise AUTHENTICATION_INVALID
    return token


def _validated_workload_claims(
    claims: Mapping[str, object],
    *,
    audience: str,
    required_scope: str,
    allowed_workloads: frozenset[str],
    max_token_ttl_seconds: int,
) -> str:
    workload_id = claims.get("workload_id")
    if (
        not isinstance(workload_id, str)
        or not CALLER_PATTERN.fullmatch(workload_id)
        or claims.get("sub") != workload_id
        or workload_id not in allowed_workloads
    ):
        raise ValueError("workload identity is invalid")
    if (
        claims.get("principal_type") != "workload"
        or claims.get("token_use") != "access"
    ):
        raise ValueError("token principal is invalid")
    if "client_id" in claims:
        raise ValueError("workload token contains account claims")
    if claims.get("aud") != audience:
        raise ValueError("token audience must be singular")
    scope = claims.get("scope")
    if not isinstance(scope, str) or required_scope not in frozenset(scope.split()):
        raise ValueError("token scope is invalid")
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    if (
        not isinstance(issued_at, (int, float))
        or not isinstance(expires_at, (int, float))
        or expires_at <= issued_at
        or expires_at - issued_at > max_token_ttl_seconds
    ):
        raise ValueError("token lifetime is invalid")
    token_id = claims.get("jti")
    if not isinstance(token_id, str) or not token_id or len(token_id) > 128:
        raise ValueError("token ID is invalid")
    return workload_id


def _reject_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JWKS contains a duplicate key")
        value[key] = item
    return value
