from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import tempfile
from pathlib import Path

from scripts.activate_interpreter_stage import _quoted, _values
from app.core.config import DEFAULT_WORKLOAD_GRANTS, Settings


WORKLOAD_ID = "dali_job_ai"
PROFILE_ID = "dalijob.job_parse"
STRUCTURED_PROFILE_IDS = {
    PROFILE_ID,
    "dalijob.job.extract",
    "dalijob.job.extract.repair",
}
MODEL_PROFILE_IDS = (
    PROFILE_ID,
    "dalijob.resume.parse",
    "dalijob.resume.match",
    "dalijob.candidate.extract",
    "dalijob.job.extract",
    "dalijob.job.extract.repair",
    "dalijob.qualification.assess",
    "dalijob.qualification.repair",
)


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
        result.append("# DaliJob Phase 3 bounded test workload.")
        result.extend(f"{key}={value}" for key, value in remaining.items())
    return result


def configure(
    env_file: Path,
    *,
    issuer: str,
    jwks_url: str,
    model: str,
    backup_suffix: str,
) -> Path:
    """Add the bounded DaliJob test policy while preserving existing policy."""
    env_file = env_file.resolve()
    original = env_file.read_text(encoding="utf-8")
    lines = original.splitlines()
    values = _values(lines)

    profiles = json.loads(values.get("AI_GATEWAY_MODEL_PROFILES_JSON", "{}"))
    grants = json.loads(values["AI_GATEWAY_WORKLOAD_GRANTS_JSON"]) if (
        "AI_GATEWAY_WORKLOAD_GRANTS_JSON" in values
    ) else copy.deepcopy(DEFAULT_WORKLOAD_GRANTS)
    limits = json.loads(
        values.get(
            "AI_GATEWAY_CALLER_LIMITS_JSON",
            str(Settings.model_fields["caller_limits_json"].default),
        )
    )
    platform_ids = json.loads(
        values.get("AI_GATEWAY_PLATFORM_WORKLOAD_IDS_JSON", "[]")
    )
    if not isinstance(profiles, dict) or not isinstance(grants, dict):
        raise ValueError("Gateway profile or grant policy is malformed")
    if not isinstance(limits, dict) or not isinstance(platform_ids, list):
        raise ValueError("Gateway limit or Platform workload policy is malformed")
    if any(not isinstance(item, str) for item in platform_ids):
        raise ValueError("Platform workload allowlist is malformed")

    parser_profile = {
        "capability": "text_generation",
        "provider": "openai",
        "model": model,
        "required_for_readiness": False,
        "supported_outputs": ["json_schema"],
        "max_input_bytes": 65536,
        "capacity_pool": "dalijob-background",
        "traffic_class": "background",
    }
    matching_profile = {
        **parser_profile,
        "supported_outputs": ["json"],
        "max_input_bytes": 131072,
    }
    expected_profiles = {
        profile_id: (
            parser_profile if profile_id in STRUCTURED_PROFILE_IDS else matching_profile
        )
        for profile_id in MODEL_PROFILE_IDS
    }
    expected_grant = {
        "enabled": True,
        "products": ["dalijob"],
        "profiles": list(MODEL_PROFILE_IDS),
        "capabilities": ["text_generation"],
    }
    for profile_id, expected_profile in expected_profiles.items():
        existing = profiles.get(profile_id)
        if existing is not None and existing != expected_profile:
            raise ValueError(f"existing {profile_id} policy differs from reviewed policy")
        profiles[profile_id] = expected_profile
    existing_grant = grants.get(WORKLOAD_ID)
    previous_grant = {
        **expected_grant,
        "profiles": [PROFILE_ID],
    }
    if existing_grant not in (None, previous_grant, expected_grant):
        raise ValueError(f"existing {WORKLOAD_ID} policy differs from reviewed policy")
    grants[WORKLOAD_ID] = expected_grant
    existing_limit = limits.get(WORKLOAD_ID)
    if existing_limit not in (None, 1):
        raise ValueError("existing DaliJob caller limit differs from reviewed limit")
    limits[WORKLOAD_ID] = 1
    if WORKLOAD_ID not in platform_ids:
        platform_ids.append(WORKLOAD_ID)

    updates = {
        "AI_GATEWAY_POLICY_GENERATION_ID": "us3-phase3-dalijob-json-schema-v3",
        "AI_GATEWAY_MODEL_PROFILES_JSON": _quoted(
            json.dumps(profiles, separators=(",", ":"), sort_keys=True)
        ),
        "AI_GATEWAY_WORKLOAD_GRANTS_JSON": _quoted(
            json.dumps(grants, separators=(",", ":"), sort_keys=True)
        ),
        "AI_GATEWAY_CALLER_LIMITS_JSON": _quoted(
            json.dumps(limits, separators=(",", ":"), sort_keys=True)
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
        description="Install the reviewed bounded us3 DaliJob Phase 3 Gateway policy."
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--jwks-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--backup-suffix", required=True)
    args = parser.parse_args()
    backup = configure(
        args.env_file,
        issuer=args.issuer,
        jwks_url=args.jwks_url,
        model=args.model,
        backup_suffix=args.backup_suffix,
    )
    print(
        f"configured workload={WORKLOAD_ID} profiles={','.join(MODEL_PROFILE_IDS)} "
        f"backup={backup}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
