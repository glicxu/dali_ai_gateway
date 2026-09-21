import json

from scripts.activate_interpreter_stage import _values
from scripts.configure_dalijob_phase3 import MODEL_PROFILE_IDS, configure
from app.core.config import DEFAULT_WORKLOAD_GRANTS


def test_configure_preserves_existing_policy_and_adds_bounded_dalijob(tmp_path):
    env_file = tmp_path / "gateway.env"
    env_file.write_text(
        "AI_GATEWAY_MODEL_PROFILES_JSON='{}'\n"
        "AI_GATEWAY_WORKLOAD_GRANTS_JSON='{\"existing\":{\"products\":[\"x\"]}}'\n"
        "AI_GATEWAY_CALLER_LIMITS_JSON='{\"existing\":2}'\n",
        encoding="utf-8",
    )

    backup = configure(
        env_file,
        issuer="https://server.dalifin.com",
        jwks_url="http://127.0.0.1:5030/.well-known/jwks.json",
        model="gpt-5.6-luna",
        backup_suffix="test",
    )

    values = _values(env_file.read_text(encoding="utf-8").splitlines())
    profiles = json.loads(values["AI_GATEWAY_MODEL_PROFILES_JSON"])
    grants = json.loads(values["AI_GATEWAY_WORKLOAD_GRANTS_JSON"])
    limits = json.loads(values["AI_GATEWAY_CALLER_LIMITS_JSON"])
    assert grants["existing"] == {"products": ["x"]}
    assert profiles["dalijob.job_parse"]["capacity_pool"] == "dalijob-background"
    assert profiles["dalijob.job_parse"]["supported_outputs"] == ["json_schema"]
    assert profiles["dalijob.job.extract"]["supported_outputs"] == ["json_schema"]
    assert profiles["dalijob.job.extract.repair"]["supported_outputs"] == ["json_schema"]
    assert set(MODEL_PROFILE_IDS).issubset(profiles)
    assert profiles["dalijob.candidate.extract"]["supported_outputs"] == ["json"]
    assert profiles["dalijob.qualification.assess"]["max_input_bytes"] == 131072
    assert grants["dali_job_ai"]["products"] == ["dalijob"]
    assert grants["dali_job_ai"]["profiles"] == list(MODEL_PROFILE_IDS)
    assert limits["dali_job_ai"] == 1
    assert limits["existing"] == 2
    assert json.loads(values["AI_GATEWAY_PLATFORM_WORKLOAD_IDS_JSON"]) == [
        "dali_job_ai"
    ]
    assert values["AI_GATEWAY_PLATFORM_WORKLOAD_AUTH_ENABLED"] == "true"
    assert values["AI_GATEWAY_POLICY_GENERATION_ID"] == (
        "us3-phase3-dalijob-json-schema-v3"
    )
    assert backup.read_text(encoding="utf-8").startswith(
        "AI_GATEWAY_MODEL_PROFILES_JSON"
    )


def test_configure_refuses_policy_drift(tmp_path):
    env_file = tmp_path / "gateway.env"
    env_file.write_text(
        "AI_GATEWAY_MODEL_PROFILES_JSON='{\"dalijob.job_parse\":{\"model\":\"other\"}}'\n",
        encoding="utf-8",
    )

    try:
        configure(
            env_file,
            issuer="https://server.dalifin.com",
            jwks_url="http://127.0.0.1:5030/.well-known/jwks.json",
            model="gpt-5.6-luna",
            backup_suffix="test",
        )
    except ValueError as exc:
        assert "reviewed policy" in str(exc)
    else:
        raise AssertionError("policy drift was accepted")


def test_configure_preserves_builtin_grants_when_env_override_is_absent(tmp_path):
    env_file = tmp_path / "gateway.env"
    env_file.write_text("AI_GATEWAY_ENV=production\n", encoding="utf-8")

    configure(
        env_file,
        issuer="https://server.dalifin.com",
        jwks_url="http://127.0.0.1:5030/.well-known/jwks.json",
        model="gpt-5.6-luna",
        backup_suffix="test",
    )

    values = _values(env_file.read_text(encoding="utf-8").splitlines())
    grants = json.loads(values["AI_GATEWAY_WORKLOAD_GRANTS_JSON"])
    for workload_id, grant in DEFAULT_WORKLOAD_GRANTS.items():
        assert grants[workload_id] == grant
    assert grants["dali_job_ai"]["enabled"] is True
