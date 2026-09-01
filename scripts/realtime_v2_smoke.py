from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import uuid
import wave
from collections import Counter
from pathlib import Path

import websockets


async def run(wav_path: Path, *, timeout_seconds: float, policy: str) -> None:
    tokens = json.loads(os.environ["AI_GATEWAY_SERVICE_TOKENS_JSON"])
    caller = os.environ.get("AI_GATEWAY_SMOKE_CALLER", "dali_chat_server")
    token = tokens[caller]
    with wave.open(str(wav_path), "rb") as audio_file:
        if audio_file.getframerate() != 16000 or audio_file.getnchannels() != 1 or audio_file.getsampwidth() != 2:
            raise ValueError("The v2 smoke WAV must be 16 kHz, mono, PCM16.")
        pcm = audio_file.readframes(audio_file.getnframes())

    counts: Counter[str] = Counter()
    request_id = str(uuid.uuid4())
    async with websockets.connect(
        "ws://127.0.0.1:5040/ai/v2/realtime/translations",
        additional_headers={"Authorization": f"Bearer {token}", "X-Dali-Caller": caller},
        open_timeout=10,
    ) as socket:
        start = {
            "type": "session.start", "request_id": request_id,
            "product": "dali_chat", "profile": "dali_chat.interpret.openai",
            "target_language": "zh-CN", "audio_sample_rate_hz": 16000,
        }
        if policy != "single":
            start["policy"] = policy
            start["window_seconds"] = 120
            start["fallback_profile"] = "dali_chat.interpret.gemini"
        await socket.send(json.dumps(start))
        ready = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
        counts[str(ready.get("type"))] += 1
        if ready.get("session_id") != request_id:
            raise RuntimeError(
                f"session.ready identity mismatch: type={ready.get('type')} code="
                f"{(ready.get('error') or {}).get('code')}"
            )
        for sequence, offset in enumerate(range(0, len(pcm), 12000), start=1):
            await socket.send(json.dumps({
                "type": "audio.append", "sequence": sequence,
                "audio": base64.b64encode(pcm[offset : offset + 12000]).decode("ascii"),
            }))
            event = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
            counts[str(event.get("type"))] += 1
            if event.get("type") != "audio.accepted":
                raise RuntimeError(f"expected audio.accepted, got {event.get('type')}")
        await socket.send(json.dumps({"type": "audio.commit"}))
        await socket.send(json.dumps({"type": "session.stop"}))
        while True:
            event = json.loads(await asyncio.wait_for(socket.recv(), timeout=timeout_seconds))
            event_type = str(event.get("type"))
            counts[event_type] += 1
            if event_type == "session.closed":
                break
    print(json.dumps({"request_id": request_id, "policy": policy, "event_counts": dict(counts)}, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    parser.add_argument("--policy", choices=("single", "windowed_failover", "windowed_alternate"), default="single")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    asyncio.run(run(args.wav, timeout_seconds=args.timeout, policy=args.policy))


if __name__ == "__main__":
    main()
