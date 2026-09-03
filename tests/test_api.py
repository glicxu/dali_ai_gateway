from __future__ import annotations

import copy
import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from starlette.websockets import WebSocketDisconnect

from app.api.routes import _send_realtime_event
from app.core.config import DEFAULT_WORKLOAD_GRANTS, Settings
from app.core.security import WorkloadPrincipal
from app.core.usage_delivery import UsageDelivery
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


class _PlannedRotationSession:
    def __init__(self) -> None:
        self.closed = False
        self._rotation_sent = False
        self._wait = asyncio.Event()

    async def append(self, _: str) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def clear(self) -> None:
        return None

    async def next_event(self) -> RealtimeEvent:
        if not self._rotation_sent:
            self._rotation_sent = True
            return RealtimeEvent("error", code="provider_session_rotation_required")
        await self._wait.wait()
        raise AssertionError("planned rotation session should be closed")

    async def close(self) -> None:
        self.closed = True
        self._wait.set()


class _PlannedRotationProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.planned_rotation = _PlannedRotationSession()
        self._sessions = [self.planned_rotation, self.realtime_translation]

    async def open_realtime_translation(self, **kwargs):
        self.realtime_translation_outputs.append(kwargs["outputs"])
        return self._sessions.pop(0)


class _SlowWebSocket:
    def __init__(self) -> None:
        self.closed: tuple[int, str] | None = None
        self._never = asyncio.Event()

    async def send_json(self, _: dict[str, object]) -> None:
        await self._never.wait()

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)


class _CaptureUsageSink:
    def __init__(self) -> None:
        self.measurements = []

    async def put(self, measurement):
        self.measurements.append(measurement)
        return "accepted"


class _FailingUsageSink:
    async def put(self, measurement):
        del measurement
        raise RuntimeError("sink unavailable")


class _FailingTextProvider(FakeProvider):
    async def generate(self, **kwargs):
        del kwargs
        raise RuntimeError("provider outcome unavailable")


