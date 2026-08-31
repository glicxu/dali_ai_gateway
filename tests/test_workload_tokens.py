from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from typing import Callable

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import GatewayError
from app.core.security import ServiceAuthenticator
from app.core.workload_tokens import (
    CutoverWorkloadAuthenticator,
    JwksCache,
    PlatformWorkloadAuthenticator,
)


@dataclass(frozen=True)
class _SigningKey:
    key_id: str
    private_key: rsa.RSAPrivateKey
    jwk: dict[str, str]


def _signing_key(key_id: str) -> _SigningKey:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    return _SigningKey(
        key_id=key_id,
        private_key=private_key,
        jwk={
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "kid": key_id,
            "n": _base64url_uint(numbers.n),
            "e": _base64url_uint(numbers.e),
        },
    )


def _base64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _token(key: _SigningKey, **overrides: object) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://platform.test",
        "aud": "dali-ai-gateway",
        "sub": "dali_classroom_server",
        "workload_id": "dali_classroom_server",
        "principal_type": "workload",
        "token_use": "access",
        "scope": "ai_gateway:invoke",
        "iat": now,
        "nbf": now,
        "exp": now + 300,
        "jti": "e5f5d679-2b31-49e3-8c84-1488ee87e429",
        **overrides,
    }
    return jwt.encode(
        claims,
        key.private_key,
        algorithm="RS256",
        headers={"kid": key.key_id},
    )


def _jwks_response(*keys: _SigningKey) -> httpx.Response:
    return httpx.Response(200, json={"keys": [key.jwk for key in keys]})


def _authenticator(
    *,
    client: httpx.AsyncClient,
    clock: Callable[[], float] = time.monotonic,
    max_staleness_seconds: float = 300,
) -> PlatformWorkloadAuthenticator:
    cache = JwksCache(
        url="https://platform.test/.well-known/jwks.json",
        timeout_seconds=0.2,
        refresh_interval_seconds=60,
        max_staleness_seconds=max_staleness_seconds,
        unknown_key_cooldown_seconds=5,
        client=client,
        clock=clock,
    )
    return PlatformWorkloadAuthenticator(
        issuer="https://platform.test",
        audience="dali-ai-gateway",
        required_scope="ai_gateway:invoke",
        workload_ids=frozenset({"dali_classroom_server"}),
        max_token_ttl_seconds=600,
        clock_skew_seconds=5,
        jwks=cache,
    )


def test_platform_workload_token_derives_identity_and_validates_claim_shape() -> None:
    async def exercise() -> None:
        key = _signing_key("current-key")
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: _jwks_response(key))
        )
        authenticator = _authenticator(client=client)
        await authenticator.start()
        try:
            principal = await authenticator.authenticate_workload(
                "spoofed_header",
                f"Bearer {_token(key)}",
            )
            assert principal.workload_id == "dali_classroom_server"
            assert principal.credential_kind == "workload_token"

            invalid_claims = [
                {"iss": "https://wrong.test"},
                {"aud": "wrong-audience"},
                {"aud": ["dali-ai-gateway", "other"]},
                {"scope": "other:scope"},
                {"principal_type": "account", "client_id": "mobile"},
                {"token_use": "refresh"},
                {"workload_id": "quality_evaluator"},
                {"sub": "quality_evaluator"},
                {"exp": int(time.time()) + 1200},
            ]
            for overrides in invalid_claims:
                with pytest.raises(GatewayError):
                    await authenticator.authenticate_workload(
                        None,
                        f"Bearer {_token(key, **overrides)}",
                    )
        finally:
            await authenticator.close()
            await client.aclose()

    asyncio.run(exercise())


