from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.core.realtime_transcription_hedge import bridge_hedged_transcription
from app.providers.base import RealtimeEvent


@dataclass
class _Session:
    final_on_commit: str | None = None
    appended: list[str] = field(default_factory=list)
    committed: int = 0
    closed: bool = False
    events: asyncio.Queue[RealtimeEvent] = field(default_factory=asyncio.Queue)

    async def append(self, audio: str) -> None:
        self.appended.append(audio)

    async def commit(self) -> None:
        self.committed += 1
        if self.final_on_commit is not None:
            await self.events.put(
                RealtimeEvent("transcript.final", text=self.final_on_commit)
            )

    async def clear(self) -> None:
        return None

    async def next_event(self) -> RealtimeEvent:
        return await self.events.get()

    async def close(self) -> None:
        self.closed = True


class _Socket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.sent: list[dict[str, object]] = []
        self.sent_event = asyncio.Event()

    async def receive_json(self) -> dict[str, object]:
        return await self.incoming.get()

    async def send_json(self, value: dict[str, object]) -> None:
        self.sent.append(value)
        self.sent_event.set()


async def _wait_for_final(socket: _Socket) -> None:
    for _ in range(100):
        if any(item.get("type") == "transcript.final" for item in socket.sent):
            return
        socket.sent_event.clear()
        try:
            await asyncio.wait_for(socket.sent_event.wait(), timeout=0.05)
        except TimeoutError:
            pass
    raise AssertionError("no transcript.final was delivered")


def test_delayed_hedge_replays_only_after_primary_stalls() -> None:
    async def scenario() -> None:
        socket = _Socket()
        primary = _Session()
        fallback = _Session(final_on_commit="Fallback transcript.")

        async def open_profile(_profile: str) -> _Session:
            return fallback

        task = asyncio.create_task(
            bridge_hedged_transcription(
                socket,  # type: ignore[arg-type]
                primary,
                primary_profile="primary",
                fallback_profile="fallback",
                open_profile=open_profile,  # type: ignore[arg-type]
                hedge_delay_seconds=0.01,
                max_buffer_bytes=64,
            )
        )
        await socket.incoming.put({"type": "audio.append", "audio": "AQI="})
        await socket.incoming.put({"type": "audio.commit"})
        await _wait_for_final(socket)
        await socket.incoming.put({"type": "session.stop"})
        await task

        assert primary.appended == ["AQI="]
        assert primary.committed == 1
        assert fallback.appended == ["AQI="]
        assert fallback.committed == 1
        assert [item["text"] for item in socket.sent] == ["Fallback transcript."]

    asyncio.run(scenario())


def test_healthy_primary_does_not_send_audio_to_standby() -> None:
    async def scenario() -> None:
        socket = _Socket()
        primary = _Session(final_on_commit="Primary transcript.")
        fallback = _Session(final_on_commit="Duplicate transcript.")

        async def open_profile(_profile: str) -> _Session:
            return fallback

        task = asyncio.create_task(
            bridge_hedged_transcription(
                socket,  # type: ignore[arg-type]
                primary,
                primary_profile="primary",
                fallback_profile="fallback",
                open_profile=open_profile,  # type: ignore[arg-type]
                hedge_delay_seconds=0.05,
                max_buffer_bytes=64,
            )
        )
        await socket.incoming.put({"type": "audio.append", "audio": "AQI="})
        await socket.incoming.put({"type": "audio.commit"})
        await _wait_for_final(socket)
        await socket.incoming.put({"type": "session.stop"})
        await task

        assert fallback.appended == []
        assert fallback.committed == 0
        assert [item["text"] for item in socket.sent] == ["Primary transcript."]

    asyncio.run(scenario())


def test_new_commit_replaces_stale_hedge_without_closing_client() -> None:
    async def scenario() -> None:
        socket = _Socket()
        initial_primary = _Session()
        first_fallback = _Session()
        replacement_primary = _Session(final_on_commit="Recovered transcript.")
        replacement_fallback = _Session()
        opened = {
            "primary": [replacement_primary],
            "fallback": [first_fallback, replacement_fallback],
        }

        async def open_profile(profile: str) -> _Session:
            return opened[profile].pop(0)

        task = asyncio.create_task(
            bridge_hedged_transcription(
                socket,  # type: ignore[arg-type]
                initial_primary,
                primary_profile="primary",
                fallback_profile="fallback",
                open_profile=open_profile,  # type: ignore[arg-type]
                hedge_delay_seconds=0.01,
                max_buffer_bytes=64,
            )
        )
        await socket.incoming.put({"type": "audio.append", "audio": "AQI="})
        await socket.incoming.put({"type": "audio.commit"})
        await asyncio.sleep(0.03)
        await socket.incoming.put({"type": "audio.append", "audio": "AwQ="})
        await socket.incoming.put({"type": "audio.commit"})
        await _wait_for_final(socket)
        await socket.incoming.put({"type": "session.stop"})
        await task

        assert initial_primary.closed is True
        assert first_fallback.closed is True
        assert replacement_primary.appended == ["AwQ="]
        assert replacement_primary.committed == 1
        assert [item["text"] for item in socket.sent] == [
            "Recovered transcript."
        ]

    asyncio.run(scenario())
