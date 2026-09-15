from __future__ import annotations

import json

from app.core.config import Settings


WORKLOAD_ID = "dali_bible_server_ai"
GRANT = {
    "enabled": True,
    "products": ["dalibible"],
    "profiles": ["dalibible.study.standard"],
    "capabilities": ["text_generation"],
}


def render_canary_environment(settings: Settings) -> str:
    grants = json.loads(settings.workload_grants_json)
    if not isinstance(grants, dict):
        raise ValueError("Gateway workload grants must be a JSON object")
    grants[WORKLOAD_ID] = GRANT

    workload_ids = json.loads(settings.platform_workload_ids_json)
    if not isinstance(workload_ids, list) or not all(
        isinstance(value, str) for value in workload_ids
    ):
        raise ValueError("Platform workload IDs must be a JSON string list")
    workload_ids = list(dict.fromkeys([*workload_ids, WORKLOAD_ID]))

    compact_grants = json.dumps(grants, separators=(",", ":"), sort_keys=True)
    compact_ids = json.dumps(workload_ids, separators=(",", ":"))
    return "\n".join(
        (
            f"AI_GATEWAY_WORKLOAD_GRANTS_JSON={compact_grants}",
            f"AI_GATEWAY_PLATFORM_WORKLOAD_IDS_JSON={compact_ids}",
            "AI_GATEWAY_POLICY_GENERATION_ID=aws-us2-dalibible-canary-v1",
            "",
        )
    )


def main() -> None:
    print(render_canary_environment(Settings()), end="")


if __name__ == "__main__":
    main()
