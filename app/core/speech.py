"""Content-free speech configuration identity; no product narration state."""

from hashlib import sha256
import json

from app.core.policy import ModelProfile


def speech_configuration_id(profile: ModelProfile) -> str:
    # Hash the resolved immutable profile, not a separately read policy generation.
    # Unrelated policy reloads should not invalidate a narration revision.
    value = {
        "contract": "speech.v1",
        "profile": profile.name,
        "provider": profile.provider,
        "model": profile.model,
        "voice_routes": dict(profile.voice_routes) if profile.voice_routes else None,
        "max_input_bytes": profile.max_input_bytes,
    }
    return sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()
