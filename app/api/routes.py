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
from fastapi.responses import Response
from pydantic import ValidationError

from app.container import Container
from app.core.errors import GatewayError, REQUEST_INVALID
from app.models import (
    AudioTranscriptionResponse,
    MediaAnalysisResponse,
    RealtimeAudioAppend,
    RealtimeCommand,
    RealtimeStart,
    RealtimeTranslationStart,
    SpeechSynthesisRequest,
    TextGenerationRequest,
    TextGenerationResponse,
)


_IMAGE_CONTENT_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
_VIDEO_CONTENT_TYPES = {
    "video/mp4",
    "video/mpeg",
    "video/quicktime",
    "video/webm",
}


def router_for(container: Container) -> APIRouter:
    router = APIRouter()

    @router.get("/health/live", tags=["Health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready", tags=["Health"])
    async def ready() -> dict[str, str]:
        if not container.is_ready():
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
        principal = await container.authenticator.authenticate_workload(
            caller, authorization
        )
        return await container.service.generate_text(
            caller=principal.workload_id, request=request
        )

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
        principal = await container.authenticator.authenticate_workload(
            caller, authorization
        )
        value = await audio.read(container.settings.max_audio_bytes + 1)
        if len(value) > container.settings.max_audio_bytes:
            raise REQUEST_INVALID
        return await container.service.transcribe_audio(
            caller=principal.workload_id,
            request_id=request_id,
            product=product,
            profile_name=profile,
            audio=value,
            filename=audio.filename or "audio.bin",
            content_type=audio.content_type or "application/octet-stream",
            source_language=source_language,
            terminology_prompt=terminology_prompt,
        )

    @router.post(
        "/ai/v1/audio/speech",
        response_class=Response,
        responses={200: {"content": {"audio/wav": {}}}},
        tags=["AI"],
    )
    async def synthesize_speech(
        request: SpeechSynthesisRequest,
        authorization: str | None = Header(default=None),
        caller: str | None = Header(default=None, alias="X-Dali-Caller"),
    ) -> Response:
        principal = await container.authenticator.authenticate_workload(
            caller, authorization
        )
        result, provider, model = await container.service.synthesize_speech(
            caller=principal.workload_id, request=request
        )
        headers = {
            "X-Dali-Provider": provider,
            "X-Dali-Model": model,
        }
        if result.usage.input_tokens is not None:
            headers["X-Dali-Input-Tokens"] = str(result.usage.input_tokens)
        if result.usage.output_tokens is not None:
            headers["X-Dali-Output-Tokens"] = str(result.usage.output_tokens)
        return Response(
            content=result.audio,
            media_type=result.content_type,
            headers=headers,
        )

    @router.post(
        "/ai/v1/media/analyses",
        response_model=MediaAnalysisResponse,
        tags=["AI"],
    )
    async def analyze_media(
        request_id: UUID = Form(),
        product: str = Form(pattern=r"^[a-z][a-z0-9_-]{1,63}$"),
        profile: str = Form(pattern=r"^[a-z][a-z0-9_.-]{2,127}$"),
        system_instruction: str = Form(min_length=1, max_length=20_000),
        prompt: str = Form(min_length=1, max_length=50_000),
        temperature: float = Form(default=0.2, ge=0, le=2),
        media: UploadFile = File(),
        authorization: str | None = Header(default=None),
        caller: str | None = Header(default=None, alias="X-Dali-Caller"),
    ) -> MediaAnalysisResponse:
        principal = await container.authenticator.authenticate_workload(
            caller, authorization
        )
        content_type = (media.content_type or "").lower()
        if content_type in _IMAGE_CONTENT_TYPES:
            media_kind = "image"
        elif content_type in _VIDEO_CONTENT_TYPES:
            media_kind = "video"
        else:
            raise REQUEST_INVALID
        value = await media.read(container.settings.max_media_bytes + 1)
        if not value or len(value) > container.settings.max_media_bytes:
            raise REQUEST_INVALID
        return await container.service.analyze_media(
            caller=principal.workload_id,
            request_id=request_id,
            product=product,
            profile_name=profile,
            system_instruction=system_instruction,
            prompt=prompt,
            media=value,
            content_type=content_type,
            media_kind=media_kind,
            temperature=temperature,
        )

    @router.websocket("/ai/v1/realtime/transcriptions")
    async def realtime_transcription(websocket: WebSocket) -> None:
        try:
            principal = await container.authenticator.authenticate_workload(
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
            caller = principal.workload_id
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
            principal = await container.authenticator.authenticate_workload(
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
            caller = principal.workload_id
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
            if event.audio is not None:
                payload["audio"] = event.audio
            if event.content_type is not None:
                payload["content_type"] = event.content_type
            if event.sample_rate_hz is not None:
                payload["sample_rate_hz"] = event.sample_rate_hz
            if event.channels is not None:
                payload["channels"] = event.channels
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
