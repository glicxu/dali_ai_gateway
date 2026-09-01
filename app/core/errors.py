from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GatewayError(Exception):
    status_code: int
    code: str
    message: str
    retryable: bool = False
    retry_after_ms: int | None = None


AUTHENTICATION_REQUIRED = GatewayError(
    401,
    "ai_gateway_authentication_required",
    "Service authentication is required.",
)
AUTHENTICATION_INVALID = GatewayError(
    401,
    "ai_gateway_authentication_invalid",
    "Service authentication is invalid.",
)
PROFILE_NOT_ALLOWED = GatewayError(
    403,
    "ai_gateway_profile_not_allowed",
    "The requested model profile is not allowed.",
)
CAPACITY_EXCEEDED = GatewayError(
    429,
    "ai_gateway_capacity_exceeded",
    "AI capacity is temporarily unavailable.",
    True,
)
SERVICE_DRAINING = GatewayError(
    503,
    "ai_gateway_draining",
    "The AI Gateway is draining active work.",
    True,
)
USAGE_DELIVERY_UNCONFIRMED = GatewayError(
    503,
    "ai_gateway_usage_delivery_unconfirmed",
    "AI work completed but usage delivery was not confirmed.",
    False,
)
PROVIDER_OUTCOME_AMBIGUOUS = GatewayError(
    502,
    "ai_gateway_provider_outcome_ambiguous",
    "The AI provider outcome is unknown and must not be retried automatically.",
    False,
)
PROVIDER_UNAVAILABLE = GatewayError(
    503,
    "ai_gateway_provider_unavailable",
    "The AI provider is temporarily unavailable.",
    True,
)
PROVIDER_NOT_CONFIGURED = GatewayError(
    503,
    "ai_gateway_provider_not_configured",
    "The requested AI provider is not configured.",
    True,
)
REQUEST_INVALID = GatewayError(
    400,
    "ai_gateway_request_invalid",
    "The AI request is invalid.",
)
