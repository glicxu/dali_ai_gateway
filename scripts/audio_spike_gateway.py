"""Loopback-only spike factory, separate from the deployed Gateway entrypoint."""

import io
import json
import math
import os
import struct
import wave

from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app
from app.models import UsageMeasurement
from app.providers.base import SpeechResult


class ToneProvider:
    """Synthetic playback fixture. Never claims to synthesize the supplied text."""

    async def probe(self):
        pass

    async def close(self):
        pass

    async def synthesize(self, **kwargs):
        audio = io.BytesIO()
        with wave.open(audio, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(
                b"".join(
                    struct.pack(
                        "<h", int(2000 * math.sin(2 * math.pi * 440 * i / 24000))
                    )
                    for i in range(12000)
                )
            )
        return SpeechResult(audio.getvalue(), "audio/wav", UsageMeasurement())


def app_factory():
    provider = os.environ.get("DALI_AUDIO_SPIKE_PROVIDER", "fake")
    if provider not in {"fake", "openai", "gemini"}:
        raise ValueError("Invalid spike provider")
    profile = (
        "dali_chat.speech.gemini" if provider == "gemini" else "dali_chat.speech.openai"
    )
    model = {
        "fake": "synthetic-tone",
        "openai": "gpt-4o-mini-tts",
        "gemini": "gemini-3.1-flash-tts-preview",
    }[provider]
    voice = {"fake": "tone", "openai": "alloy", "gemini": "Kore"}[provider]
    settings = Settings(
        _env_file=None,
        service_tokens_json=SecretStr(
            json.dumps(
                {
                    "dali_chat_server": os.environ["DALI_AUDIO_SPIKE_SERVICE_TOKEN"],
                }
            )
        ),
        legacy_auth_workload_ids_json='["dali_chat_server"]',
        caller_limits_json='{"dali_chat_server":1}',
        policy_generation_id="audio-spike-local-v1",
        workload_grants_json=json.dumps(
            {
                "dali_chat_server": {
                    "products": ["dali_chat"],
                    "profiles": [profile],
                    "capabilities": ["speech_synthesis"],
                    "enabled": True,
                }
            }
        ),
        model_profiles_json=json.dumps(
            {
                profile: {
                    "capability": "speech_synthesis",
                    "provider": "spike" if provider == "fake" else provider,
                    "model": model,
                    "voice_routes": {"narrator_main": voice},
                    "max_input_bytes": 16000,
                    "capacity_pool": "audio_spike",
                }
            }
        ),
    )
    return create_app(
        settings, providers={"spike": ToneProvider()} if provider == "fake" else None
    )
