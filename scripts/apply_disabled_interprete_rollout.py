from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path


INTERPRETE_GRANT = {
    "enabled": False,
    "products": ["interprete"],
    "profiles": [
        "interprete.live_summary",
        "interprete.translation.text",
        "interprete.transcription.batch",
        "interprete.transcription.realtime",
        "interprete.translation.realtime",
        "interprete.speech.standard",
    ],
    "capabilities": [
        "text_generation",
        "audio_transcription",
        "realtime_transcription",
        "realtime_translation",
        "speech_synthesis",
    ],
}


def _read_values(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return values


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
        result.append("# Phase 6 disabled Interprete rollout metadata.")
        result.extend(f"{key}={value}" for key, value in remaining.items())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add the disabled Interprete workload without changing active grants."
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--backup-suffix", required=True)
    args = parser.parse_args()

    env_file = args.env_file.resolve()
    original = env_file.read_text(encoding="utf-8")
    lines = original.splitlines()
    values = _read_values(lines)

    grants = json.loads(values.get("AI_GATEWAY_WORKLOAD_GRANTS_JSON", "{}"))
    if not isinstance(grants, dict):
        raise ValueError("AI_GATEWAY_WORKLOAD_GRANTS_JSON must be an object")
    grants["interpreter_server_ai"] = INTERPRETE_GRANT

    limits = json.loads(values.get("AI_GATEWAY_CALLER_LIMITS_JSON", "{}"))
    if not isinstance(limits, dict):
        raise ValueError("AI_GATEWAY_CALLER_LIMITS_JSON must be an object")
    limits.setdefault("interpreter_server_ai", 2)
    limits.setdefault("interprete", 2)
    limits.setdefault("interprete_realtime", 1)

    workload_ids = json.loads(
        values.get("AI_GATEWAY_PLATFORM_WORKLOAD_IDS_JSON", "[]")
    )
    if not isinstance(workload_ids, list) or not all(
        isinstance(item, str) for item in workload_ids
    ):
        raise ValueError("AI_GATEWAY_PLATFORM_WORKLOAD_IDS_JSON must be a string list")
    if "interpreter_server_ai" not in workload_ids:
        workload_ids.append("interpreter_server_ai")

    updates = {
        "AI_GATEWAY_POLICY_GENERATION_ID": "aws-us2-classroom-chat-interprete-v6",
        "AI_GATEWAY_WORKLOAD_GRANTS_JSON": json.dumps(grants, separators=(",", ":")),
        "AI_GATEWAY_CALLER_LIMITS_JSON": json.dumps(limits, separators=(",", ":")),
        "AI_GATEWAY_PLATFORM_WORKLOAD_IDS_JSON": json.dumps(
            workload_ids, separators=(",", ":")
        ),
    }
    updated = "\n".join(_replace(lines, updates)) + "\n"

    backup = env_file.with_name(env_file.name + ".bak." + args.backup_suffix)
    shutil.copy2(env_file, backup)
    stat = env_file.stat()
    fd, temporary_name = tempfile.mkstemp(prefix=env_file.name + ".", dir=env_file.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, stat.st_mode)
        os.chown(temporary_name, stat.st_uid, stat.st_gid)
        os.replace(temporary_name, env_file)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)

    print(f"updated={env_file} backup={backup} workload=interpreter_server_ai enabled=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
