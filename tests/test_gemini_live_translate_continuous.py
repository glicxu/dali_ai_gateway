from __future__ import annotations

import asyncio
import base64
import json

from app.providers.gemini import GeminiRealtimeSession


class _QueuedSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict[str, object]] = []
        self.closed = False

    async def send(self, value: str) -> None:
        self.sent.append(json.loads(value))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def close(self) -> None:
        self.closed = True


def test_continuous_translate_commit_finalizes_and_drops_digital_silence() -> None:
    async def exercise() -> None:
        socket = _QueuedSocket()

        async def connect(_url: str, **_kwargs):
            return socket

        session = GeminiRealtimeSession(
            url="wss://gemini.test/live",
            model="gemini-3.5-live-translate-preview",
            mode="translation",
            setup_config={"generationConfig": {"responseModalities": ["AUDIO"]}},
            audio_sample_rate_hz=16000,
            connect=connect,
            max_duration_seconds=60,
            outputs=frozenset(
                {"source_transcript", "target_transcript", "translated_audio"}
            ),
        )
        await session.start()
        commit_task = asyncio.create_task(session.commit())
        while socket.sent[-1] != {"realtimeInput": {"audioStreamEnd": True}}:
            await asyncio.sleep(0)
        await socket.incoming.put(
            json.dumps(
                {
                    "serverContent": {
                        "inputTranscription": {"text": "Hello."},
                        "outputTranscription": {"text": "Hola."},
                        "modelTurn": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "data": base64.b64encode(b"\x01\x00").decode(),
                                        "mimeType": "audio/pcm;rate=24000",
                                    }
                                }
                            ]
                        },
                    }
                }
            )
        )
        assert (await session.next_event()).type == "transcript.delta"
        assert (await session.next_event()).type == "translation.audio.delta"
        assert (await session.next_event()).type == "translation.delta"

        await socket.incoming.put(
            json.dumps(
                {
                    "serverContent": {
                        "modelTurn": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "data": base64.b64encode(b"\x00" * 4800).decode(),
                                        "mimeType": "audio/pcm;rate=24000",
                                    }
                                }
                            ]
                        }
                    }
                }
            )
        )
        await commit_task

        assert socket.sent[-1] == {"realtimeInput": {"audioStreamEnd": True}}
        source_final = await session.next_event()
        target_final = await session.next_event()
        audio_final = await session.next_event()
        assert (source_final.type, source_final.text) == (
            "transcript.final",
            "Hello.",
        )
        assert (target_final.type, target_final.text) == (
            "translation.final",
            "Hola.",
        )
        assert audio_final.type == "translation.audio.final"
        assert audio_final.item_id == "gemini-1"
        try:
            await asyncio.wait_for(session.next_event(), timeout=0.01)
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("digital silence produced an output event")

        await session.close()
        assert socket.closed

    asyncio.run(exercise())