def test_unknown_key_refresh_is_coalesced_and_accepts_new_key() -> None:
    async def exercise() -> None:
        current = _signing_key("current-key")
        next_key = _signing_key("next-key")
        calls = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return (
                _jwks_response(current)
                if calls == 1
                else _jwks_response(current, next_key)
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        authenticator = _authenticator(client=client)
        await authenticator.start()
        try:
            principal = await authenticator.authenticate_workload(
                None,
                f"Bearer {_token(next_key)}",
            )
            assert principal.workload_id == "dali_classroom_server"
            assert calls == 2
        finally:
            await authenticator.close()
            await client.aclose()

    asyncio.run(exercise())


def test_unknown_key_refresh_prevents_request_stampede() -> None:
    async def exercise() -> None:
        current = _signing_key("current-key")
        unknown = _signing_key("unknown-key")
        calls = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return _jwks_response(current)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        authenticator = _authenticator(client=client)
        await authenticator.start()
        try:
            results = await asyncio.gather(
                *(
                    authenticator.authenticate_workload(
                        None,
                        f"Bearer {_token(unknown)}",
                    )
                    for _ in range(10)
                ),
                return_exceptions=True,
            )
            assert all(isinstance(result, GatewayError) for result in results)
            assert calls == 2
        finally:
            await authenticator.close()
            await client.aclose()

    asyncio.run(exercise())


def test_last_known_good_keys_fail_closed_after_maximum_staleness() -> None:
    async def exercise() -> None:
        key = _signing_key("current-key")
        now = [100.0]
        succeed = True

        def handler(_: httpx.Request) -> httpx.Response:
            return _jwks_response(key) if succeed else httpx.Response(503)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        authenticator = _authenticator(
            client=client,
            clock=lambda: now[0],
            max_staleness_seconds=30,
        )
        await authenticator.start()
        token = _token(key)
        try:
            assert authenticator.ready
            succeed = False
            now[0] += 20
            assert not await authenticator.refresh_keys()
            assert (
                await authenticator.authenticate_workload(None, f"Bearer {token}")
            ).workload_id == "dali_classroom_server"

            now[0] += 11
            assert not authenticator.ready
            with pytest.raises(GatewayError):
                await authenticator.authenticate_workload(None, f"Bearer {token}")
        finally:
            await authenticator.close()
            await client.aclose()

    asyncio.run(exercise())


def test_jwks_rotation_retires_previous_key_deterministically() -> None:
    async def exercise() -> None:
        previous = _signing_key("previous-key")
        current = _signing_key("current-key")
        calls = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return (
                _jwks_response(previous, current)
                if calls == 1
                else _jwks_response(current)
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        authenticator = _authenticator(client=client)
        await authenticator.start()
        try:
            assert (
                await authenticator.authenticate_workload(
                    None, f"Bearer {_token(previous)}"
                )
            ).workload_id == "dali_classroom_server"
            assert await authenticator.refresh_keys()
            with pytest.raises(GatewayError):
                await authenticator.authenticate_workload(
                    None, f"Bearer {_token(previous)}"
                )
            assert (
                await authenticator.authenticate_workload(
                    None, f"Bearer {_token(current)}"
                )
            ).workload_id == "dali_classroom_server"
        finally:
            await authenticator.close()
            await client.aclose()

    asyncio.run(exercise())


def test_legacy_compatibility_is_caller_specific_and_revocable() -> None:
    async def exercise() -> None:
        legacy = ServiceAuthenticator(
            {
                "dali_classroom_server": "classroom-token",
                "dali_chat_server": "chat-token",
            }
        )
        authenticator = CutoverWorkloadAuthenticator(
            legacy=legacy,
            legacy_workload_ids=frozenset({"dali_classroom_server"}),
        )
        assert (
            await authenticator.authenticate_workload(
                "dali_classroom_server", "Bearer classroom-token"
            )
        ).workload_id == "dali_classroom_server"
        with pytest.raises(GatewayError):
            await authenticator.authenticate_workload(
                "dali_chat_server", "Bearer chat-token"
            )

    asyncio.run(exercise())


def test_platform_shadow_failure_preserves_explicit_legacy_rollback() -> None:
    async def exercise() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(503))
        )
        platform = _authenticator(client=client)
        authenticator = CutoverWorkloadAuthenticator(
            legacy=ServiceAuthenticator({"dali_classroom_server": "classroom-token"}),
            legacy_workload_ids=frozenset({"dali_classroom_server"}),
            platform=platform,
            platform_required_for_readiness=False,
        )
        await authenticator.start()
        try:
            assert authenticator.ready
            principal = await authenticator.authenticate_workload(
                "dali_classroom_server",
                "Bearer classroom-token",
            )
            assert principal.credential_kind == "legacy_service_token"
        finally:
            await authenticator.close()
            await client.aclose()

    asyncio.run(exercise())


def test_platform_authentication_requires_complete_disabled_by_default_config() -> None:
    assert not Settings().platform_workload_auth_enabled
    with pytest.raises(ValidationError, match="complete configuration"):
        Settings(platform_workload_auth_enabled=True)
