from __future__ import annotations

import asyncio
import base64
import binascii
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

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
from app.core.errors import GatewayError, REQUEST_INVALID, SERVICE_DRAINING
from app.models import (
    AudioTranscriptionResponse,
    MediaAnalysisResponse,
    RealtimeAudioAppend,
    RealtimeCommand,
    RealtimeStart,
    RealtimeTranslationStart,
    RealtimeV2AudioAppend,
    RealtimeV2TranslationStart,
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
_NORMALIZED_REALTIME_PROVIDER_EVENTS = frozenset(
    {
        "transcript.delta",
        "transcript.final",
        "translation.delta",
        "translation.final",
        "translation.audio.delta",
        "translation.audio.final",
    }
)


def _provider_event_matches_requested_outputs(
    event_type: str, outputs: list[str] | None
) -> bool:
    requested = frozenset(outputs or {"target_transcript", "translated_audio"})
    if event_type.startswith("transcript."):
        return "source_transcript" in requested
    if event_type.startswith("translation.audio."):
        return "translated_audio" in requested
    return "target_transcript" in requested


def router_for(container: Container) -> APIRouter:
    router = APIRouter()

    def ensure_accepting() -> None:
        if container.draining:
            raise SERVICE_DRAINING

    @router.get("/health/live", tags=["Health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready", tags=["Health"])
    async def ready() -> dict[str, object]:
        if not container.is_ready():
            raise GatewayError(
                503,
                "ai_gateway_not_ready",
                "The AI Gateway is not ready.",
                True,
            )
        return {"status": "ready", **(await container.safe_readiness_details())}

    @router.get("/metrics", response_class=Response, tags=["Health"])
    async def metrics() -> Response:
        details = await container.safe_readiness_details()
        provider_counts = details["provider_counts"]
        assert isinstance(provider_counts, dict)
        lines = [
            "# TYPE dali_gateway_ready gauge",
            f"dali_gateway_ready {1 if container.is_ready() else 0}",
            "# TYPE dali_gateway_draining gauge",
            f"dali_gateway_draining {1 if details['draining'] else 0}",
            "# TYPE dali_gateway_active_leases gauge",
            f"dali_gateway_active_leases {details['active_leases']}",
            "# TYPE dali_gateway_usage_sink_configured gauge",
            "dali_gateway_usage_sink_configured "
            f"{1 if details['usage_sink_configured'] else 0}",
            "# TYPE dali_gateway_provider_health gauge",
        ]
        for status in ("healthy", "degraded", "stale", "unknown"):
            lines.append(
                f'dali_gateway_provider_health{{status="{status}"}} '
                f"{provider_counts.get(status, 0)}"
            )
        lines.append("# TYPE dali_gateway_events_total counter")
        for outcome, count in sorted(container.service.safe_metric_counts().items()):
            lines.append(f'dali_gateway_events_total{{outcome="{outcome}"}} {count}')
        return Response(content="\n".join(lines) + "\n", media_type="text/plain")

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
        ensure_accepting()
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
        ensure_accepting()
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
        ensure_accepting()
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
        ensure_accepting()
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
            ensure_accepting()
            principal = await container.authenticator.authenticate_workload(
                websocket.headers.get("x-dali-caller"),
                websocket.headers.get("authorization"),
            )
        except GatewayError as error:
            await websocket.close(
                code=1013 if error.code == SERVICE_DRAINING.code else 4401,
                reason=error.code,
            )
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
            ensure_accepting()
            principal = await container.authenticator.authenticate_workload(
                websocket.headers.get("x-dali-caller"),
                websocket.headers.get("authorization"),
            )
        except GatewayError as error:
            await websocket.close(
                code=1013 if error.code == SERVICE_DRAINING.code else 4401,
                reason=error.code,
            )
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

    @router.websocket("/ai/v2/realtime/transcriptions")
    async def realtime_transcription_v2(websocket: WebSocket) -> None:
        try:
            ensure_accepting()
            principal = await container.authenticator.authenticate_workload(
                websocket.headers.get("x-dali-caller"),
                websocket.headers.get("authorization"),
            )
        except GatewayError as error:
            await websocket.close(
                code=1013 if error.code == SERVICE_DRAINING.code else 4401,
                reason=error.code,
            )
            return
        await websocket.accept()
        session = None
        session_ref = [None]
        try:
            try:
                start = RealtimeStart.model_validate(await websocket.receive_json())
            except (ValidationError, ValueError, TypeError):
                raise REQUEST_INVALID
            caller = principal.workload_id
            async with container.service.admission.lease(
                caller, "realtime_transcription"
            ):

                async def open_profile(_profile: str):
                    return await container.service.open_realtime(
                        caller=caller, request=start
                    )

                session = await open_profile(start.profile)
                session_ref[0] = session
                await _bridge_v2(
                    websocket,
                    session,
                    request_id=start.request_id,
                    profile=start.profile,
                    audio_sample_rate_hz=start.audio_sample_rate_hz,
                    outputs=["source_transcript"],
                    open_profile=open_profile,
                    session_ref=session_ref,
                    is_draining=lambda: container.draining,
                )
        except WebSocketDisconnect:
            pass
        except GatewayError as error:
            await _safe_error(websocket, error)
        finally:
            if session_ref[0] is not None:
                await session_ref[0].close()
            elif session is not None:
                await session.close()

    @router.websocket("/ai/v2/realtime/translations")
    async def realtime_translation_v2(websocket: WebSocket) -> None:
        """Versioned contract foundation; routing policies are added next."""
        try:
            ensure_accepting()
            principal = await container.authenticator.authenticate_workload(
                websocket.headers.get("x-dali-caller"),
                websocket.headers.get("authorization"),
            )
        except GatewayError as error:
            await websocket.close(
                code=1013 if error.code == SERVICE_DRAINING.code else 4401,
                reason=error.code,
            )
            return
        await websocket.accept()
        session = None
        session_ref = [None]
        usage_state = None
        usage_delivery_callback = None
        try:
            try:
                start = RealtimeV2TranslationStart.model_validate(
                    await websocket.receive_json()
                )
            except (ValidationError, ValueError, TypeError):
                raise REQUEST_INVALID
            if start.policy == "compare":
                raise GatewayError(
                    400,
                    "ai_gateway_realtime_policy_not_implemented",
                    "Realtime comparison is not enabled yet.",
                )
            caller = principal.workload_id
            async with container.service.admission.lease(
                caller, "realtime_translation"
            ):
                request = RealtimeTranslationStart(
                    type=start.type,
                    request_id=start.request_id,
                    product=start.product,
                    profile=start.profile,
                    target_language=start.target_language,
                    instructions=start.instructions,
                    audio_sample_rate_hz=start.audio_sample_rate_hz,
                    outputs=start.outputs,
                )
                usage_state = {
                    "started_at": datetime.now(timezone.utc),
                    "finished_at": None,
                    "disposition": "disconnected",
                    "selected_profile": start.profile,
                    "source_audio_received_bytes": 0,
                    "source_audio_accepted_bytes": 0,
                    "fallback_count": 0,
                    "rotation_count": 0,
                    "delivered": False,
                }
                if start.fallback_profile is not None:
                    container.service.validate_realtime_translation_route(
                        caller=caller,
                        request=request,
                        primary_profile_name=start.profile,
                        profile_name=start.fallback_profile,
                    )

                async def open_profile(profile: str):
                    return await container.service.open_realtime_translation(
                        caller=caller,
                        request=request.model_copy(update={"profile": profile}),
                    )

                async def deliver_usage() -> None:
                    assert usage_state is not None
                    if usage_state["delivered"]:
                        return
                    if usage_state["finished_at"] is None:
                        usage_state["finished_at"] = datetime.now(timezone.utc)
                    await container.service.deliver_realtime_usage(
                        request_id=start.request_id,
                        caller=caller,
                        request=request,
                        selected_profile=str(usage_state["selected_profile"]),
                        started_at=usage_state["started_at"],
                        finished_at=usage_state["finished_at"],
                        disposition=usage_state["disposition"],
                        source_audio_received_bytes=int(
                            usage_state["source_audio_received_bytes"]
                        ),
                        source_audio_accepted_bytes=int(
                            usage_state["source_audio_accepted_bytes"]
                        ),
                        fallback_count=int(usage_state["fallback_count"]),
                        rotation_count=int(usage_state["rotation_count"]),
                    )
                    usage_state["delivered"] = True

                usage_delivery_callback = deliver_usage

                session = await open_profile(start.profile)
                session_ref[0] = session
                await _bridge_v2(
                    websocket,
                    session,
                    request_id=start.request_id,
                    profile=start.profile,
                    window_seconds=start.window_seconds,
                    fallback_profile=start.fallback_profile,
                    alternate=start.policy == "windowed_alternate",
                    audio_sample_rate_hz=start.audio_sample_rate_hz,
                    outputs=start.outputs,
                    target_language=start.target_language,
                    open_profile=open_profile,
                    session_ref=session_ref,
                    record_failure=lambda profile_name: (
                        container.service.record_realtime_route_failure(
                            caller=caller, request=request, profile_name=profile_name
                        )
                    ),
                    usage_state=usage_state,
                    deliver_usage=usage_delivery_callback,
                    is_draining=lambda: container.draining,
                )
        except WebSocketDisconnect:
            pass
        except GatewayError as error:
            await _safe_error(websocket, error)
        finally:
            if session_ref[0] is not None:
                await session_ref[0].close()
            elif session is not None:
                await session.close()
            if (
                usage_state is not None
                and usage_delivery_callback is not None
                and not usage_state["delivered"]
            ):
                try:
                    await usage_delivery_callback()
                except GatewayError:
                    # The WebSocket may already be unavailable. The durable
                    # sink retry was exhausted; the redacted failure counter
                    # keeps the unresolved reconciliation state observable.
                    pass

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


async def _bridge_v2(
    websocket: WebSocket,
    session,
    *,
    request_id: UUID,
    profile: str,
    window_seconds: int = 90,
    fallback_profile: str | None = None,
    alternate: bool = False,
    open_profile=None,
    session_ref=None,
    record_failure=None,
    audio_sample_rate_hz: int = 24000,
    outputs: list[str] | None = None,
    target_language: str = "",
    usage_state: dict[str, object] | None = None,
    deliver_usage=None,
    is_draining=None,
) -> None:
    max_chunk_bytes = 256 * 1024
    # The bridge awaits provider acceptance before reading another client frame;
    # Gateway-owned in-flight audio is therefore bounded to one chunk.
    max_unacknowledged_chunks = 1
    max_unacknowledged_bytes = max_chunk_bytes
    window_id = f"w-{uuid4()}"
    session_id = str(request_id)
    lane_id = "primary"
    output_sequence = 0
    accepted_sequence = 0
    input_sequence = 0
    accepted_chunks = 0
    usage_final_sent = False
    switch_count = 0
    rotation_count = 0
    max_switches = 8
    last_switch_at: float | None = None
    switch_cooldown_seconds = 5.0
    window_started = time.monotonic()
    session_started = window_started

    def update_usage_state() -> None:
        if usage_state is None:
            return
        usage_state["selected_profile"] = active_profile
        usage_state["fallback_count"] = switch_count
        usage_state["rotation_count"] = rotation_count

    async def send_event(value: dict[str, object]) -> None:
        await _send_realtime_event(websocket, value)

    async def send_usage_final(disposition: str) -> None:
        nonlocal output_sequence, usage_final_sent
        if usage_final_sent:
            return
        usage_final_sent = True
        update_usage_state()
        if usage_state is not None:
            usage_state["disposition"] = disposition
            if usage_state["finished_at"] is None:
                usage_state["finished_at"] = datetime.now(timezone.utc)
        if deliver_usage is not None:
            await deliver_usage()
        output_sequence += 1
        await send_event(
            {
                "type": "usage.final",
                "session_id": session_id,
                "window_id": window_id,
                "lane_id": lane_id,
                "provider_ref": active_profile,
                "sequence": output_sequence,
                "duration_ms": max(0, int((time.monotonic() - session_started) * 1000)),
                "accepted_input_chunks": accepted_chunks,
                "fallback_count": switch_count,
                "rotation_count": rotation_count,
            }
        )

    async def close_for_provider_output_violation() -> None:
        nonlocal output_sequence
        if record_failure is not None:
            record_failure(active_profile)
        await send_usage_final("provider_failed")
        output_sequence += 1
        await send_event(
            {
                "type": "session.closed",
                "session_id": session_id,
                "window_id": window_id,
                "lane_id": lane_id,
                "provider_ref": active_profile,
                "sequence": output_sequence,
                "accepted_sequence": accepted_sequence,
                "output_sequence": output_sequence,
                "disposition": "provider_failed",
                "retryable": False,
                "failure_stage": "provider_output",
            }
        )

    active_profile = profile
    await send_event(
        {
            "type": "session.ready",
            "session_id": session_id,
            "request_id": str(request_id),
            "window_id": window_id,
            "lane_id": lane_id,
            "profile": profile,
            "provider_ref": active_profile,
            "sequence": 0,
            "max_chunk_bytes": max_chunk_bytes,
            "max_unacknowledged_chunks": max_unacknowledged_chunks,
            "max_unacknowledged_bytes": max_unacknowledged_bytes,
            "max_outbound_events": 1,
            "audio_sample_rate_hz": audio_sample_rate_hz,
            "outputs": outputs or ["target_transcript", "translated_audio"],
        }
    )
    while True:
        client_task = asyncio.create_task(websocket.receive_json())
        provider_task = asyncio.create_task(session.next_event())
        drain_task = (
            asyncio.create_task(_wait_until_draining(is_draining))
            if is_draining is not None
            else None
        )
        wait_tasks = {client_task, provider_task}
        if drain_task is not None:
            wait_tasks.add(drain_task)
        done, pending = await asyncio.wait(
            wait_tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if drain_task is not None and drain_task in done:
            rotation_count += 1
            output_sequence += 1
            await send_event(
                {
                    "type": "session.rotation_required",
                    "session_id": session_id,
                    "window_id": window_id,
                    "lane_id": lane_id,
                    "provider_ref": active_profile,
                    "sequence": output_sequence,
                    "deadline_ms": int(time.time() * 1000) + 1000,
                    "accepted_sequence": accepted_sequence,
                }
            )
            await send_usage_final("cancelled")
            output_sequence += 1
            await send_event(
                {
                    "type": "session.closed",
                    "session_id": session_id,
                    "window_id": window_id,
                    "lane_id": lane_id,
                    "provider_ref": active_profile,
                    "sequence": output_sequence,
                    "accepted_sequence": accepted_sequence,
                    "output_sequence": output_sequence,
                    "disposition": "cancelled",
                    "retryable": True,
                    "failure_stage": "gateway",
                }
            )
            return
        if provider_task in done:
            event = provider_task.result()
            output_sequence += 1
            if event.type == "error":
                if event.code == "provider_session_rotation_required":
                    rotation_count += 1
                    previous_window_id = window_id
                    previous_profile = active_profile
                    await send_event(
                        {
                            "type": "session.rotation_required",
                            "session_id": session_id,
                            "window_id": previous_window_id,
                            "lane_id": lane_id,
                            "provider_ref": previous_profile,
                            "sequence": output_sequence,
                            "deadline_ms": int(time.time() * 1000),
                            "accepted_sequence": accepted_sequence,
                        }
                    )
                    try:
                        await session.close()
                        if open_profile is None:
                            raise RuntimeError("realtime profile opener is unavailable")
                        session = await open_profile(active_profile)
                        if session_ref is not None:
                            session_ref[0] = session
                    except Exception:
                        if record_failure is not None:
                            record_failure(active_profile)
                        await send_usage_final("provider_failed")
                        output_sequence += 1
                        await send_event(
                            {
                                "type": "session.closed",
                                "session_id": session_id,
                                "window_id": previous_window_id,
                                "lane_id": lane_id,
                                "provider_ref": previous_profile,
                                "sequence": output_sequence,
                                "accepted_sequence": accepted_sequence,
                                "output_sequence": output_sequence,
                                "disposition": "provider_failed",
                                "retryable": True,
                                "failure_stage": "provider_start",
                            }
                        )
                        return
                    window_id = f"w-{uuid4()}"
                    window_started = time.monotonic()
                    accepted_sequence = 0
                    output_sequence += 1
                    await send_event(
                        {
                            "type": "window.closed",
                            "session_id": session_id,
                            "window_id": previous_window_id,
                            "lane_id": lane_id,
                            "provider_ref": previous_profile,
                            "sequence": output_sequence,
                            "accepted_sequence": input_sequence,
                            "output_sequence": output_sequence,
                            "partial": False,
                            "disposition": "complete",
                        }
                    )
                    continue
                if record_failure is not None:
                    record_failure(active_profile)
                switch_count += 1
                if switch_count > max_switches:
                    await send_usage_final("provider_failed")
                    output_sequence += 1
                    await send_event(
                        {
                            "type": "session.closed",
                            "session_id": session_id,
                            "window_id": window_id,
                            "lane_id": lane_id,
                            "provider_ref": active_profile,
                            "sequence": output_sequence,
                            "accepted_sequence": accepted_sequence,
                            "output_sequence": output_sequence,
                            "disposition": "provider_failed",
                            "retryable": False,
                            "failure_stage": "provider_stream",
                        }
                    )
                    return
                await send_event(
                    {
                        "type": "window.failed",
                        "session_id": session_id,
                        "window_id": window_id,
                        "lane_id": lane_id,
                        "provider_ref": active_profile,
                        "sequence": output_sequence,
                        "accepted_sequence": accepted_sequence,
                        "partial": accepted_sequence > 0,
                        "retryable": True,
                        "disposition": "provider_failed",
                        "failure_stage": "provider_stream",
                    }
                )
                if fallback_profile is None or open_profile is None:
                    await send_usage_final("provider_failed")
                    output_sequence += 1
                    await send_event(
                        {
                            "type": "session.closed",
                            "session_id": session_id,
                            "window_id": window_id,
                            "lane_id": lane_id,
                            "provider_ref": active_profile,
                            "sequence": output_sequence,
                            "accepted_sequence": accepted_sequence,
                            "output_sequence": output_sequence,
                            "disposition": "provider_failed",
                            "retryable": True,
                            "failure_stage": "provider_stream",
                        }
                    )
                    return
                now = time.monotonic()
                if (
                    last_switch_at is not None
                    and now - last_switch_at < switch_cooldown_seconds
                ):
                    await send_usage_final("provider_failed")
                    output_sequence += 1
                    await send_event(
                        {
                            "type": "session.closed",
                            "session_id": session_id,
                            "window_id": window_id,
                            "lane_id": lane_id,
                            "provider_ref": active_profile,
                            "sequence": output_sequence,
                            "accepted_sequence": accepted_sequence,
                            "output_sequence": output_sequence,
                            "disposition": "provider_failed",
                            "retryable": True,
                            "failure_stage": "provider_stream",
                        }
                    )
                    return
                try:
                    await session.close()
                    session = await open_profile(fallback_profile)
                    if session_ref is not None:
                        session_ref[0] = session
                except Exception:
                    if record_failure is not None:
                        record_failure(fallback_profile)
                    await send_usage_final("provider_failed")
                    output_sequence += 1
                    await send_event(
                        {
                            "type": "session.closed",
                            "session_id": session_id,
                            "window_id": window_id,
                            "lane_id": lane_id,
                            "provider_ref": active_profile,
                            "sequence": output_sequence,
                            "accepted_sequence": accepted_sequence,
                            "output_sequence": output_sequence,
                            "disposition": "provider_failed",
                            "retryable": True,
                            "failure_stage": "provider_start",
                        }
                    )
                    return
                previous_profile = active_profile
                active_profile = fallback_profile
                last_switch_at = time.monotonic()
                window_id = f"w-{uuid4()}"
                window_started = time.monotonic()
                accepted_sequence = 0
                output_sequence += 1
                await send_event(
                    {
                        "type": "provider.switched",
                        "session_id": session_id,
                        "window_id": window_id,
                        "lane_id": lane_id,
                        "provider_ref": active_profile,
                        "sequence": output_sequence,
                        "from_provider": previous_profile,
                        "to_provider": active_profile,
                        "reason": "provider_unavailable",
                    }
                )
                continue
            if event.type not in _NORMALIZED_REALTIME_PROVIDER_EVENTS:
                await close_for_provider_output_violation()
                return
            if not _provider_event_matches_requested_outputs(event.type, outputs):
                await close_for_provider_output_violation()
                return
            payload = {
                "type": event.type,
                "session_id": session_id,
                "window_id": window_id,
                "lane_id": lane_id,
                "provider_ref": active_profile,
                "sequence": output_sequence,
            }
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
            if event.type.startswith("translation.audio."):
                payload["response_id"] = event.item_id or (
                    f"{session_id}:{window_id}:{output_sequence}"
                )
                payload["target_language"] = target_language
                if event.sample_format is not None:
                    payload["sample_format"] = event.sample_format
                elif event.content_type is not None and event.content_type.startswith(
                    "audio/pcm"
                ):
                    payload["sample_format"] = "s16le"
                if event.type == "translation.audio.final":
                    payload["disposition"] = "complete"
            await send_event(payload)
        if client_task in done:
            raw = client_task.result()
            event_type = raw.get("type") if isinstance(raw, dict) else None
            try:
                if event_type == "audio.append":
                    append = RealtimeV2AudioAppend.model_validate(raw)
                    if append.sequence != input_sequence + 1:
                        raise REQUEST_INVALID
                    audio_bytes = _decoded_audio_size(append.audio)
                    if audio_bytes > max_chunk_bytes:
                        raise REQUEST_INVALID
                    if usage_state is not None:
                        usage_state["source_audio_received_bytes"] = (
                            int(usage_state["source_audio_received_bytes"])
                            + audio_bytes
                        )
                    if time.monotonic() - window_started >= window_seconds:
                        rotation_count += 1
                        previous_window_id = window_id
                        next_profile = (
                            (
                                profile
                                if active_profile == fallback_profile
                                else fallback_profile
                            )
                            if alternate and fallback_profile is not None
                            else active_profile
                        )
                        previous_profile = active_profile
                        if (
                            last_switch_at is not None
                            and time.monotonic() - last_switch_at
                            < switch_cooldown_seconds
                        ):
                            await session.append(append.audio)
                            input_sequence = append.sequence
                            accepted_sequence = input_sequence
                            accepted_chunks += 1
                            if usage_state is not None:
                                usage_state["source_audio_accepted_bytes"] = (
                                    int(usage_state["source_audio_accepted_bytes"])
                                    + audio_bytes
                                )
                            output_sequence += 1
                            await send_event(
                                {
                                    "type": "audio.accepted",
                                    "session_id": session_id,
                                    "window_id": window_id,
                                    "lane_id": lane_id,
                                    "provider_ref": active_profile,
                                    "sequence": output_sequence,
                                    "accepted_sequence": accepted_sequence,
                                }
                            )
                            continue
                        output_sequence += 1
                        await send_event(
                            {
                                "type": "session.rotation_required",
                                "session_id": session_id,
                                "window_id": previous_window_id,
                                "lane_id": lane_id,
                                "provider_ref": active_profile,
                                "sequence": output_sequence,
                                "deadline_ms": int(time.time() * 1000),
                                "accepted_sequence": accepted_sequence,
                            }
                        )
                        await session.close()
                        session = await open_profile(next_profile)
                        if session_ref is not None:
                            session_ref[0] = session
                        window_id = f"w-{uuid4()}"
                        window_started = time.monotonic()
                        accepted_sequence = 0
                        output_sequence += 1
                        await send_event(
                            {
                                "type": "window.closed",
                                "session_id": session_id,
                                "window_id": previous_window_id,
                                "lane_id": lane_id,
                                "provider_ref": previous_profile,
                                "sequence": output_sequence,
                                "accepted_sequence": input_sequence,
                                "output_sequence": output_sequence,
                                "partial": False,
                                "disposition": "complete",
                            }
                        )
                        if alternate and fallback_profile is not None:
                            switch_count += 1
                            if switch_count > max_switches:
                                await send_usage_final("provider_failed")
                                output_sequence += 1
                                await send_event(
                                    {
                                        "type": "session.closed",
                                        "session_id": session_id,
                                        "window_id": window_id,
                                        "lane_id": lane_id,
                                        "provider_ref": active_profile,
                                        "sequence": output_sequence,
                                        "accepted_sequence": accepted_sequence,
                                        "output_sequence": output_sequence,
                                        "disposition": "provider_failed",
                                    }
                                )
                                return
                            active_profile = next_profile
                            last_switch_at = time.monotonic()
                            output_sequence += 1
                            await send_event(
                                {
                                    "type": "provider.switched",
                                    "session_id": session_id,
                                    "window_id": window_id,
                                    "lane_id": lane_id,
                                    "provider_ref": active_profile,
                                    "sequence": output_sequence,
                                    "from_provider": previous_profile,
                                    "to_provider": active_profile,
                                    "reason": "scheduled_alternate",
                                }
                            )
                    await session.append(append.audio)
                    input_sequence = append.sequence
                    accepted_sequence = input_sequence
                    accepted_chunks += 1
                    if usage_state is not None:
                        usage_state["source_audio_accepted_bytes"] = (
                            int(usage_state["source_audio_accepted_bytes"])
                            + audio_bytes
                        )
                    output_sequence += 1
                    await send_event(
                        {
                            "type": "audio.accepted",
                            "session_id": session_id,
                            "window_id": window_id,
                            "lane_id": lane_id,
                            "provider_ref": active_profile,
                            "sequence": output_sequence,
                            "accepted_sequence": accepted_sequence,
                        }
                    )
                else:
                    command = RealtimeCommand.model_validate(raw)
                    if command.type == "audio.commit":
                        await session.commit()
                    elif command.type == "audio.clear":
                        await session.clear()
                    else:
                        await send_usage_final("cancelled")
                        output_sequence += 1
                        await send_event(
                            {
                                "type": "session.closed",
                                "session_id": session_id,
                                "window_id": window_id,
                                "lane_id": lane_id,
                                "provider_ref": active_profile,
                                "sequence": output_sequence,
                                "accepted_sequence": accepted_sequence,
                                "output_sequence": output_sequence,
                                "disposition": "cancelled",
                            }
                        )
                        return
            except ValidationError as error:
                raise REQUEST_INVALID from error


def _decoded_audio_size(value: str) -> int:
    try:
        return len(base64.b64decode(value, validate=True))
    except (binascii.Error, ValueError) as error:
        raise REQUEST_INVALID from error


async def _wait_until_draining(is_draining) -> None:
    while not is_draining():
        await asyncio.sleep(0.05)


async def _send_realtime_event(
    websocket: WebSocket, value: dict[str, object], *, timeout_seconds: float = 5
) -> None:
    """Send directly with no Gateway-owned outbound queue.

    Waiting for each send keeps at most one event in flight. A caller that does
    not drain receives a temporary-overload close rather than retained audio or
    text buffers.
    """
    try:
        await asyncio.wait_for(websocket.send_json(value), timeout=timeout_seconds)
    except asyncio.TimeoutError as error:
        try:
            await websocket.close(code=1013, reason="slow_consumer")
        finally:
            raise WebSocketDisconnect(code=1013) from error


async def _safe_error(websocket: WebSocket, error: GatewayError) -> None:
    try:
        await websocket.send_json(
            {
                "type": "error",
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                    "retry_after_ms": error.retry_after_ms,
                },
            }
        )
        await websocket.close(code=1011 if error.retryable else 1008)
    except Exception:
        pass