def test_health_and_readiness(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    readiness = client.get("/health/ready").json()
    assert readiness["status"] == "ready"
    assert readiness["draining"] is False
    assert readiness["active_leases"] == 0
    assert "generation_id" in readiness
    assert "provider_counts" in readiness
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "dali_gateway_ready 1" in metrics.text
    assert "dali_gateway_active_leases 0" in metrics.text
    assert "Private lecture" not in metrics.text


def test_drain_rejects_new_work_and_removes_readiness(
    client: TestClient, headers: dict[str, str]
) -> None:
    client.app.state.container.begin_drain()
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503
    response = client.post(
        "/ai/v1/text/generations",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "product": "classroom",
            "profile": "classroom.translation.economy",
            "system_instruction": "Translate faithfully.",
            "input": "Private lecture text.",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_gateway_draining"


def test_planned_drain_rotates_and_closes_active_realtime_session(
    client: TestClient, headers: dict[str, str], fake_provider: FakeProvider
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
        assert socket.receive_json()["type"] == "session.ready"
        client.app.state.container.begin_drain()
        rotation = socket.receive_json()
        assert rotation["type"] == "session.rotation_required"
        assert rotation["accepted_sequence"] == 0
        assert socket.receive_json()["type"] == "usage.final"
        closed = socket.receive_json()
        assert closed["type"] == "session.closed"
        assert closed["failure_stage"] == "gateway"
        assert closed["retryable"] is True
    assert fake_provider.realtime_translation.closed_event.wait(timeout=1)


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


def test_batch_text_generation_delivers_content_free_usage(
    client: TestClient, headers: dict[str, str]
) -> None:
    sink = _CaptureUsageSink()
    client.app.state.container.service.usage_delivery = UsageDelivery(
        sink, max_attempts=1, retry_delay_seconds=0
    )
    request_id = str(uuid4())
    response = client.post(
        "/ai/v1/text/generations",
        headers=headers,
        json={
            "request_id": request_id,
            "product": "classroom",
            "profile": "classroom.translation.economy",
            "system_instruction": "Translate faithfully.",
            "input": "Private lecture text.",
        },
    )
    assert response.status_code == 200
    assert len(sink.measurements) == 1
    measurement = sink.measurements[0]
    assert str(measurement.request_id) == request_id
    assert measurement.input_tokens.value == 7
    assert measurement.output_tokens.value == 3
    serialized = measurement.model_dump_json()
    assert "Private lecture" not in serialized
    assert "Translate faithfully" not in serialized
    metrics = client.get("/metrics").text
    assert 'dali_gateway_events_total{outcome="usage_delivery_accepted"} 1' in metrics
    assert "Private lecture" not in metrics


def test_batch_usage_failure_is_explicit_and_non_retryable(
    client: TestClient, headers: dict[str, str], fake_provider: FakeProvider
) -> None:
    client.app.state.container.service.usage_delivery = UsageDelivery(
        _FailingUsageSink(), max_attempts=1, retry_delay_seconds=0
    )
    response = client.post(
        "/ai/v1/text/generations",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "product": "classroom",
            "profile": "classroom.translation.economy",
            "system_instruction": "Translate faithfully.",
            "input": "Private lecture text.",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "ai_gateway_usage_delivery_unconfirmed",
        "message": "AI work completed but usage delivery was not confirmed.",
        "retryable": False,
        "retry_after_ms": None,
    }
    assert fake_provider.generated_inputs == ["Private lecture text."]


def test_ambiguous_batch_provider_outcome_is_measured_and_not_retryable(
    headers: dict[str, str], settings: Settings
) -> None:
    sink = _CaptureUsageSink()
    application = create_app(
        settings,
        providers={"gemini": _FailingTextProvider(), "openai": FakeProvider()},
    )
    application.state.container.service.usage_delivery = UsageDelivery(
        sink, max_attempts=1, retry_delay_seconds=0
    )
    with TestClient(application) as value:
        response = value.post(
            "/ai/v1/text/generations",
            headers=headers,
            json={
                "request_id": str(uuid4()),
                "product": "classroom",
                "profile": "classroom.translation.economy",
                "system_instruction": "Translate faithfully.",
                "input": "Private lecture text.",
            },
        )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "ai_gateway_provider_outcome_ambiguous"
    assert response.json()["error"]["retryable"] is False
    assert len(sink.measurements) == 1
    assert sink.measurements[0].disposition == "ambiguous"
    assert "Private lecture" not in sink.measurements[0].model_dump_json()


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


def test_realtime_v2_transcription_acknowledges_input(
    client: TestClient,
    headers: dict[str, str],
    fake_provider: FakeProvider,
) -> None:
    request_id = str(uuid4())
    with client.websocket_connect(
        "/ai/v2/realtime/transcriptions", headers=headers
    ) as socket:
        socket.send_json(
            {
                "type": "session.start",
                "request_id": request_id,
                "product": "classroom",
                "profile": "classroom.transcription.live",
                "source_language": "en",
            }
        )
        ready = socket.receive_json()
        assert ready["type"] == "session.ready"
        assert ready["request_id"] == request_id
        assert ready["outputs"] == ["source_transcript"]
        socket.send_json({"type": "audio.append", "sequence": 1, "audio": "AQI="})
        assert socket.receive_json()["type"] == "audio.accepted"
        assert socket.receive_json() == {
            "type": "transcript.delta",
            "session_id": request_id,
            "window_id": ready["window_id"],
            "lane_id": "primary",
            "provider_ref": "classroom.transcription.live",
            "sequence": 2,
            "text": "Lecture",
            "item_id": "item-1",
        }
        socket.send_json({"type": "session.stop"})
        assert socket.receive_json()["type"] == "usage.final"
        assert socket.receive_json()["type"] == "session.closed"

    assert fake_provider.realtime.appended == ["AQI="]
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
        translated = socket.receive_json()
        assert translated["type"] == "translation.delta"
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
    sink = _CaptureUsageSink()
    client.app.state.container.service.usage_delivery = UsageDelivery(
        sink, max_attempts=1, retry_delay_seconds=0
    )
    request_id = str(uuid4())
    with client.websocket_connect(
        "/ai/v2/realtime/translations", headers=headers
    ) as socket:
        socket.send_json(
            {
                "type": "session.start",
                "request_id": request_id,
                "product": "classroom",
                "profile": "classroom.translation.live",
                "target_language": "de-DE",
            }
        )
        ready = socket.receive_json()
        assert ready["type"] == "session.ready"
        assert ready["sequence"] == 0
        assert ready["session_id"] == ready["request_id"]
        assert ready["lane_id"] == "primary"
        assert ready["provider_ref"] == "classroom.translation.live"
        socket.send_json({"type": "audio.append", "sequence": 1, "audio": "AQI="})
        accepted = socket.receive_json()
        assert accepted["type"] == "audio.accepted"
        assert accepted["accepted_sequence"] == 1
        assert accepted["session_id"] == ready["session_id"]
        assert accepted["provider_ref"] == ready["provider_ref"]
        translated = socket.receive_json()
        assert translated["type"] == "translation.delta"
        socket.send_json({"type": "session.stop"})
        usage = socket.receive_json()
        assert usage["type"] == "usage.final"
        assert usage["accepted_input_chunks"] == 1
        closed = socket.receive_json()
        assert closed["type"] == "session.closed"
        assert closed["disposition"] == "cancelled"
        assert [
            ready["sequence"],
            accepted["sequence"],
            translated["sequence"],
            usage["sequence"],
            closed["sequence"],
        ] == [0, 1, 2, 3, 4]
    assert fake_provider.realtime_translation.closed_event.wait(timeout=1)
    assert len(sink.measurements) == 1
    measurement = sink.measurements[0]
    assert str(measurement.request_id) == request_id
    assert measurement.disposition == "cancelled"
    assert measurement.source_audio_received_bytes.value == 2
    assert measurement.source_audio_accepted_bytes.value == 2
    assert measurement.route_id == "openai.gpt-realtime-translate"


def test_realtime_v2_rejects_duplicate_input_sequence(
    client: TestClient, headers: dict[str, str], fake_provider: FakeProvider
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
        socket.receive_json()
        socket.send_json({"type": "audio.append", "sequence": 1, "audio": "AQI="})
        assert socket.receive_json()["type"] == "audio.accepted"
        assert socket.receive_json()["type"] == "translation.delta"
        socket.send_json({"type": "audio.append", "sequence": 1, "audio": "AQI="})
        error = socket.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "ai_gateway_request_invalid"
        assert fake_provider.realtime_translation.appended == ["AQI="]


def test_realtime_v2_rejects_input_sequence_gap(
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
            }
        )
        socket.receive_json()
        socket.send_json({"type": "audio.append", "sequence": 2, "audio": "AQI="})
        error = socket.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "ai_gateway_request_invalid"


def test_realtime_v2_disconnect_closes_provider_session(
    client: TestClient, headers: dict[str, str], fake_provider: FakeProvider
) -> None:
    sink = _CaptureUsageSink()
    client.app.state.container.service.usage_delivery = UsageDelivery(
        sink, max_attempts=1, retry_delay_seconds=0
    )
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
        assert socket.receive_json()["type"] == "session.ready"
    assert fake_provider.realtime_translation.closed_event.wait(timeout=1)
    assert len(sink.measurements) == 1
    assert sink.measurements[0].disposition == "disconnected"


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
    application = create_app(
        settings, providers={"openai": provider, "gemini": provider}
    )
    sink = _CaptureUsageSink()
    application.state.container.service.usage_delivery = UsageDelivery(
        sink, max_attempts=1, retry_delay_seconds=0
    )
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
            ready = socket.receive_json()
            assert ready["type"] == "session.ready"
            assert ready["audio_sample_rate_hz"] == 24000
            assert ready["outputs"] == ["target_transcript", "translated_audio"]
            assert ready["max_chunk_bytes"] == 256 * 1024
            assert ready["max_unacknowledged_chunks"] == 1
            assert ready["max_unacknowledged_bytes"] == 256 * 1024
            assert ready["max_outbound_events"] == 1
            socket.send_json({"type": "audio.append", "sequence": 1, "audio": "AQI="})
            events = [socket.receive_json(), socket.receive_json()]
            if events[0]["type"] != "window.failed":
                events.append(socket.receive_json())
            failed = next(event for event in events if event["type"] == "window.failed")
            assert failed["failure_stage"] == "provider_stream"
            assert failed["partial"] is True
            switched = next(
                event for event in events if event["type"] == "provider.switched"
            )
            assert switched["type"] == "provider.switched"
            assert switched["from_provider"] == "dali_chat.interpret.openai"
            assert switched["to_provider"] == "dali_chat.interpret.gemini"
            socket.send_json({"type": "audio.append", "sequence": 2, "audio": "AQI="})
            assert socket.receive_json()["type"] == "audio.accepted"
            assert socket.receive_json()["type"] == "translation.delta"
            # Accepted audio from the failed window is never replayed.
            assert provider.realtime_translation.appended == ["AQI="]
            socket.send_json({"type": "session.stop"})
            assert socket.receive_json()["type"] == "usage.final"
            assert socket.receive_json()["type"] == "session.closed"
    assert len(sink.measurements) == 1
    assert sink.measurements[0].fallback_count == 1
    assert sink.measurements[0].route_id == "gemini.gemini-3.5-live-translate-preview"


def test_realtime_v2_rejects_oversized_audio_before_provider_append() -> None:
    provider = FakeProvider()
    grants = copy.deepcopy(DEFAULT_WORKLOAD_GRANTS["dali_chat_server"])
    grants["enabled"] = True
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        workload_grants_json=json.dumps({"dali_chat_server": grants}),
        caller_limits_json='{"dali_chat_server":1}',
        legacy_auth_workload_ids_json='["dali_chat_server"]',
    )
    application = create_app(
        settings, providers={"openai": provider, "gemini": provider}
    )
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
                    "target_language": "zh-CN",
                }
            )
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_json(
                {"type": "audio.append", "sequence": 1, "audio": "A" * (256 * 1024 + 1)}
            )
            error = socket.receive_json()
            assert error["error"]["code"] == "ai_gateway_request_invalid"
            assert provider.realtime_translation.appended == []


