from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.security import ServiceAuthenticator
from app.providers.registry import ProviderRegistry
from app.services import AdmissionController, GatewayService


@dataclass(slots=True)
class Container:
    settings: Settings
    authenticator: ServiceAuthenticator
    registry: ProviderRegistry
    service: GatewayService

    async def close(self) -> None:
        await self.registry.close()


def build_container(
    settings: Settings, *, providers: dict[str, object] | None = None
) -> Container:
    authenticator = ServiceAuthenticator(settings.service_tokens())
    registry = ProviderRegistry(settings, providers=providers)
    admission = AdmissionController(settings.caller_limits())
    return Container(
        settings=settings,
        authenticator=authenticator,
        registry=registry,
        service=GatewayService(registry, admission),
    )
