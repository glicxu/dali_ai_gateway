from __future__ import annotations

import asyncio
import base64
import binascii
from collections import deque
from collections.abc import Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from app.core.errors import REQUEST_INVALID
from app.models import RealtimeAudioAppend, RealtimeCommand
from app.providers.base import RealtimeEvent, RealtimeTranscriptionSession


ProfileOpener = Callable[[str], Awaitable[RealtimeTranscriptionSession]]


async def bridge_hedged_transcription(
    websocket: WebSocket,
    primary: RealtimeTranscriptionSession,
    *,
    primary_profile: str,
    fallback_profile: str,
    open_profile: ProfileOpener,
    hedge_delay_seconds: float,
    max_buffer_bytes: int,
) -> None:
    """Run one bounded, delayed transcription hedge without retaining content."""
    active = primary
    active_profile = primary_profile
    standby: RealtimeTranscriptionSession | None = None
    standby_profile = fallback_profile
    audio: deque[tuple[str, int]] = deque()
    audio_bytes = 0
    committed_audio: list[str] = []
    commit_pending = False
    hedge_active = False
    client_task: asyncio.Task | None = None
    active_task: asyncio.Task | None = None
    standby_task: asyncio.Task | None = None
    hedge_task: asyncio.Task | None = None

    async def ensure_standby() -> RealtimeTranscriptionSession:
        nonlocal standby
        if standby is None:
            standby = await open_profile(standby_profile)
        return standby

    async def reset_standby() -> None:
        nonlocal standby, standby_task
        if standby_task is not None:
            standby_task.cancel()
            await asyncio.gather(standby_task, return_exceptions=True)
            standby_task = None
        if standby is not None:
            await standby.close()
            standby = None
        # Keep the alternate route warm. A failed warm-up does not disturb the
        # healthy active route; activation will retry it later.
        try:
            await ensure_standby()
        except Exception:
            standby = None

    async def activate_hedge() -> None:
        nonlocal hedge_active, standby, standby_task
        for attempt in range(2):
            candidate = await ensure_standby()
            try:
                for chunk in committed_audio:
                    await candidate.append(chunk)
                await candidate.commit()
                break
            except Exception:
                await candidate.close()
                standby = None
                if attempt == 1:
                    raise
        hedge_active = True
        standby_task = asyncio.create_task(candidate.next_event())

    async def promote_standby() -> None:
        nonlocal active, active_profile, active_task
        nonlocal standby, standby_profile, standby_task, hedge_active
        previous = active
        previous_profile = active_profile
        if active_task is not None:
            active_task.cancel()
            await asyncio.gather(active_task, return_exceptions=True)
        active = await ensure_standby()
        active_profile = standby_profile
        active_task = None
        standby = None
        standby_task = None
        standby_profile = previous_profile
        hedge_active = False
        await previous.close()
        await reset_standby()

    async def restart_routes_for_new_commit() -> None:
        """Abandon a stale commit while preserving the newer audio window."""
        nonlocal active, active_task, standby, standby_task
        nonlocal hedge_active, hedge_task, commit_pending, committed_audio
        for task in (active_task, standby_task, hedge_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (active_task, standby_task, hedge_task) if task),
            return_exceptions=True,
        )
        active_task = None
        standby_task = None
        hedge_task = None
        await active.close()
        if standby is not None and standby is not active:
            await standby.close()
        standby = None
        active = await open_profile(active_profile)
        # Audio received since the stale commit was already sent to the old
        # session, so replay that bounded current window to the replacement.
        for chunk, _ in audio:
            await active.append(chunk)
        commit_pending = False
        hedge_active = False
        committed_audio = []
        await reset_standby()

    async def send_provider_event(event: RealtimeEvent) -> None:
        payload: dict[str, object] = {"type": event.type}
        if event.text is not None:
            payload["text"] = event.text
        if event.item_id is not None:
            payload["item_id"] = event.item_id
        await websocket.send_json(payload)

    # Warm-up is intentionally best effort.
    try:
        standby = await open_profile(standby_profile)
    except Exception:
        standby = None

    try:
        while True:
            if client_task is None:
                client_task = asyncio.create_task(websocket.receive_json())
            if active_task is None:
                active_task = asyncio.create_task(active.next_event())
            tasks = {client_task, active_task}
            if standby_task is not None:
                tasks.add(standby_task)
            if hedge_task is not None:
                tasks.add(hedge_task)
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            if hedge_task is not None and hedge_task in done:
                hedge_task = None
                if commit_pending and not hedge_active:
                    try:
                        await activate_hedge()
                    except Exception:
                        await websocket.send_json(_provider_error())
                        return

            if active_task is not None and active_task in done:
                task = active_task
                active_task = None
                try:
                    event = task.result()
                except Exception:
                    event = RealtimeEvent("error")
                if event.type == "error":
                    if commit_pending:
                        if not hedge_active:
                            try:
                                await activate_hedge()
                            except Exception:
                                await websocket.send_json(_provider_error())
                                return
                    else:
                        # Preserve the current uncommitted window across an
                        # explicit primary failure, then continue on fallback.
                        committed_audio = [chunk for chunk, _ in audio]
                        candidate = await ensure_standby()
                        for chunk in committed_audio:
                            await candidate.append(chunk)
                        await promote_standby()
                else:
                    await send_provider_event(event)
                    if event.type == "transcript.final" and commit_pending:
                        commit_pending = False
                        committed_audio = []
                        if hedge_task is not None:
                            hedge_task.cancel()
                            await asyncio.gather(hedge_task, return_exceptions=True)
                            hedge_task = None
                        if hedge_active:
                            hedge_active = False
                            await reset_standby()

            if standby_task is not None and standby_task in done:
                task = standby_task
                standby_task = None
                try:
                    event = task.result()
                except Exception:
                    event = RealtimeEvent("error")
                if event.type == "error":
                    await websocket.send_json(_provider_error())
                    return
                if hedge_active:
                    await send_provider_event(event)
                    if event.type == "transcript.final" and commit_pending:
                        commit_pending = False
                        committed_audio = []
                        await promote_standby()
                    elif standby is not None:
                        standby_task = asyncio.create_task(standby.next_event())

            if client_task is not None and client_task in done:
                task = client_task
                client_task = None
                raw = task.result()
                event_type = raw.get("type") if isinstance(raw, dict) else None
                if event_type == "audio.append":
                    append = RealtimeAudioAppend.model_validate(raw)
                    try:
                        size = len(base64.b64decode(append.audio, validate=True))
                    except (binascii.Error, ValueError) as error:
                        raise REQUEST_INVALID from error
                    if size > max_buffer_bytes:
                        raise REQUEST_INVALID
                    await active.append(append.audio)
                    audio.append((append.audio, size))
                    audio_bytes += size
                    while audio_bytes > max_buffer_bytes and audio:
                        _, removed = audio.popleft()
                        audio_bytes -= removed
                else:
                    command = RealtimeCommand.model_validate(raw)
                    if command.type == "audio.commit":
                        if commit_pending:
                            await restart_routes_for_new_commit()
                        committed_audio = [chunk for chunk, _ in audio]
                        audio.clear()
                        audio_bytes = 0
                        commit_pending = True
                        hedge_active = False
                        await active.commit()
                        hedge_task = asyncio.create_task(
                            asyncio.sleep(hedge_delay_seconds)
                        )
                    elif command.type == "audio.clear":
                        audio.clear()
                        audio_bytes = 0
                        committed_audio = []
                        commit_pending = False
                        await active.clear()
                        if standby is not None:
                            await standby.clear()
                    else:
                        return
    except WebSocketDisconnect:
        return
    finally:
        for task in (client_task, active_task, standby_task, hedge_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(
                task
                for task in (client_task, active_task, standby_task, hedge_task)
                if task is not None
            ),
            return_exceptions=True,
        )
        await active.close()
        if standby is not None and standby is not active:
            await standby.close()


def _provider_error() -> dict[str, object]:
    return {
        "type": "error",
        "error": {
            "code": "ai_gateway_provider_unavailable",
            "message": "The AI providers are temporarily unavailable.",
            "retryable": True,
        },
    }