def test_realtime_v2_planned_provider_expiry_rotates_without_failure() -> None:
    provider = _PlannedRotationProvider()
    grants = copy.deepcopy(DEFAULT_WORKLOAD_GRANTS["dali_chat_server"])
    grants["enabled"] = True
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        workload_grants_json=json.dumps({"dali_chat_server": grants}),
        caller_limits_json='{"dali_chat_server":1}',
        legacy_auth_workload_ids_json='["dali_chat_server"]',
    )
    application = create_app(
        settings, providers={"openai": provider, "gemini": provider}
    )
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
                    "target_language": "zh-CN",
                }
            )
            assert socket.receive_json()["type"] == "session.ready"
            rotation = socket.receive_json()
            closed = socket.receive_json()
            assert rotation["type"] == "session.rotation_required"
            assert closed["type"] == "window.closed"
            assert closed["disposition"] == "complete"
            assert provider.planned_rotation.closed
            socket.send_json({"type": "audio.append", "sequence": 1, "audio": "AQI="})
            assert socket.receive_json()["type"] == "audio.accepted"
            assert socket.receive_json()["type"] == "translation.delta"
            socket.send_json({"type": "session.stop"})


def test_realtime_outbound_send_timeout_closes_slow_consumer() -> None:
    async def exercise() -> None:
        websocket = _SlowWebSocket()
        with pytest.raises(WebSocketDisconnect) as captured:
            await _send_realtime_event(
                websocket, {"type": "translation.delta"}, timeout_seconds=0.001
            )
        assert captured.value.code == 1013
        assert websocket.closed == (1013, "slow_consumer")

    asyncio.run(exercise())


