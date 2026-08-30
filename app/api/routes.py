from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import ValidationError

from app.container import Container
from app.core.errors import GatewayError, REQUEST_INVALID
from app.models import (
    AudioTranscriptionResponse,
    RealtimeAudioAppend,
    RealtimeCommand,
    RealtimeStart,
    RealtimeTranslationStart,
    TextGenerationRequest,
    TextGenerationResponse,
)


def router_for(container: Container) -> APIRouter:
    router = APIRouter()

    @router.get("/health/live", tags=["Health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready", tags=["Health"])
    async def ready() -> dict[str, str]:
        if not container.authenticator.configured or not container.registry.configured:
            raise GatewayError(
                503,
                "ai_gateway_not_ready",
                "The AI Gateway is not ready.",
                True,
            )
        return {"status": "ready"}

    @router.post(
        "/ai/v1/text/generations",
        response_model=TextGenerationResponse,
        tags=["AI"],
    )
    async def generate_text(
        request: TextGenerationRequest,
        authorization: str | None = Header(default=None),
        caller: str | None = Header(default=None, alias="X-Dali-Caller"),
    ) -> TextGenerationResponse:
        principal = container.authenticator.authenticate(caller, authorization)
        return await container.service.generate_text(caller=principal, request=request)

    @router.post(
        "/ai/v1/audio/transcriptions",
        response_model=AudioTranscriptionResponse,
        tags=["AI"],
    )
    async def transcribe_audio(
        request_id: UUID = Form(),
        product: str = Form(pattern=r"^[a-z][a-z0-9_-]{1,63}$"),
        profile: str = Form(pattern=r"^[a-z][a-z0-9_.-]{2,127}$"),
        source_language: str = Form(default="auto", min_length=2, max_length=35),
        terminology_prompt: str = Form(default="", max_length=2_000),
        audio: UploadFile = File(),
        authorization: str | None = Header(default=None),
        caller: str | None = Header(default=None, alias="X-Dali-Caller"),
    ) -> AudioTranscriptionResponse:
        principal = container.authenticator.authenticate(caller, authorization)
        value = await audio.read(container.settings.max_audio_bytes + 1)
        if len(value) > container.settings.max_audio_bytes:
            raise REQUEST_INVALID
        return await container.service.transcribe_audio(
            caller=principal,
            request_id=request_id,
            product=product,
            profile_name=profile,
            audio=value,
            filename=audio.filename or "audio.bin",
            content_type=audio.content_type or "application/octet-stream",
            source_language=source_language,
            terminology_prompt=terminology_prompt,
        )

    @router.websocket("/ai/v1/realtime/transcriptions")
    async def realtime_transcription(websocket: WebSocket) -> None:
        try:
            caller = container.authenticator.authenticate(
                websocket.headers.get("x-dali-caller"),
                websocket.headers.get("authorization"),
            )
        except GatewayError as error:
            await websocket.close(code=4401, reason=error.code)
            return
        await websocket.accept()
        session = None
        try:
            try:
                start = RealtimeStart.model_validate(await websocket.receive_json())
            except (ValidationError, ValueError, TypeError):
                raise REQUEST_INVALID
            async with container.service.admission.lease(
                caller, "realtime_transcription"
            ):
                session = await container.service.open_realtime(
                    caller=caller, request=start
                )
                await websocket.send_json(
                    {
                        "type": "session.ready",
                        "request_id": str(start.request_id),
                        "profile": start.profile,
                    }
                )
                await _bridge(websocket, session)
        except WebSocketDisconnect:
            pass
        except GatewayError as error:
            await _safe_error(websocket, error)
        finally:
            if session is not None:
                await session.close()

    @router.websocket("/ai/v1/realtime/translations")
    async def realtime_translation(websocket: WebSocket) -> None:
        try:
            caller = container.authenticator.authenticate(
                websocket.headers.get("x-dali-caller"),
                websocket.headers.get("authorization"),
            )
        except GatewayError as error:
            await websocket.close(code=4401, reason=error.code)
            return
        await websocket.accept()
        session = None
        try:
            try:
                start = RealtimeTranslationStart.model_validate(
                    await websocket.receive_json()
                )
            except (ValidationError, ValueError, TypeError):
                raise REQUEST_INVALID
            async with container.service.admission.lease(
                caller, "realtime_translation"
            ):
                session = await container.service.open_realtime_translation(
                    caller=caller, request=start
                )
                await websocket.send_json(
                    {
                        "type": "session.ready",
                        "request_id": str(start.request_id),
                        "profile": start.profile,
                    }
                )
                await _bridge(websocket, session)
        except WebSocketDisconnect:
            pass
        except GatewayError as error:
            await _safe_error(websocket, error)
        finally:
            if session is not None:
                await session.close()

    return router


async def _bridge(websocket: WebSocket, session) -> None:
    while True:
        client_task = asyncio.create_task(websocket.receive_json())
        provider_task = asyncio.create_task(session.next_event())
        done, pending = await asyncio.wait(
            {client_task, provider_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if provider_task in done:
            event = provider_task.result()
            if event.type == "error":
                await websocket.send_json(
                    {
                        "type": "error",
                        "error": {
                            "code": "ai_gateway_provider_unavailable",
                            "message": "The AI provider is temporarily unavailable.",
                            "retryable": True,
                        },
                    }
                )
                return
            payload = {"type": event.type}
            if event.text is not None:
                payload["text"] = event.text
            if event.item_id is not None:
                payload["item_id"] = event.item_id
            await websocket.send_json(payload)
        if client_task in done:
            raw = client_task.result()
            event_type = raw.get("type") if isinstance(raw, dict) else None
            try:
                if event_type == "audio.append":
                    append = RealtimeAudioAppend.model_validate(raw)
                    if len(append.audio) > 1_400_000:
                        raise REQUEST_INVALID
                    await session.append(append.audio)
                else:
                    command = RealtimeCommand.model_validate(raw)
                    if command.type == "audio.commit":
                        await session.commit()
                    elif command.type == "audio.clear":
                        await session.clear()
                    else:
                        return
            except ValidationError as error:
                raise REQUEST_INVALID from error


async def _safe_error(websocket: WebSocket, error: GatewayError) -> None:
    try:
        await websocket.send_json(
            {
                "type": "error",
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                },
            }
        )
        await websocket.close(code=1011 if error.retryable else 1008)
    except Exception:
        pass
