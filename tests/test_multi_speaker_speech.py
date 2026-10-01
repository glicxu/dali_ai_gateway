import asyncio
import base64
import json
import logging

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.models import SpeechSynthesisRequest
from app.providers.gemini import GeminiProvider
from app.core.config import DEFAULT_WORKLOAD_GRANTS, Settings
from app.main import create_app
from tests.conftest import FakeProvider


def test_multi_speaker_request_requires_two_configured_speakers():
    request = SpeechSynthesisRequest.model_validate({
        "request_id": "10000000-0000-0000-0000-000000000001",
        "product": "dali_audio",
        "profile": "dali_audio.speech.gemini",
        "turns": [
            {"speaker": "Jess", "text": "Hello."},
            {"speaker": "John", "text": "Hi.", "style": "quiet"},
        ],
        "speakers": [
            {"speaker": "Jess", "voice": "female"},
            {"speaker": "John", "voice": "male"},
        ],
    })
    assert request.input is None
    assert [turn.speaker for turn in request.turns] == ["Jess", "John"]


def test_gemini_dialogue_is_one_interaction_with_two_voices():
    async def exercise():
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={
                "steps": [{"type": "model_output", "content": [{
                    "type": "audio",
                    "data": base64.b64encode(b"pcm").decode(),
                    "mime_type": "audio/l16; rate=24000; channels=1",
                }]}],
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = GeminiProvider(
            api_key="test", base_url="https://example.test/v1beta",
            timeout_seconds=5, client=client)
        result = await provider.synthesize_dialogue(
            model="gemini-3.1-flash-tts-preview",
            turns=[{"speaker": "Jess", "text": "Hello.", "style": ""},
                   {"speaker": "John", "text": "Hi.", "style": "quiet"}],
            speakers=[{"speaker": "Jess", "voice": "Kore"},
                      {"speaker": "John", "voice": "Puck"}],
            instructions="",
        )
        assert result.audio.startswith(b"RIFF")
        assert captured["generation_config"]["speech_config"] == [
            {"speaker": "Jess", "voice": "Kore"},
            {"speaker": "John", "voice": "Puck"},
        ]
        assert "Jess: Hello." in captured["input"]
        assert "John (quiet): Hi." in captured["input"]
        await client.aclose()

    asyncio.run(exercise())


def test_gateway_routes_aliases_for_one_multi_speaker_request(caplog):
    import copy

    grants = copy.deepcopy(DEFAULT_WORKLOAD_GRANTS)
    grants["dali_chat_server"]["enabled"] = True
    provider = FakeProvider()
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-token"}'),
        legacy_auth_workload_ids_json='["dali_chat_server"]',
        workload_grants_json=json.dumps(grants),
        model_profiles_json=json.dumps({
            "dali_chat.speech.gemini": {
                "capability": "speech_synthesis", "provider": "gemini",
                "model": "gemini-3.1-flash-tts-preview",
                "voice_routes": {"female": "Kore", "male": "Puck"},
            }
        }),
    )
    app = create_app(settings, providers={"gemini": provider})
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        with TestClient(app) as client:
            response = client.post('/ai/v1/audio/speech', headers={
                'Authorization': 'Bearer chat-token',
                'X-Dali-Caller': 'dali_chat_server',
            }, json={
                'request_id': '10000000-0000-0000-0000-000000000002',
                'product': 'dali_chat', 'profile': 'dali_chat.speech.gemini',
                'instructions': 'Private product direction.',
                'turns': [{'speaker': 'Jess', 'text': 'Private hello.'},
                          {'speaker': 'John', 'text': 'Private hi.'}],
                'speakers': [{'speaker': 'Jess', 'voice': 'female'},
                             {'speaker': 'John', 'voice': 'male'}],
            })
    assert response.status_code == 200, response.json()
    assert response.content == b'RIFF-test-dialogue'
    assert provider.synthesized_dialogues == [[
        {'speaker': 'Jess', 'text': 'Private hello.', 'style': ''},
        {'speaker': 'John', 'text': 'Private hi.', 'style': ''},
    ]]
    audit = [record.getMessage() for record in caplog.records
             if 'provider_speech_section_dispatch' in record.getMessage()]
    assert len(audit) == 2
    assert 'section=0' in audit[0]
    assert 'speaker=Jess' in audit[0]
    assert 'requested_voice=female' in audit[0]
    assert 'provider_voice=Kore' in audit[0]
    assert 'section=1' in audit[1]
    assert 'requested_voice=male' in audit[1]
    assert 'provider_voice=Puck' in audit[1]
    assert all('instruction_sha256=' in line for line in audit)
    assert all('style_sha256=' in line for line in audit)
    assert all('Private' not in line for line in audit)
