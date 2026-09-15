import json

from app.core.config import Settings
from scripts.render_dalibible_canary_env import render_canary_environment


def test_canary_env_merges_existing_policy_without_broadening_other_grants() -> None:
    existing = {
        "existing": {
            "enabled": True,
            "products": ["existing"],
            "profiles": ["existing.profile"],
            "capabilities": ["text_generation"],
        }
    }
    settings = Settings(
        workload_grants_json=json.dumps(existing),
        platform_workload_ids_json='["existing_workload"]',
    )

    rendered = render_canary_environment(settings)
    values = dict(line.split("=", 1) for line in rendered.splitlines())
    grants = json.loads(values["AI_GATEWAY_WORKLOAD_GRANTS_JSON"])
    workload_ids = json.loads(values["AI_GATEWAY_PLATFORM_WORKLOAD_IDS_JSON"])

    assert grants["existing"] == existing["existing"]
    assert grants["dali_bible_server_ai"] == {
        "enabled": True,
        "products": ["dalibible"],
        "profiles": ["dalibible.study.standard"],
        "capabilities": ["text_generation"],
    }
    assert workload_ids == ["existing_workload", "dali_bible_server_ai"]
