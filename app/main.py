from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import router_for
from app.container import Container, build_container
from app.core.config import Settings, get_settings
from app.core.errors import GatewayError


def create_app(
    settings: Settings | None = None,
    *,
    providers: dict[str, object] | None = None,
) -> FastAPI:
    container = build_container(settings or get_settings(), providers=providers)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await container.close()

    application = FastAPI(
        title="Dali AI Gateway",
        version="0.1.0",
        openapi_version="3.1.0",
        lifespan=lifespan,
    )
    application.state.container = container
    application.include_router(router_for(container))

    @application.exception_handler(GatewayError)
    async def gateway_error_handler(_: Request, error: GatewayError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, __: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "ai_gateway_request_invalid",
                    "message": "The AI request is invalid.",
                    "retryable": False,
                }
            },
        )

    return application


app = create_app()
