from __future__ import annotations

import re
import secrets

from app.core.errors import AUTHENTICATION_INVALID, AUTHENTICATION_REQUIRED


CALLER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")


class ServiceAuthenticator:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = dict(tokens)

    @property
    def configured(self) -> bool:
        return bool(self._tokens)

    def authenticate(self, caller: str | None, authorization: str | None) -> str:
        if not caller or not authorization:
            raise AUTHENTICATION_REQUIRED
        if not CALLER_PATTERN.fullmatch(caller):
            raise AUTHENTICATION_INVALID
        scheme, separator, supplied = authorization.partition(" ")
        expected = self._tokens.get(caller)
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or expected is None
            or not secrets.compare_digest(expected, supplied)
        ):
            raise AUTHENTICATION_INVALID
        return caller
