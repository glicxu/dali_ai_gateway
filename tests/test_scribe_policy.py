from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.errors import GatewayError


def test_scribe_policy_disabled_by_default() -> None:
    settings = Settings(scribe_ai_enabled=False)
    policy = settings.policy_generation()

    # Scribe profiles exist in the catalog
    assert "scribe.transcription.live" in policy.profiles
    assert "scribe.summary.text" in policy.profiles

    # Scribe grant exists but is disabled
    grant = policy.grants.get("dali_scribe_server_ai")
    assert grant is not None
    assert grant.enabled is False
    assert grant.products == frozenset({"scribe"})
    assert "scribe.transcription.live" in grant.profiles
    assert "scribe.summary.text" in grant.profiles


def test_scribe_policy_enabled_with_setting() -> None:
    settings = Settings(scribe_ai_enabled=True)
    policy = settings.policy_generation()

    grant = policy.grants.get("dali_scribe_server_ai")
    assert grant is not None
    assert grant.enabled is True
    assert grant.products == frozenset({"scribe"})


def test_scribe_grant_cannot_access_classroom_or_chat_profiles() -> None:
    settings = Settings(scribe_ai_enabled=True)
    policy = settings.policy_generation()

    grant = policy.grants["dali_scribe_server_ai"]
    # Does not have classroom or chat products
    assert "classroom" not in grant.products
    assert "dali_chat" not in grant.products

    # Does not have classroom profiles
    assert "classroom.translation.economy" not in grant.profiles
    assert "classroom.transcription.live" not in grant.profiles