def test_realtime_v2_unknown_provider_event_fails_closed() -> None:
    provider = FakeProvider()
    provider.realtime_translation.events.put_nowait(RealtimeEvent("provider.unknown"))
    grants = copy.deepcopy(DEFAULT_WORKLOAD_GRANTS["dali_chat_server"])
    grants["enabled"] = True
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        workload_grants_json=json.dumps({"dali_chat_server": grants}),
        caller_limits_json='{"dali_chat_server":1}',
        legacy_auth_workload_ids_json='["dali_chat_server"]',
    )
    application = create_app(
        settings, providers={"openai": provider, "gemini": provider}
    )
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
                    "target_language": "zh-CN",
                }
            )
            assert socket.receive_json()["type"] == "session.ready"
            assert socket.receive_json()["type"] == "usage.final"
            closed = socket.receive_json()
            assert closed["type"] == "session.closed"
            assert closed["failure_stage"] == "provider_output"
            assert closed["retryable"] is False


def test_realtime_v2_forwards_normalized_translated_audio_metadata() -> None:
    provider = FakeProvider()
    provider.realtime_translation.events.put_nowait(
        RealtimeEvent(
            "translation.audio.delta",
            audio="AQI=",
            item_id="provider-response-1",
            content_type="audio/pcm",
            sample_rate_hz=24000,
            channels=1,
            sample_format="s16le",
        )
    )
    grants = copy.deepcopy(DEFAULT_WORKLOAD_GRANTS["dali_chat_server"])
    grants["enabled"] = True
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        workload_grants_json=json.dumps({"dali_chat_server": grants}),
        caller_limits_json='{"dali_chat_server":1}',
        legacy_auth_workload_ids_json='["dali_chat_server"]',
    )
    application = create_app(
        settings, providers={"openai": provider, "gemini": provider}
    )
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
                    "target_language": "zh-CN",
                }
            )
            assert socket.receive_json()["type"] == "session.ready"
            audio = socket.receive_json()
            assert audio["type"] == "translation.audio.delta"
            assert audio["response_id"] == "provider-response-1"
            assert audio["target_language"] == "zh-CN"
            assert audio["content_type"] == "audio/pcm"
            assert audio["sample_rate_hz"] == 24000
            assert audio["channels"] == 1
            assert audio["sample_format"] == "s16le"
            socket.send_json({"type": "session.stop"})


