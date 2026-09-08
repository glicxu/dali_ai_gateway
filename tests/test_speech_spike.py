import copy
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import DEFAULT_PROFILES, DEFAULT_WORKLOAD_GRANTS, Settings
from app.main import create_app
from tests.conftest import FakeProvider


def test_speech_discovery_pin_and_voice_routing() -> None:
    profiles = copy.deepcopy(DEFAULT_PROFILES)
    profiles["dali_chat.speech.gemini"].update(
        voice_routes={"narrator_main": "Kore"},
        max_input_bytes=100,
    )
    grants = copy.deepcopy(DEFAULT_WORKLOAD_GRANTS)
    grants["dali_classroom_server"]["enabled"] = False
    grants["dali_chat_server"]["enabled"] = True
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        legacy_auth_workload_ids_json='["dali_chat_server"]',
        model_profiles_json=json.dumps(profiles),
        workload_grants_json=json.dumps(grants),
    )
    provider = FakeProvider()
    captured = []
    original = provider.synthesize

    async def synthesize(**kwargs):
        captured.append(kwargs)
        return await original(**kwargs)

    provider.synthesize = synthesize
    app = create_app(settings, providers={"gemini": provider})
    headers = {
        "Authorization": "Bearer chat-test-token",
        "X-Dali-Caller": "dali_chat_server",
    }
    params = {"product": "dali_chat", "profile": "dali_chat.speech.gemini"}
    path = "/ai/v1/audio/speech/capabilities"
    with TestClient(app) as client:
        assert client.get(path, params=params).status_code == 401
        assert (
            client.get(
                path, headers=headers, params={**params, "product": "classroom"}
            ).status_code
            == 403
        )
        response = client.get(path, params=params, headers=headers)
        assert response.status_code == 200
        capability = response.json()
        assert capability["voices"] == ["narrator_main"]
        assert "Kore" not in response.text
        assert capability["instructions_semantics"] == "best_effort"
        payload = {
            **params,
            "request_id": str(uuid4()),
            "input": "Hello.",
            "voice": "narrator_main",
            "instructions": "Warm and restrained.",
            "configuration_id": capability["configuration_id"],
        }
        spoken = client.post("/ai/v1/audio/speech", headers=headers, json=payload)
        assert spoken.status_code == 200
        assert (
            spoken.headers["x-dali-speech-configuration"]
            == capability["configuration_id"]
        )
        assert spoken.headers["cache-control"] == "no-store"
        assert captured[0]["voice"] == "Kore"
        assert captured[0]["instructions"] == payload["instructions"]
        for override, status in [
            ({"configuration_id": "0" * 64}, 409),
            ({"voice": "Kore"}, 403),
            ({"input": "Ã©" * 60}, 400),
        ]:
            rejected = client.post(
                "/ai/v1/audio/speech",
                headers=headers,
                json={**payload, **override, "request_id": str(uuid4())},
            )
            assert rejected.status_code == status
        assert len(captured) == 1


def test_configuration_identity_tracks_voice_mapping_not_unrelated_policy() -> None:
    from app.core.policy import PolicyGeneration, PolicyGenerationDocument
    from app.core.speech import speech_configuration_id

    profiles = copy.deepcopy(DEFAULT_PROFILES)

    def identity():
        generation = PolicyGeneration.from_document(
            PolicyGenerationDocument(
                generation_id="spike-test",
                profiles=profiles,
                grants=DEFAULT_WORKLOAD_GRANTS,
            )
        )
        return speech_configuration_id(generation.profiles["dali_chat.speech.gemini"])

    before = identity()
    profiles["dali_chat.text.openai"]["model"] = "unrelated-model"
    assert identity() == before
    profiles["dali_chat.speech.gemini"]["voice_routes"] = {"narrator_main": "Kore"}
    assert identity() != before
