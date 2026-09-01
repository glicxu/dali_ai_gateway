from __future__ import annotations

import copy
import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import DEFAULT_WORKLOAD_GRANTS, Settings
from app.core.security import WorkloadPrincipal
from app.main import create_app
from app.models import RealtimeTranslationStart
from app.providers.base import RealtimeEvent
from tests.conftest import FakeProvider


class _InjectedWorkloadAuthenticator:
    configured = True
    ready = True
    workload_ids = frozenset({"dali_classroom_server"})

    async def authenticate_workload(
        self, caller_hint: str | None, authorization: str | None
    ) -> WorkloadPrincipal:
        del caller_hint
        if authorization != "Bearer verified-workload-token":
            raise RuntimeError("test verifier rejected credential")
        return WorkloadPrincipal(
            workload_id="dali_classroom_server",
            credential_kind="workload_token",
        )

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _ErrorOnceSession:
    def __init__(self) -> None:
        self.closed = False
        self._appended = asyncio.Event()
        self._error_sent = False

    async def append(self, _: str) -> None:
        self._error_sent = True
        self._appended.set()

    async def commit(self) -> None:
        return None

    async def clear(self) -> None:
        return None

    async def next_event(self) -> RealtimeEvent:
        await self._appended.wait()
        self._error_sent = False
        return RealtimeEvent("error", code="provider_down")

    async def close(self) -> None:
        self.closed = True


class _FailoverProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self._sessions = [_ErrorOnceSession(), self.realtime_translation]

    async def open_realtime_translation(self, **kwargs):
        self.realtime_translation_outputs.append(kwargs["outputs"])
        return self._sessions.pop(0)