def test_realtime_v2_fails_closed_when_provider_ignores_output_selection() -> None:
    provider = FakeProvider()
    provider.realtime_translation.events.put_nowait(
        RealtimeEvent("transcript.delta", text="must-not-forward", item_id="source-1")
    )
    grants = copy.deepcopy(DEFAULT_WORKLOAD_GRANTS["dali_chat_server"])
    grants["enabled"] = True
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        workload_grants_json=json.dumps({"dali_chat_server": grants}),
        caller_limits_json='{"dali_chat_server":1}',
        legacy_auth_workload_ids_json='["dali_chat_server"]',
    )
    application = create_app(
        settings, providers={"openai": provider, "gemini": provider}
    )
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
                    "target_language": "zh-CN",
                    "outputs": ["target_transcript"],
                }
            )
            assert socket.receive_json()["type"] == "session.ready"
            assert socket.receive_json()["type"] == "usage.final"
            closed = socket.receive_json()
            assert closed["type"] == "session.closed"
            assert closed["failure_stage"] == "provider_output"


def test_realtime_v2_provider_terminal_event_has_failure_stage() -> None:
    provider = _FailoverProvider()
    grants = copy.deepcopy(DEFAULT_WORKLOAD_GRANTS["dali_chat_server"])
    grants["enabled"] = True
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        workload_grants_json=json.dumps({"dali_chat_server": grants}),
        caller_limits_json='{"dali_chat_server":1}',
        legacy_auth_workload_ids_json='["dali_chat_server"]',
    )
    application = create_app(
        settings, providers={"openai": provider, "gemini": provider}
    )
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
                    "target_language": "zh-CN",
                }
            )
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_json({"type": "audio.append", "sequence": 1, "audio": "AQI="})
            assert socket.receive_json()["type"] == "audio.accepted"
            assert socket.receive_json()["type"] == "window.failed"
            usage = socket.receive_json()
            assert usage["type"] == "usage.final"
            assert usage["accepted_input_chunks"] == 1
            closed = socket.receive_json()
            assert closed["type"] == "session.closed"
            assert closed["failure_stage"] == "provider_stream"
            assert closed["retryable"] is True


