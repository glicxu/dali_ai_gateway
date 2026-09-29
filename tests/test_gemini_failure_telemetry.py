from __future__ import annotations

import asyncio
import base64
import logging

import httpx
import pytest

from app.core.errors import GatewayError
from app.providers.gemini import GeminiProvider


class _FailingClient:
    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        del kwargs
        request = httpx.Request("POST", url)
        return httpx.Response(429, request=request, json={"ignored": "payload"})


def _speech_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        json={
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "audio",
                            "data": base64.b64encode(b"pcm").decode(),
                            "mime_type": "audio/l16; rate=24000; channels=1",
                        }
                    ],
                }
            ]
        },
    )


def test_speech_failure_logs_only_content_free_transport_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = GeminiProvider(
        api_key="test-secret",
        base_url="https://provider.invalid",
        timeout_seconds=1,
        client=_FailingClient(),  # type: ignore[arg-type]
    )

    async def exercise() -> None:
        with pytest.raises(GatewayError) as captured:
            await provider.synthesize(
                model="test-model",
                input_text="private document text",
                voice="private voice choice",
                instructions="private delivery instructions",
            )
        assert captured.value.code == "ai_gateway_provider_rate_limited"
        assert captured.value.retry_after_ms == 3_600_000

    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        asyncio.run(exercise())

    assert caplog.messages == [
        "provider_failure provider=gemini operation=speech "
        "kind=HTTPStatusError status=429"
    ]
    combined = "\n".join(caplog.messages)
    assert "private" not in combined
    assert "test-secret" not in combined
    assert "ignored" not in combined


def test_speech_key_pool_rotates_between_requests() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["x-goog-api-key"])
        return _speech_response(request)

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = GeminiProvider(
            api_key="project-one",
            api_keys=("project-two",),
            base_url="https://provider.invalid",
            timeout_seconds=1,
            client=client,
        )
        for text in ("First.", "Second."):
            await provider.synthesize(
                model="test-model", input_text=text, voice="Kore", instructions=""
            )
        await client.aclose()

    asyncio.run(exercise())
    assert seen == ["project-one", "project-two"]


def test_speech_key_pool_falls_back_only_after_rate_limit() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["x-goog-api-key"]
        seen.append(key)
        if key == "project-one":
            return httpx.Response(429, request=request)
        return _speech_response(request)

    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = GeminiProvider(
            api_key="project-one",
            api_keys=("project-two",),
            base_url="https://provider.invalid",
            timeout_seconds=1,
            client=client,
        )
        await provider.synthesize_dialogue(
            model="test-model",
            turns=[{"speaker": "Jess", "text": "Hello.", "style": ""}],
            speakers=[{"speaker": "Jess", "voice": "Kore"}],
            instructions="",
        )
        await client.aclose()

    asyncio.run(exercise())
    assert seen == ["project-one", "project-two"]
