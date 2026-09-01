from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


WindowSeconds = Literal[60, 90, 120]
RealtimePolicyMode = Literal[
    "single", "compare", "windowed_failover", "windowed_alternate"
]


class RealtimeRoutePolicy(BaseModel):
    """Validated provider-neutral policy for a realtime translation session.

    The policy contains profile references only. Provider and model identifiers
    remain server-side configuration, and callers cannot provide credentials.
    This model is deliberately independent of the v1 WebSocket contract until
    the versioned realtime contract is enabled.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: RealtimePolicyMode = "single"
    primary_profile: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")
    fallback_profile: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_.-]{2,127}$"
    )
    compare_profile: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_.-]{2,127}$"
    )
    window_seconds: WindowSeconds = 90

    @model_validator(mode="after")
    def validate_mode_profiles(self) -> RealtimeRoutePolicy:
        if self.mode == "single":
            if self.fallback_profile is not None or self.compare_profile is not None:
                raise ValueError(
                    "single mode cannot define fallback or compare profile"
                )
        elif self.mode == "compare":
            if self.compare_profile is None:
                raise ValueError("compare mode requires compare_profile")
            if self.fallback_profile is not None:
                raise ValueError("compare mode cannot define fallback_profile")
        elif self.fallback_profile is None:
            raise ValueError("windowed routing mode requires fallback_profile")

        referenced = [
            profile
            for profile in (
                self.primary_profile,
                self.fallback_profile,
                self.compare_profile,
            )
            if profile is not None
        ]
        if len(set(referenced)) != len(referenced):
            raise ValueError("realtime route profiles must be distinct")
        return self

    @property
    def ordered_profiles(self) -> tuple[str, ...]:
        """Return the stable route order used by selection and preflight."""
        if self.mode == "compare":
            assert self.compare_profile is not None
            return (self.primary_profile, self.compare_profile)
        if self.fallback_profile is not None:
            return (self.primary_profile, self.fallback_profile)
        return (self.primary_profile,)
