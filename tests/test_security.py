from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import GatewayError
from app.core.security import ServiceAuthenticator


def test_legacy_token_overlap_accepts_current_and_previous_only() -> None:
    settings = Settings(
        service_tokens_json=SecretStr(
            json.dumps({"dali_classroom_server": ["current-token", "previous-token"]})
        )
    )
    authenticator = ServiceAuthenticator(settings.service_tokens())

    assert (
        authenticator.authenticate("dali_classroom_server", "Bearer current-token")
        == "dali_classroom_server"
    )
    assert (
        authenticator.authenticate("dali_classroom_server", "Bearer previous-token")
        == "dali_classroom_server"
    )
    with pytest.raises(GatewayError):
        authenticator.authenticate("dali_classroom_server", "Bearer retired-token")


def test_legacy_token_overlap_is_bounded_and_unique() -> None:
    with pytest.raises(ValueError, match="one or two unique tokens"):
        Settings(
            service_tokens_json=SecretStr(
                '{"dali_classroom_server":["one","two","three"]}'
            )
        ).service_tokens()


def test_legacy_token_cannot_authenticate_as_another_workload() -> None:
    authenticator = ServiceAuthenticator(
        {
            "dali_classroom_server": "classroom-token",
            "quality_evaluator": "evaluator-token",
        }
    )

    with pytest.raises(GatewayError):
        authenticator.authenticate(
            "quality_evaluator",
            "Bearer classroom-token",
        )