def test_health_and_readiness(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_readiness_uses_cached_health_without_synchronous_probe(
    client: TestClient, fake_provider: FakeProvider
) -> None:
    initial_calls = fake_provider.probe_calls
    assert initial_calls == 2

    assert client.get("/health/ready").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert fake_provider.probe_calls == initial_calls


def test_text_generation_is_service_authenticated_and_profile_routed(
    client: TestClient,
    headers: dict[str, str],
    fake_provider: FakeProvider,
) -> None:
    payload = {
        "request_id": str(uuid4()),
        "product": "classroom",
        "profile": "classroom.translation.economy",
        "system_instruction": "Translate faithfully.",
        "input": "Private lecture text.",
        "response_format": "text",
        "temperature": 0,
    }
    assert client.post("/ai/v1/text/generations", json=payload).status_code == 401
    response = client.post("/ai/v1/text/generations", headers=headers, json=payload)
    assert response.status_code == 200
    assert response.json() == {
        "request_id": payload["request_id"],
        "output": "Generated result.",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "usage": {"input_tokens": 7, "output_tokens": 3, "audio_ms": None},
    }
    assert fake_provider.generated_inputs == ["Private lecture text."]


def test_disabled_provider_route_fails_before_provider_work(
    fake_provider: FakeProvider,
) -> None:
    settings = Settings(
        service_tokens_json=SecretStr(
            json.dumps({"dali_classroom_server": "service-test-token"})
        ),
        caller_limits_json=json.dumps({"dali_classroom_server": 1}),
        provider_circuit_enabled=True,
        provider_circuit_disabled_routes_json=json.dumps(
            ["gemini.gemini-3.5-flash-lite"]
        ),
    )
    application = create_app(
        settings, providers={"openai": fake_provider, "gemini": fake_provider}
    )
    payload = {
        "request_id": str(uuid4()),
        "product": "classroom",
        "profile": "classroom.translation.economy",
        "system_instruction": "Translate faithfully.",
        "input": "Private lecture text.",
        "response_format": "text",
        "temperature": 0,
    }
    with TestClient(application) as value:
        response = value.post(
            "/ai/v1/text/generations",
            headers={
                "Authorization": "Bearer service-test-token",
                "X-Dali-Caller": "dali_classroom_server",
            },
            json=payload,
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_gateway_provider_unavailable"
    assert fake_provider.generated_inputs == []


def test_speech_synthesis_is_profile_routed_as_binary(
    fake_provider: FakeProvider,
) -> None:
    grants = copy.deepcopy(DEFAULT_WORKLOAD_GRANTS)
    grants["dali_classroom_server"]["enabled"] = False
    grants["dali_chat_server"]["enabled"] = True
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-token"}'),
        legacy_auth_workload_ids_json='["dali_chat_server"]',
        workload_grants_json=json.dumps(grants),
    )
    application = create_app(settings, providers={"gemini": fake_provider})
    with TestClient(application) as value:
        response = value.post(
            "/ai/v1/audio/speech",
            headers={
                "Authorization": "Bearer chat-token",
                "X-Dali-Caller": "dali_chat_server",
            },
            json={
                "request_id": str(uuid4()),
                "product": "dali_chat",
                "profile": "dali_chat.speech.gemini",
                "input": "Read this aloud.",
                "voice": "Kore",
            },
        )

    assert response.status_code == 200
    assert response.content == b"RIFF-test-audio"
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["x-dali-provider"] == "gemini"
    assert response.headers["x-dali-model"] == "gemini-3.1-flash-tts-preview"
    assert fake_provider.synthesized_text == ["Read this aloud."]


def test_injected_authenticator_derives_workload_from_verified_credential(
    settings: Settings, fake_provider: FakeProvider
) -> None:
    application = create_app(
        settings,
        providers={"openai": fake_provider, "gemini": fake_provider},
        authenticator=_InjectedWorkloadAuthenticator(),
    )
    with TestClient(application) as value:
        response = value.post(
            "/ai/v1/text/generations",
            headers={
                "Authorization": "Bearer verified-workload-token",
                "X-Dali-Caller": "untrusted_header_value",
            },
            json={
                "request_id": str(uuid4()),
                "product": "classroom",
                "profile": "classroom.translation.economy",
                "system_instruction": "Instruction",
                "input": "Input",
            },
        )
    assert response.status_code == 200


def test_caller_cannot_cross_product_or_capability(
    client: TestClient, headers: dict[str, str]
) -> None:
    base = {
        "request_id": str(uuid4()),
        "system_instruction": "Instruction",
        "input": "Input",
    }
    wrong_product = client.post(
        "/ai/v1/text/generations",
        headers=headers,
        json={
            **base,
            "product": "interprete",
            "profile": "classroom.translation.economy",
        },
    )
    assert wrong_product.status_code == 403
    wrong_capability = client.post(
        "/ai/v1/text/generations",
        headers=headers,
        json={
            **base,
            "product": "classroom",
            "profile": "classroom.transcription.live",
        },
    )
    assert wrong_capability.status_code == 403

    implicit_shared_profile = client.post(
        "/ai/v1/text/generations",
        headers=headers,
        json={
            **base,
            "product": "classroom",
            "profile": "shared.text.gemini",
        },
    )
    assert implicit_shared_profile.status_code == 403


def test_readiness_requires_every_required_profile_provider(
    settings: Settings, fake_provider: FakeProvider
) -> None:
    application = create_app(settings, providers={"gemini": fake_provider})
    with TestClient(application) as value:
        response = value.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_gateway_not_ready"


def test_readiness_requires_exact_identity_and_grant_alignment(
    fake_provider: FakeProvider,
) -> None:
    settings = Settings(
        service_tokens_json=SecretStr(
            '{"dali_classroom_server":"token","ungranted_server":"token-2"}'
        ),
        legacy_auth_workload_ids_json=('["dali_classroom_server","ungranted_server"]'),
    )
    application = create_app(
        settings,
        providers={"gemini": fake_provider, "openai": fake_provider},
    )
    with TestClient(application) as value:
        response = value.get("/health/ready")
    assert response.status_code == 503


def test_required_provider_degradation_is_not_hidden(settings: Settings) -> None:
    healthy = FakeProvider()
    degraded = FakeProvider(probe_error=True)
    application = create_app(
        settings,
        providers={"gemini": healthy, "openai": degraded},
    )
    with TestClient(application) as value:
        response = value.get("/health/ready")
        details = value.app.state.container.registry.safe_readiness_details()
    assert response.status_code == 503
    assert details["provider_counts"] == {
        "unknown": 0,
        "healthy": 1,
        "degraded": 1,
        "stale": 0,
    }


def test_optional_provider_degradation_does_not_flap_readiness(
    settings: Settings,
) -> None:
    healthy = FakeProvider()
    degraded = FakeProvider(probe_error=True)
    application = create_app(
        settings,
        providers={"gemini": healthy, "openai": healthy, "ollama": degraded},
    )
    with TestClient(application) as value:
        response = value.get("/health/ready")
    assert response.status_code == 200


def test_profile_kill_switch_denies_only_the_disabled_profile(
    fake_provider: FakeProvider, headers: dict[str, str]
) -> None:
    grants = {
        "dali_classroom_server": copy.deepcopy(
            DEFAULT_WORKLOAD_GRANTS["dali_classroom_server"]
        )
    }
    grants["dali_classroom_server"]["disabled_profiles"] = [
        "classroom.translation.economy"
    ]
    settings = Settings(
        service_tokens_json=SecretStr(
            json.dumps({"dali_classroom_server": "service-test-token"})
        ),
        workload_grants_json=json.dumps(grants),
    )
    application = create_app(
        settings,
        providers={"gemini": fake_provider, "openai": fake_provider},
    )
    with TestClient(application) as value:
        response = value.post(
            "/ai/v1/text/generations",
            headers=headers,
            json={
                "request_id": str(uuid4()),
                "product": "classroom",
                "profile": "classroom.translation.economy",
                "system_instruction": "Instruction",
                "input": "Input",
            },
        )
        assert response.status_code == 403
        assert value.get("/health/ready").status_code == 200


def test_batch_transcription_is_bounded_and_routed(
    client: TestClient,
    headers: dict[str, str],
    fake_provider: FakeProvider,
) -> None:
    request_id = str(uuid4())
    response = client.post(
        "/ai/v1/audio/transcriptions",
        headers=headers,
        data={
            "request_id": request_id,
            "product": "classroom",
            "profile": "classroom.transcription.economy",
            "source_language": "en",
            "terminology_prompt": "Biology",
        },
        files={"audio": ("lecture.wav", b"RIFF-private-audio", "audio/wav")},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "Captured lecture."
    assert response.json()["model"] == "gemini-3.5-flash-lite"
    assert fake_provider.transcribed_audio == [b"RIFF-private-audio"]


def test_media_analysis_is_bounded_authenticated_and_exactly_routed(
    fake_provider: FakeProvider,
) -> None:
    grants = {
        "dali_chat_server": copy.deepcopy(DEFAULT_WORKLOAD_GRANTS["dali_chat_server"])
    }
    grants["dali_chat_server"]["enabled"] = True
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        workload_grants_json=json.dumps(grants),
        caller_limits_json='{"dali_chat_server":2}',
        legacy_auth_workload_ids_json='["dali_chat_server"]',
    )
    application = create_app(
        settings,
        providers={"openai": fake_provider, "gemini": fake_provider},
    )
    data = {
        "request_id": str(uuid4()),
        "product": "dali_chat",
        "profile": "dali_chat.video.gemini",
        "system_instruction": "Analyze media safely.",
        "prompt": "What happens in this clip?",
    }
    with TestClient(application) as value:
        assert (
            value.post(
                "/ai/v1/media/analyses",
                data=data,
                files={"media": ("clip.mp4", b"private-video", "video/mp4")},
            ).status_code
            == 401
        )
        response = value.post(
            "/ai/v1/media/analyses",
            headers={
                "Authorization": "Bearer chat-test-token",
                "X-Dali-Caller": "dali_chat_server",
            },
            data=data,
            files={"media": ("clip.mp4", b"private-video", "video/mp4")},
        )

    assert response.status_code == 200
    assert response.json()["output"] == "Analyzed media."
    assert response.json()["provider"] == "gemini"
    assert fake_provider.analyzed_media == [("video", b"private-video")]


def test_validation_error_does_not_echo_private_content(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = client.post(
        "/ai/v1/text/generations",
        headers=headers,
        json={"input": "DO-NOT-ECHO-PRIVATE-LECTURE"},
    )
    assert response.status_code == 422
    assert "DO-NOT-ECHO-PRIVATE-LECTURE" not in response.text
    assert response.json()["error"]["code"] == "ai_gateway_request_invalid"


def test_realtime_transcription_bridge(
    client: TestClient,
    headers: dict[str, str],
    fake_provider: FakeProvider,
) -> None:
    with client.websocket_connect(
        "/ai/v1/realtime/transcriptions", headers=headers
    ) as socket:
        socket.send_json(
            {
                "type": "session.start",
                "request_id": str(uuid4()),
                "product": "classroom",
                "profile": "classroom.transcription.live",
                "source_language": "en",
            }
        )
        assert socket.receive_json()["type"] == "session.ready"
        socket.send_json({"type": "audio.append", "audio": "AQI="})
        assert socket.receive_json() == {
            "type": "transcript.delta",
            "text": "Lecture",
            "item_id": "item-1",
        }
        socket.send_json({"type": "audio.commit"})
        assert socket.receive_json() == {
            "type": "transcript.final",
            "text": "Lecture text.",
            "item_id": "item-1",
        }
        socket.send_json({"type": "session.stop"})
    assert fake_provider.realtime.appended == ["AQI="]
    assert fake_provider.realtime.committed == 1
    assert fake_provider.realtime.closed_event.wait(timeout=1)


def test_realtime_translation_bridge(
    client: TestClient,
    headers: dict[str, str],
    fake_provider: FakeProvider,
) -> None:
    with client.websocket_connect(
        "/ai/v1/realtime/translations", headers=headers
    ) as socket:
        socket.send_json(
            {
                "type": "session.start",
                "request_id": str(uuid4()),
                "product": "classroom",
                "profile": "classroom.translation.live",
                "target_language": "de-DE",
                "instructions": "Translate the classroom lecture faithfully.",
                "outputs": [
                    "source_transcript",
                    "target_transcript",
                    "translated_audio",
                ],
            }
        )
        assert socket.receive_json()["type"] == "session.ready"
        socket.send_json({"type": "audio.append", "audio": "AQI="})
        assert socket.receive_json()["type"] == "translation.delta"
        socket.send_json({"type": "session.stop"})
    assert fake_provider.realtime_translation.appended == ["AQI="]
    assert fake_provider.realtime_translation_outputs == [
        frozenset({"source_transcript", "target_transcript", "translated_audio"})
    ]
    assert fake_provider.realtime_translation.closed_event.wait(timeout=1)


def test_realtime_translation_outputs_default_and_reject_duplicates() -> None:
    values = {
        "type": "session.start",
        "request_id": str(uuid4()),
        "product": "classroom",
        "profile": "classroom.translation.live",
        "target_language": "de-DE",
    }
    request = RealtimeTranslationStart.model_validate(values)
    assert request.outputs == ["target_transcript", "translated_audio"]
    with pytest.raises(ValidationError):
        RealtimeTranslationStart.model_validate(
            values | {"outputs": ["source_transcript", "source_transcript"]}
        )


def test_realtime_v2_single_contract_acknowledges_input(
    client: TestClient,
    headers: dict[str, str],
    fake_provider: FakeProvider,
) -> None:
    with client.websocket_connect(
        "/ai/v2/realtime/translations", headers=headers
    ) as socket:
        socket.send_json(
            {
                "type": "session.start",
                "request_id": str(uuid4()),
                "product": "classroom",
                "profile": "classroom.translation.live",
                "target_language": "de-DE",
            }
        )
        ready = socket.receive_json()
        assert ready["type"] == "session.ready"
        assert ready["sequence"] == 0
        socket.send_json({"type": "audio.append", "sequence": 1, "audio": "AQI="})
        accepted = socket.receive_json()
        assert accepted["type"] == "audio.accepted"
        assert accepted["accepted_sequence"] == 1
        assert socket.receive_json()["type"] == "translation.delta"
        socket.send_json({"type": "session.stop"})
    assert fake_provider.realtime_translation.closed_event.wait(timeout=1)


def test_realtime_v2_rejects_unimplemented_comparison_policy(
    client: TestClient, headers: dict[str, str]
) -> None:
    with client.websocket_connect(
        "/ai/v2/realtime/translations", headers=headers
    ) as socket:
        socket.send_json(
            {
                "type": "session.start",
                "request_id": str(uuid4()),
                "product": "classroom",
                "profile": "classroom.translation.live",
                "target_language": "de-DE",
                "policy": "compare",
                "compare_profile": "dali_chat.interpret.openai",
            }
        )
        error = socket.receive_json()
        assert error["error"]["code"] == "ai_gateway_realtime_policy_not_implemented"


def test_realtime_v2_automatically_fails_over_at_provider_error() -> None:
    provider = _FailoverProvider()
    grants = copy.deepcopy(DEFAULT_WORKLOAD_GRANTS["dali_chat_server"])
    grants["enabled"] = True
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        workload_grants_json=json.dumps({"dali_chat_server": grants}),
        caller_limits_json='{"dali_chat_server":1}',
        legacy_auth_workload_ids_json='["dali_chat_server"]',
    )
    application = create_app(settings, providers={"openai": provider, "gemini": provider})
    headers = {
        "Authorization": "Bearer chat-test-token",
        "X-Dali-Caller": "dali_chat_server",
    }
    with TestClient(application) as client:
        with client.websocket_connect(
            "/ai/v2/realtime/translations", headers=headers
        ) as socket:
            socket.send_json(
                {
                    "type": "session.start",
                    "request_id": str(uuid4()),
                    "product": "dali_chat",
                    "profile": "dali_chat.interpret.openai",
                    "fallback_profile": "dali_chat.interpret.gemini",
                    "policy": "windowed_failover",
                    "target_language": "zh-CN",
                }
            )
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_json({"type": "audio.append", "sequence": 1, "audio": "AQI="})
            events = [socket.receive_json(), socket.receive_json()]
            if events[0]["type"] != "window.failed":
                events.append(socket.receive_json())
            assert any(event["type"] == "window.failed" for event in events)
            switched = next(
                event for event in events if event["type"] == "provider.switched"
            )
            assert switched["type"] == "provider.switched"
            assert switched["from_provider"] == "dali_chat.interpret.openai"
            assert switched["to_provider"] == "dali_chat.interpret.gemini"
            socket.send_json({"type": "audio.append", "sequence": 2, "audio": "AQI="})
            assert socket.receive_json()["type"] == "audio.accepted"
            assert socket.receive_json()["type"] == "translation.delta"
            socket.send_json({"type": "session.stop"})
