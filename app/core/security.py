from __future__ import annotations

import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from app.core.errors import AUTHENTICATION_INVALID, AUTHENTICATION_REQUIRED


CALLER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")


@dataclass(frozen=True, slots=True)
class WorkloadPrincipal:
    workload_id: str
    credential_kind: Literal["legacy_service_token", "workload_token"]


class WorkloadAuthenticator(Protocol):
    @property
    def configured(self) -> bool: ...

    @property
    def ready(self) -> bool: ...

    @property
    def workload_ids(self) -> frozenset[str]: ...

    async def authenticate_workload(
        self, caller_hint: str | None, authorization: str | None
    ) -> WorkloadPrincipal: ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...


class ServiceAuthenticator:
    def __init__(self, tokens: Mapping[str, str | Sequence[str]]) -> None:
        self._tokens = {
            caller: (value,) if isinstance(value, str) else tuple(value)
            for caller, value in tokens.items()
        }

    @property
    def configured(self) -> bool:
        return bool(self._tokens)

    @property
    def ready(self) -> bool:
        return self.configured

    @property
    def callers(self) -> frozenset[str]:
        """Compatibility alias; new code uses workload_ids."""
        return self.workload_ids

    @property
    def workload_ids(self) -> frozenset[str]:
        return frozenset(self._tokens)

    def authenticate(self, caller: str | None, authorization: str | None) -> str:
        return self._authenticate_legacy(caller, authorization).workload_id

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def authenticate_workload(
        self, caller_hint: str | None, authorization: str | None
    ) -> WorkloadPrincipal:
        return self._authenticate_legacy(caller_hint, authorization)

    def _authenticate_legacy(
        self, caller_hint: str | None, authorization: str | None
    ) -> WorkloadPrincipal:
        caller = caller_hint
        if not caller or not authorization:
            raise AUTHENTICATION_REQUIRED
        if not CALLER_PATTERN.fullmatch(caller):
            raise AUTHENTICATION_INVALID
        scheme, separator, supplied = authorization.partition(" ")
        expected = self._tokens.get(caller, ())
        token_matches = tuple(
            secrets.compare_digest(token, supplied) for token in expected
        )
        if separator != " " or scheme.lower() != "bearer" or not any(token_matches):
            raise AUTHENTICATION_INVALID
        return WorkloadPrincipal(
            workload_id=caller,
            credential_kind="legacy_service_token",
        )
