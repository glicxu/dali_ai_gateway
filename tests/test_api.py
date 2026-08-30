from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from tests.conftest import FakeProvider


def test_health_and_readiness(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}


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
    assert response.json()["model"] == "gpt-4o-mini-transcribe"
    assert fake_provider.transcribed_audio == [b"RIFF-private-audio"]


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
    assert fake_provider.realtime.closed


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
            }
        )
        assert socket.receive_json()["type"] == "session.ready"
        socket.send_json({"type": "audio.append", "audio": "AQI="})
        assert socket.receive_json()["type"] == "translation.delta"
        socket.send_json({"type": "session.stop"})
    assert fake_provider.realtime_translation.appended == ["AQI="]
    assert fake_provider.realtime_translation.closed
