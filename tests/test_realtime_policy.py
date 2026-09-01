import pytest
from pydantic import ValidationError

from app.core.realtime_policy import RealtimeRoutePolicy


def test_single_policy_defaults_to_ninety_second_windows() -> None:
    policy = RealtimeRoutePolicy(primary_profile="dali_chat.interpret.gemini")

    assert policy.mode == "single"
    assert policy.window_seconds == 90
    assert policy.fallback_profile is None


def test_windowed_failover_requires_distinct_fallback() -> None:
    policy = RealtimeRoutePolicy(
        mode="windowed_failover",
        primary_profile="dali_chat.interpret.gemini",
        fallback_profile="dali_chat.interpret.openai",
        window_seconds=60,
    )

    assert policy.window_seconds == 60
    assert policy.ordered_profiles == (
        "dali_chat.interpret.gemini",
        "dali_chat.interpret.openai",
    )

    with pytest.raises(ValidationError, match="requires fallback_profile"):
        RealtimeRoutePolicy(
            mode="windowed_failover",
            primary_profile="dali_chat.interpret.gemini",
        )

    with pytest.raises(ValidationError, match="must be distinct"):
        RealtimeRoutePolicy(
            mode="windowed_failover",
            primary_profile="dali_chat.interpret.gemini",
            fallback_profile="dali_chat.interpret.gemini",
        )


def test_compare_requires_only_compare_profile() -> None:
    policy = RealtimeRoutePolicy(
        mode="compare",
        primary_profile="dali_chat.interpret.gemini",
        compare_profile="dali_chat.interpret.openai",
        window_seconds=120,
    )

    assert policy.compare_profile == "dali_chat.interpret.openai"
    assert policy.ordered_profiles == (
        "dali_chat.interpret.gemini",
        "dali_chat.interpret.openai",
    )

    with pytest.raises(ValidationError, match="requires compare_profile"):
        RealtimeRoutePolicy(
            mode="compare",
            primary_profile="dali_chat.interpret.gemini",
        )


def test_single_rejects_extra_route() -> None:
    with pytest.raises(ValidationError, match="single mode"):
        RealtimeRoutePolicy(
            primary_profile="dali_chat.interpret.gemini",
            fallback_profile="dali_chat.interpret.openai",
        )
