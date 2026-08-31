from __future__ import annotations

from dataclasses import dataclass

from app.core.circuit import CircuitRegistry
from app.core.config import Settings
from app.core.security import ServiceAuthenticator, WorkloadAuthenticator
from app.core.workload_tokens import (
    CutoverWorkloadAuthenticator,
    JwksCache,
    PlatformWorkloadAuthenticator,
)
from app.core.usage_delivery import SqsUsageSink, UsageDelivery
from app.providers.registry import ProviderRegistry
from app.services import AdmissionController, GatewayService


@dataclass(slots=True)
class Container:
    settings: Settings
    authenticator: WorkloadAuthenticator
    registry: ProviderRegistry
    service: GatewayService
    usage_delivery: UsageDelivery | None = None

    def is_ready(self) -> bool:
        return self.authenticator.ready and self.registry.is_ready_for_workloads(
            self.authenticator.workload_ids
        )

    async def start(self) -> None:
        await self.authenticator.start()
        await self.registry.start()

    async def close(self) -> None:
        try:
            await self.registry.close()
        finally:
            await self.authenticator.close()


def build_container(
    settings: Settings,
    *,
    providers: dict[str, object] | None = None,
    authenticator: WorkloadAuthenticator | None = None,
) -> Container:
    workload_authenticator = authenticator or _build_authenticator(settings)
    registry = ProviderRegistry(settings, providers=providers)
    admission = AdmissionController(
        settings.caller_limits(), lease_ttl_seconds=settings.admission_lease_ttl_seconds
    )
    return Container(
        settings=settings,
        authenticator=workload_authenticator,
        registry=registry,
        service=GatewayService(
            registry,
            admission,
            CircuitRegistry(
                enabled=settings.provider_circuit_enabled,
                failure_threshold=settings.provider_circuit_failure_threshold,
                open_seconds=settings.provider_circuit_open_seconds,
                disabled_routes=settings.provider_circuit_disabled_routes(),
            ),
        ),
        usage_delivery=_build_usage_delivery(settings),
    )


def _build_usage_delivery(settings: Settings) -> UsageDelivery | None:
    if not settings.usage_sqs_queue_url or not settings.usage_sqs_region:
        return None
    return UsageDelivery(
        SqsUsageSink(
            queue_url=settings.usage_sqs_queue_url,
            region_name=settings.usage_sqs_region,
        ),
        max_attempts=settings.usage_delivery_max_attempts,
        retry_delay_seconds=settings.usage_delivery_retry_delay_seconds,
    )


def _build_authenticator(settings: Settings) -> WorkloadAuthenticator:
    legacy = ServiceAuthenticator(settings.service_tokens())
    platform = None
    if settings.platform_workload_auth_enabled:
        platform = PlatformWorkloadAuthenticator(
            issuer=str(settings.platform_workload_issuer),
            audience=str(settings.platform_workload_audience),
            required_scope=str(settings.platform_workload_required_scope),
            workload_ids=settings.platform_workload_ids(),
            max_token_ttl_seconds=settings.platform_workload_max_token_ttl_seconds,
            clock_skew_seconds=settings.platform_workload_clock_skew_seconds,
            jwks=JwksCache(
                url=str(settings.platform_workload_jwks_url),
                timeout_seconds=settings.platform_jwks_timeout_seconds,
                refresh_interval_seconds=(
                    settings.platform_jwks_refresh_interval_seconds
                ),
                max_staleness_seconds=settings.platform_jwks_max_staleness_seconds,
                unknown_key_cooldown_seconds=(
                    settings.platform_jwks_unknown_key_cooldown_seconds
                ),
            ),
        )
    return CutoverWorkloadAuthenticator(
        legacy=legacy,
        legacy_workload_ids=settings.legacy_auth_workload_ids(),
        platform=platform,
        platform_required_for_readiness=(
            settings.platform_workload_auth_required_for_readiness
        ),
    )
