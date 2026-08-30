from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time
import uuid
import wave
from collections import Counter
from pathlib import Path

import websockets


async def run(wav_path: Path, *, timeout_seconds: float) -> None:
    tokens = json.loads(os.environ["AI_GATEWAY_SERVICE_TOKENS_JSON"])
    caller = os.environ.get("AI_GATEWAY_SMOKE_CALLER", "dali_classroom_server")
    token = tokens[caller]
    with wave.open(str(wav_path), "rb") as audio_file:
        if (
            audio_file.getframerate() != 24000
            or audio_file.getnchannels() != 1
            or audio_file.getsampwidth() != 2
        ):
            raise ValueError("The smoke WAV must be 24 kHz, mono, PCM16.")
        pcm = audio_file.readframes(audio_file.getnframes())

    counts: Counter[str] = Counter()
    started = time.monotonic()
    async with websockets.connect(
        "ws://127.0.0.1:5040/ai/v1/realtime/transcriptions",
        additional_headers={
            "Authorization": f"Bearer {token}",
            "X-Dali-Caller": caller,
        },
        open_timeout=10,
    ) as socket:
        await socket.send(
            json.dumps(
                {
                    "type": "session.start",
                    "request_id": str(uuid.uuid4()),
                    "product": "classroom",
                    "profile": "classroom.transcription.live",
                    "source_language": "en",
                    "terminology_prompt": "Biology",
                    "terminology_keywords": ["photosynthesis", "chlorophyll"],
                    "audio_sample_rate_hz": 24000,
                }
            )
        )
        ready = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
        counts[str(ready.get("type"))] += 1
        for offset in range(0, len(pcm), 12000):
            await socket.send(
                json.dumps(
                    {
                        "type": "audio.append",
                        "audio": base64.b64encode(pcm[offset : offset + 12000]).decode(
                            "ascii"
                        ),
                    }
                )
            )
        await socket.send(json.dumps({"type": "audio.commit"}))
        final_received = False
        error_received = False
        while time.monotonic() - started < timeout_seconds:
            remaining = timeout_seconds - (time.monotonic() - started)
            try:
                event = json.loads(
                    await asyncio.wait_for(socket.recv(), timeout=max(remaining, 0.1))
                )
            except TimeoutError:
                break
            event_type = str(event.get("type"))
            counts[event_type] += 1
            final_received = final_received or event_type == "transcript.final"
            error_received = error_received or event_type == "error"
            if final_received or error_received:
                break
    print(
        json.dumps(
            {
                "event_counts": dict(counts),
                "final_received": final_received,
                "error_received": error_received,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            },
            separators=(",", ":"),
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    asyncio.run(run(args.wav, timeout_seconds=args.timeout))


if __name__ == "__main__":
    main()
