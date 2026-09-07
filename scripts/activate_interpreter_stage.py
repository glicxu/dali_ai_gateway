from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import tempfile
from pathlib import Path


WORKLOAD_ID = "interpreter_server_ai"
INTERPRETER_PROFILES = (
    "interprete.live_summary",
    "interprete.translation.text",
    "interprete.transcription.batch",
    "interprete.transcription.realtime",
    "interprete.translation.realtime",
    "interprete.translation.realtime.openai",
    "interprete.speech.standard",
)
INTERPRETER_CAPABILITIES = (
    "text_generation",
    "audio_transcription",
    "realtime_transcription",
    "realtime_translation",
    "speech_synthesis",
)


def _decode(raw: str) -> str:
    if not raw.startswith(("'", '"')):
        return raw
    parsed = shlex.split(raw, comments=False, posix=True)
    if len(parsed) != 1:
        raise ValueError("environment value is malformed")
    return parsed[0]


def _values(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        result[key.strip()] = _decode(raw.strip())
    return result


def _quoted(value: str) -> str:
    if "'" in value or "\n" in value or "\r" in value:
        raise ValueError("environment value cannot be represented safely")
    return f"'{value}'"


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
        result.append("# Interpreter hosted-v2 stage workload.")
        result.extend(f"{key}={value}" for key, value in remaining.items())
    return result


def activate(
    env_file: Path,
    *,
    issuer: str,
    jwks_url: str,
    backup_suffix: str,
) -> Path:
    env_file = env_file.resolve()
    original = env_file.read_text(encoding="utf-8")
    lines = original.splitlines()
    values = _values(lines)

    grants = json.loads(values["AI_GATEWAY_WORKLOAD_GRANTS_JSON"])
    if not isinstance(grants, dict) or WORKLOAD_ID not in grants:
        raise ValueError("Interpreter workload grant is missing")
    grant = grants[WORKLOAD_ID]
    if not isinstance(grant, dict):
        raise ValueError("Interpreter workload grant is malformed")
    if set(grant.get("products", [])) != {"interprete"}:
        raise ValueError("Interpreter workload product boundary changed")
    if set(grant.get("profiles", [])) != set(INTERPRETER_PROFILES):
        raise ValueError("Interpreter workload profile boundary changed")
    if set(grant.get("capabilities", [])) != set(INTERPRETER_CAPABILITIES):
        raise ValueError("Interpreter workload capability boundary changed")
    grant["enabled"] = True

    platform_ids = json.loads(
        values.get("AI_GATEWAY_PLATFORM_WORKLOAD_IDS_JSON", "[]")
    )
    if not isinstance(platform_ids, list) or any(
        not isinstance(item, str) for item in platform_ids
    ):
        raise ValueError("Platform workload allowlist is malformed")
    if WORKLOAD_ID not in platform_ids:
        platform_ids.append(WORKLOAD_ID)

    updates = {
        "AI_GATEWAY_POLICY_GENERATION_ID": "aws-us2-interpreter-stage-v9",
        "AI_GATEWAY_WORKLOAD_GRANTS_JSON": _quoted(
            json.dumps(grants, separators=(",", ":"), sort_keys=True)
        ),
        "AI_GATEWAY_PLATFORM_WORKLOAD_AUTH_ENABLED": "true",
        "AI_GATEWAY_PLATFORM_WORKLOAD_AUTH_REQUIRED_FOR_READINESS": "true",
        "AI_GATEWAY_PLATFORM_WORKLOAD_ISSUER": issuer,
        "AI_GATEWAY_PLATFORM_WORKLOAD_AUDIENCE": "dali-ai-gateway",
        "AI_GATEWAY_PLATFORM_WORKLOAD_REQUIRED_SCOPE": "ai:execute",
        "AI_GATEWAY_PLATFORM_WORKLOAD_JWKS_URL": jwks_url,
        "AI_GATEWAY_PLATFORM_WORKLOAD_IDS_JSON": _quoted(
            json.dumps(sorted(set(platform_ids)), separators=(",", ":"))
        ),
    }
    updated = "\n".join(_replace(lines, updates)) + "\n"
    backup = env_file.with_name(f"{env_file.name}.bak.{backup_suffix}")
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    shutil.copy2(env_file, backup)
    stat = env_file.stat()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{env_file.name}.", dir=env_file.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write(updated)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary_name, stat.st_mode)
        if hasattr(os, "chown"):
            os.chown(temporary_name, stat.st_uid, stat.st_gid)
        os.replace(temporary_name, env_file)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enable the isolated Interpreter stage workload and Platform JWT auth."
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--jwks-url", required=True)
    parser.add_argument("--backup-suffix", required=True)
    args = parser.parse_args()
    backup = activate(
        args.env_file,
        issuer=args.issuer,
        jwks_url=args.jwks_url,
        backup_suffix=args.backup_suffix,
    )
    print(f"activated workload={WORKLOAD_ID} backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
