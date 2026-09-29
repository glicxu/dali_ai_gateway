"""Configure the bounded Dali Audio conversation-preparation Gateway profile."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import tempfile
from pathlib import Path

from app.core.config import DEFAULT_WORKLOAD_GRANTS, Settings
from scripts.activate_interpreter_stage import _quoted, _values


WORKLOAD_ID = "dali_audio_server"
PROFILE_ID = "dali_audio.conversation.prepare"


def _replace(lines: list[str], updates: dict[str, str]) -> list[str]:
    remaining = dict(updates)
    result: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            result.append(f"{key}={remaining.pop(key)}")
        else:
            result.append(line)
    if remaining:
        if result and result[-1]:
            result.append("")
        result.append("# Bounded Dali Audio conversation preparation.")
        result.extend(f"{key}={value}" for key, value in remaining.items())
    return result


def configure(env_file: Path, *, model: str, backup_suffix: str) -> Path:
    """Add exactly one Audio-only structured-output profile, fail-closed."""
    env_file = env_file.resolve()
    original = env_file.read_text(encoding="utf-8")
    lines = original.splitlines()
    values = _values(lines)
    profiles = json.loads(values.get("AI_GATEWAY_MODEL_PROFILES_JSON", "{}"))
    grants = (
        json.loads(values["AI_GATEWAY_WORKLOAD_GRANTS_JSON"])
        if ("AI_GATEWAY_WORKLOAD_GRANTS_JSON" in values)
        else copy.deepcopy(DEFAULT_WORKLOAD_GRANTS)
    )
    limits = json.loads(
        values.get(
            "AI_GATEWAY_CALLER_LIMITS_JSON",
            str(Settings.model_fields["caller_limits_json"].default),
        )
    )
    if not isinstance(profiles, dict) or not isinstance(grants, dict):
        raise ValueError("Gateway profile or grant policy is malformed")
    if not isinstance(limits, dict):
        raise ValueError("Gateway caller-limit policy is malformed")

    profile = {
        "capability": "text_generation",
        "provider": "openai",
        "model": model,
        "required_for_readiness": False,
        "privacy_class": "restricted",
        "usage_authority": "non_authoritative",
        "supported_outputs": ["json_schema"],
        "max_input_bytes": 65536,
        "capacity_pool": "audio_test",
        "traffic_class": "background",
    }
    existing_profile = profiles.get(PROFILE_ID)
    if existing_profile not in (None, profile):
        raise ValueError("existing conversation-preparation profile differs")
    profiles[PROFILE_ID] = profile

    existing_grant = grants.get(WORKLOAD_ID)
    if not isinstance(existing_grant, dict):
        raise ValueError("Audio workload grant is missing or malformed")
    expected_grant = {
        "enabled": True,
        "products": ["dali_audio"],
        "profiles": [
            "dali_audio.speech.openai",
            "dali_audio.speech.gemini",
            PROFILE_ID,
        ],
        "capabilities": ["speech_synthesis", "text_generation"],
    }
    if existing_grant not in (
        {
            "enabled": True,
            "products": ["dali_audio"],
            "profiles": ["dali_audio.speech.openai", "dali_audio.speech.gemini"],
            "capabilities": ["speech_synthesis"],
        },
        expected_grant,
    ):
        raise ValueError("existing Audio workload grant differs")
    grants[WORKLOAD_ID] = expected_grant
    if limits.get(WORKLOAD_ID) not in (None, 1):
        raise ValueError("Audio caller limit differs from the one-request bound")
    limits[WORKLOAD_ID] = 1

    updates = {
        "AI_GATEWAY_POLICY_GENERATION_ID": "us3-audio-conversation-v1",
        "AI_GATEWAY_MODEL_PROFILES_JSON": _quoted(
            json.dumps(profiles, separators=(",", ":"), sort_keys=True)
        ),
        "AI_GATEWAY_WORKLOAD_GRANTS_JSON": _quoted(
            json.dumps(grants, separators=(",", ":"), sort_keys=True)
        ),
        "AI_GATEWAY_CALLER_LIMITS_JSON": _quoted(
            json.dumps(limits, separators=(",", ":"), sort_keys=True)
        ),
    }
    backup = env_file.with_name(f"{env_file.name}.bak.{backup_suffix}")
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    shutil.copy2(env_file, backup)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{env_file.name}.", dir=env_file.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write("\n".join(_replace(lines, updates)) + "\n")
            target.flush()
            os.fsync(target.fileno())
        stat = env_file.stat()
        os.chmod(temporary_name, stat.st_mode)
        if hasattr(os, "chown"):
            os.chown(temporary_name, stat.st_uid, stat.st_gid)
        os.replace(temporary_name, env_file)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--backup-suffix", required=True)
    args = parser.parse_args()
    backup = configure(
        args.env_file, model=args.model, backup_suffix=args.backup_suffix
    )
    print(f"configured profile={PROFILE_ID} backup={backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