def test_realtime_v2_rejects_fallback_alias_of_primary_route() -> None:
    profiles = {
        "chat.interpret.primary": {
            "capability": "realtime_translation",
            "provider": "openai",
            "model": "gpt-realtime-translate",
        },
        "chat.interpret.alias": {
            "capability": "realtime_translation",
            "provider": "openai",
            "model": "gpt-realtime-translate",
        },
    }
    grants = {
        "dali_chat_server": {
            "products": ["dali_chat"],
            "profiles": list(profiles),
            "capabilities": ["realtime_translation"],
        }
    }
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        model_profiles_json=json.dumps(profiles),
        workload_grants_json=json.dumps(grants),
        caller_limits_json='{"dali_chat_server":1}',
        legacy_auth_workload_ids_json='["dali_chat_server"]',
    )
    application = create_app(settings, providers={"openai": FakeProvider()})
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
                    "profile": "chat.interpret.primary",
                    "fallback_profile": "chat.interpret.alias",
                    "policy": "windowed_failover",
                    "target_language": "zh-CN",
                }
            )
            error = socket.receive_json()
            assert error["error"]["code"] == "ai_gateway_profile_not_allowed"


@pytest.mark.parametrize(
    ("fallback_field", "fallback_value"),
    [("privacy_class", "restricted"), ("usage_authority", "authoritative")],
)
def test_realtime_v2_rejects_fallback_that_weakens_route_terms(
    fallback_field: str, fallback_value: str
) -> None:
    profiles = {
        "chat.interpret.primary": {
            "capability": "realtime_translation",
            "provider": "openai",
            "model": "gpt-realtime-translate",
        },
        "chat.interpret.fallback": {
            "capability": "realtime_translation",
            "provider": "gemini",
            "model": "gemini-live-translate",
            fallback_field: fallback_value,
        },
    }
    grants = {
        "dali_chat_server": {
            "products": ["dali_chat"],
            "profiles": list(profiles),
            "capabilities": ["realtime_translation"],
        }
    }
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        model_profiles_json=json.dumps(profiles),
        workload_grants_json=json.dumps(grants),
        caller_limits_json='{"dali_chat_server":1}',
        legacy_auth_workload_ids_json='["dali_chat_server"]',
    )
    application = create_app(
        settings, providers={"openai": FakeProvider(), "gemini": FakeProvider()}
    )
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
                    "profile": "chat.interpret.primary",
                    "fallback_profile": "chat.interpret.fallback",
                    "policy": "windowed_failover",
                    "target_language": "zh-CN",
                }
            )
            error = socket.receive_json()
            assert error["error"]["code"] == "ai_gateway_profile_not_allowed"


def test_realtime_route_failure_opens_provider_circuit() -> None:
    grants = copy.deepcopy(DEFAULT_WORKLOAD_GRANTS["dali_chat_server"])
    grants["enabled"] = True
    settings = Settings(
        service_tokens_json=SecretStr('{"dali_chat_server":"chat-test-token"}'),
        workload_grants_json=json.dumps({"dali_chat_server": grants}),
        caller_limits_json='{"dali_chat_server":1}',
        legacy_auth_workload_ids_json='["dali_chat_server"]',
        provider_circuit_enabled=True,
        provider_circuit_failure_threshold=1,
    )
    application = create_app(
        settings, providers={"openai": FakeProvider(), "gemini": FakeProvider()}
    )
    request = RealtimeTranslationStart(
        type="session.start",
        request_id=uuid4(),
        product="dali_chat",
        profile="dali_chat.interpret.openai",
        target_language="zh-CN",
    )
    with TestClient(application):
        service = application.state.container.service
        service.record_realtime_route_failure(
            caller="dali_chat_server", request=request, profile_name=request.profile
        )
        assert (
            service.circuits.snapshot("openai.gpt-realtime-translate").state == "open"
        )
