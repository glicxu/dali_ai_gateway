from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from app.core.config import DEFAULT_WORKLOAD_GRANTS
from scripts.activate_interpreter_stage import activate


def _read(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, raw = line.split("=", 1)
            values[key] = shlex.split(raw)[0]
    return values


def _env(path: Path) -> None:
    grants = {
        "dali_classroom_server": DEFAULT_WORKLOAD_GRANTS["dali_classroom_server"],
        "interpreter_server_ai": DEFAULT_WORKLOAD_GRANTS["interpreter_server_ai"],
    }
    path.write_text(
        "AI_GATEWAY_POLICY_GENERATION_ID=old\n"
        f"AI_GATEWAY_WORKLOAD_GRANTS_JSON='{json.dumps(grants)}'\n"
        "AI_GATEWAY_PLATFORM_WORKLOAD_AUTH_ENABLED=false\n"
        "AI_GATEWAY_PLATFORM_WORKLOAD_AUTH_REQUIRED_FOR_READINESS=false\n"
        "AI_GATEWAY_PLATFORM_WORKLOAD_IDS_JSON=[]\n",
        encoding="utf-8",
    )


def test_activation_is_additive_and_preserves_rollback_file(tmp_path: Path) -> None:
    env_file = tmp_path / "gateway.env"
    _env(env_file)
    backup = activate(
        env_file,
        issuer="https://server.dalifin.com",
        jwks_url="https://server.dalifin.com/.well-known/jwks.json",
        backup_suffix="phase1",
    )

    before = _read(backup)
    after = _read(env_file)
    grants = json.loads(after["AI_GATEWAY_WORKLOAD_GRANTS_JSON"])
    assert not json.loads(before["AI_GATEWAY_WORKLOAD_GRANTS_JSON"])[
        "interpreter_server_ai"
    ].get("enabled", False)
    assert grants["interpreter_server_ai"]["enabled"] is True
    assert grants["dali_classroom_server"] == json.loads(
        before["AI_GATEWAY_WORKLOAD_GRANTS_JSON"]
    )["dali_classroom_server"]
    assert after["AI_GATEWAY_PLATFORM_WORKLOAD_AUTH_ENABLED"] == "true"
    assert json.loads(after["AI_GATEWAY_PLATFORM_WORKLOAD_IDS_JSON"]) == [
        "interpreter_server_ai"
    ]


def test_activation_rejects_profile_boundary_drift(tmp_path: Path) -> None:
    env_file = tmp_path / "gateway.env"
    _env(env_file)
    values = _read(env_file)
    grants = json.loads(values["AI_GATEWAY_WORKLOAD_GRANTS_JSON"])
    grants["interpreter_server_ai"]["profiles"] = ["provider.direct"]
    env_file.write_text(
        env_file.read_text(encoding="utf-8").replace(
            f"'{values['AI_GATEWAY_WORKLOAD_GRANTS_JSON']}'",
            f"'{json.dumps(grants)}'",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="profile boundary"):
        activate(
            env_file,
            issuer="https://server.dalifin.com",
            jwks_url="https://server.dalifin.com/.well-known/jwks.json",
            backup_suffix="phase1",
        )
