from __future__ import annotations

import asyncio
import json

import httpx

from app.providers.gemini import GeminiProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai import OpenAIProvider


def test_openai_text_batch_and_realtime_protocols() -> None:
    async def exercise() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("/chat/completions"):
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "Translated."}}],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                    },
                )
            assert request.url.path.endswith("/audio/transcriptions")
            return httpx.Response(200, json={"text": "Transcript.", "language": "en"})

        socket = _FakeSocket(
            [
                {
                    "type": "conversation.item.input_audio_transcription.delta",
                    "delta": "Interim",
                    "item_id": "item-1",
                },
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Final transcript.",
                    "item_id": "item-1",
                },
            ]
        )
        connection: dict[str, object] = {}

        async def connect(url: str, **kwargs):
            connection["url"] = url
            connection["kwargs"] = kwargs
            return socket

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAIProvider(
            api_key="unit-test-key",
            base_url="https://api.openai.com/v1",
            timeout_seconds=5,
            client=client,
            connect=connect,
        )
        generated = await provider.generate(
            model="gpt-4o-mini",
            system_instruction="Translate.",
            input_text="Lecture.",
            response_format="text",
            temperature=0,
        )
        assert generated.output == "Translated."
        transcribed = await provider.transcribe(
            model="gpt-4o-mini-transcribe",
            audio=b"RIFF-audio",
            filename="lecture.wav",
            content_type="audio/wav",
            source_language="en",
            terminology_prompt="Biology",
        )
        assert transcribed.text == "Transcript."
        realtime = await provider.open_realtime(
            model="gpt-live-transcribe",
            source_language="en",
            terminology_prompt="Biology",
            terminology_keywords=("ATP",),
            audio_sample_rate_hz=24000,
        )
        assert "intent=transcription" in str(connection["url"])
        assert "model=gpt-live-transcribe" in str(connection["url"])
        update = socket.sent[0]
        transcription = update["session"]["audio"]["input"]["transcription"]
        assert transcription == {
            "model": "gpt-live-transcribe",
            "languages": ["en"],
            "prompt": "Biology",
            "keywords": ["ATP"],
        }
        assert (await realtime.next_event()).type == "transcript.delta"
        assert (await realtime.next_event()).text == "Final transcript."
        closed = await realtime.next_event()
        assert closed.type == "error"
        assert closed.code == "provider_connection_closed"
        await realtime.append("AQI=")
        await realtime.commit()
        await realtime.clear()
        assert socket.sent[-3:] == [
            {"type": "input_audio_buffer.append", "audio": "AQI="},
            {"type": "input_audio_buffer.commit"},
            {"type": "input_audio_buffer.clear"},
        ]
        await realtime.close()
        await client.aclose()
        assert len(requests) == 2

    asyncio.run(exercise())


def test_openai_gpt_5_6_omits_unsupported_temperature() -> None:
    async def exercise() -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Hello."}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAIProvider(
            api_key="openai-test-key",
            base_url="https://api.openai.com/v1",
            timeout_seconds=5,
            client=client,
        )
        result = await provider.generate(
            model="gpt-5.6-terra",
            system_instruction="Answer helpfully.",
            input_text="Hello",
            response_format="text",
            temperature=0.2,
        )

        assert result.output == "Hello."
        assert captured["model"] == "gpt-5.6-terra"
        assert "temperature" not in captured
        await client.aclose()

    asyncio.run(exercise())


def test_gemini_and_ollama_text_adapters() -> None:
    async def exercise() -> None:
        def gemini_handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-goog-api-key"] == "gemini-test-key"
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "Gemini result."}]}}
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 5,
                        "candidatesTokenCount": 2,
                    },
                },
            )

        gemini_client = httpx.AsyncClient(transport=httpx.MockTransport(gemini_handler))
        gemini = GeminiProvider(
            api_key="gemini-test-key",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout_seconds=5,
            client=gemini_client,
        )
        assert (
            await gemini.generate(
                model="gemini-2.0-flash-lite",
                system_instruction="Summarize.",
                input_text="Lecture.",
                response_format="json",
                temperature=0,
            )
        ).output == "Gemini result."

        def ollama_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "message": {"content": "Ollama result."},
                    "prompt_eval_count": 6,
                    "eval_count": 3,
                },
            )

        ollama_client = httpx.AsyncClient(transport=httpx.MockTransport(ollama_handler))
        ollama = OllamaProvider(
            base_url="http://127.0.0.1:11434",
            timeout_seconds=5,
            client=ollama_client,
        )
        assert (
            await ollama.generate(
                model="mistral",
                system_instruction="Summarize.",
                input_text="Lecture.",
                response_format="text",
                temperature=0,
            )
        ).output == "Ollama result."
        await gemini_client.aclose()
        await ollama_client.aclose()

    asyncio.run(exercise())


def test_gemini_transcription_and_speech_use_capability_specific_models() -> None:
    async def exercise() -> None:
        requests: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            value = json.loads(request.content)
            requests.append(value)
            assert request.url.path == "/v1beta/interactions"
            assert request.headers["api-revision"] == "2026-05-20"
            if value["model"] == "gemini-3.5-transcribe":
                return httpx.Response(
                    200,
                    json={
                        "output_text": "Exact transcript.",
                        "usage": {"input_tokens": 8, "output_tokens": 3},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "output_audio": {
                        "type": "audio",
                        "data": "AQIDBA==",
                        "mime_type": "audio/l16; rate=24000; channels=1",
                        "sample_rate": 24000,
                        "channels": 1,
                    },
                    "usage": {"input_tokens": 4, "output_tokens": 9},
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = GeminiProvider(
            api_key="gemini-test-key",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout_seconds=5,
            client=client,
        )
        transcript = await provider.transcribe(
            model="gemini-3.5-transcribe",
            audio=b"audio",
            filename="voice.m4a",
            content_type="audio/m4a",
            source_language="en",
            terminology_prompt="",
        )
        speech = await provider.synthesize(
            model="gemini-3.1-flash-tts-preview",
            input_text="Hello.",
            voice="Kore",
            instructions="Speak warmly.",
        )

        assert transcript.text == "Exact transcript."
        assert speech.audio.startswith(b"RIFF")
        assert speech.audio.endswith(b"\x01\x02\x03\x04")
        assert speech.content_type == "audio/wav"
        assert requests[0]["generation_config"] == {
            "transcription_config": {
                "mode": "verbatim",
                "language_codes": ["en"],
            }
        }
        assert requests[1]["generation_config"] == {
            "speech_config": [{"voice": "Kore"}]
        }
        assert requests[1]["response_format"] == {"type": "audio"}
        await client.aclose()

    asyncio.run(exercise())


def test_openai_speech_uses_audio_speech_endpoint() -> None:
    async def exercise() -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            assert request.url.path == "/v1/audio/speech"
            return httpx.Response(
                200,
                content=b"RIFF-openai",
                headers={"content-type": "audio/wav"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenAIProvider(
            api_key="openai-test-key",
            base_url="https://api.openai.com/v1",
            timeout_seconds=5,
            client=client,
        )
        result = await provider.synthesize(
            model="gpt-4o-mini-tts",
            input_text="Hello.",
            voice="alloy",
            instructions="Speak clearly.",
        )

        assert result.audio == b"RIFF-openai"
        assert captured == {
            "model": "gpt-4o-mini-tts",
            "input": "Hello.",
            "voice": "alloy",
            "response_format": "wav",
            "instructions": "Speak clearly.",
        }
        await client.aclose()

    asyncio.run(exercise())


def test_openai_image_and_gemini_video_analysis_protocols() -> None:
    async def exercise() -> None:
        def openai_handler(request: httpx.Request) -> httpx.Response:
            value = json.loads(request.content)
            assert request.url.path == "/v1/responses"
            assert value["input"][0]["content"] == [
                {"type": "input_text", "text": "Describe this image."},
                {
                    "type": "input_image",
                    "image_url": "data:image/jpeg;base64,AQI=",
                },
            ]
            return httpx.Response(
                200,
                json={
                    "output": [
                        {"content": [{"type": "output_text", "text": "A test image."}]}
                    ],
                    "usage": {"input_tokens": 9, "output_tokens": 4},
                },
            )

        openai_client = httpx.AsyncClient(transport=httpx.MockTransport(openai_handler))
        openai = OpenAIProvider(
            api_key="openai-test-key",
            base_url="https://api.openai.com/v1",
            timeout_seconds=5,
            client=openai_client,
        )
        image = await openai.analyze_media(
            model="gpt-4.1-mini",
            system_instruction="Analyze faithfully.",
            prompt="Describe this image.",
            media=b"\x01\x02",
            content_type="image/jpeg",
            media_kind="image",
            temperature=0.2,
        )
        assert image.output == "A test image."
        assert image.usage.input_tokens == 9

        def gemini_handler(request: httpx.Request) -> httpx.Response:
            value = json.loads(request.content)
            parts = value["contents"][0]["parts"]
            assert parts == [
                {"text": "Summarize this video."},
                {"inlineData": {"mimeType": "video/mp4", "data": "AwQ="}},
            ]
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "A short test clip."}]}}
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 12,
                        "candidatesTokenCount": 5,
                    },
                },
            )

        gemini_client = httpx.AsyncClient(transport=httpx.MockTransport(gemini_handler))
        gemini = GeminiProvider(
            api_key="gemini-test-key",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout_seconds=5,
            client=gemini_client,
        )
        video = await gemini.analyze_media(
            model="gemini-3.5-flash-lite",
            system_instruction="Analyze faithfully.",
            prompt="Summarize this video.",
            media=b"\x03\x04",
            content_type="video/mp4",
            media_kind="video",
            temperature=0.2,
        )
        assert video.output == "A short test clip."
        assert video.usage.output_tokens == 5
        await openai_client.aclose()
        await gemini_client.aclose()

    asyncio.run(exercise())


def test_provider_probes_send_no_product_content() -> None:
    async def exercise() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"data": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        providers = [
            OpenAIProvider(
                api_key="openai-test-key",
                base_url="https://api.openai.com/v1",
                timeout_seconds=5,
                client=client,
            ),
            GeminiProvider(
                api_key="gemini-test-key",
                base_url="https://generativelanguage.googleapis.com/v1beta",
                timeout_seconds=5,
                client=client,
            ),
            OllamaProvider(
                base_url="http://127.0.0.1:11434",
                timeout_seconds=5,
                client=client,
            ),
        ]

        for provider in providers:
            await provider.probe()

        assert [request.method for request in requests] == ["GET", "GET", "GET"]
        assert [request.url.path for request in requests] == [
            "/v1/models",
            "/v1beta/models",
            "/api/tags",
        ]
        assert all(request.content == b"" for request in requests)
        await client.aclose()

    asyncio.run(exercise())


def test_gemini_batch_audio_transcription_protocol() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            value = json.loads(request.content)
            parts = value["contents"][0]["parts"]
            assert "Return only the spoken transcript" in parts[0]["text"]
            assert parts[1]["inlineData"] == {
                "mimeType": "audio/wav",
                "data": "AQI=",
            }
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "Energy is conserved."}]}}
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 7,
                        "candidatesTokenCount": 4,
                    },
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gemini = GeminiProvider(
            api_key="gemini-test-key",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout_seconds=5,
            client=client,
        )

        result = await gemini.transcribe(
            model="gemini-3.5-flash-lite",
            audio=b"\x01\x02",
            filename="classroom.wav",
            content_type="audio/wav",
            source_language="en",
            terminology_prompt="Physics",
        )

        assert result.text == "Energy is conserved."
        assert result.usage.input_tokens == 7
        assert result.usage.output_tokens == 4
        await client.aclose()

    asyncio.run(exercise())


def test_gemini_batch_no_speech_is_live_feedback_not_provider_failure() -> None:
    async def exercise() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "candidates": [{"content": {"parts": []}}],
                        "usageMetadata": {"promptTokenCount": 3},
                    },
                )
            )
        )
        gemini = GeminiProvider(
            api_key="gemini-test-key",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout_seconds=5,
            client=client,
        )

        result = await gemini.transcribe(
            model="gemini-3.5-flash-lite",
            audio=b"\x00\x00",
            filename="classroom.wav",
            content_type="audio/wav",
            source_language="auto",
            terminology_prompt="",
        )

        assert result.text == "<no audio>"
        assert result.usage.input_tokens == 3
        await client.aclose()

    asyncio.run(exercise())


def test_openai_realtime_translation_protocol() -> None:
    async def exercise() -> None:
        socket = _FakeSocket(
            [
                {
                    "type": "session.output_transcript.delta",
                    "delta": "Guten",
                    "item_id": "translation-1",
                },
                {
                    "type": "session.output_transcript.completed",
                    "transcript": "Guten Tag.",
                    "item_id": "translation-1",
                },
                {
                    "type": "session.output_audio.delta",
                    "delta": "AQI=",
                    "item_id": "translation-1",
                },
                {
                    "type": "session.output_audio.done",
                    "item_id": "translation-1",
                },
            ]
        )
        connection: dict[str, object] = {}

        async def connect(url: str, **kwargs):
            connection["url"] = url
            return socket

        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None))
        provider = OpenAIProvider(
            api_key="unit-test-key",
            base_url="https://api.openai.com/v1",
            timeout_seconds=5,
            client=client,
            connect=connect,
        )
        realtime = await provider.open_realtime_translation(
            model="gpt-realtime-translate",
            target_language="de-DE",
            instructions="Translate faithfully.",
            audio_sample_rate_hz=24000,
        )
        assert "/v1/realtime/translations" in str(connection["url"])
        assert "model=gpt-realtime-translate" in str(connection["url"])
        assert socket.sent[0] == {
            "type": "session.update",
            "session": {
                "audio": {"output": {"language": "de"}},
                "instructions": "Translate faithfully.",
            },
        }
        assert (await realtime.next_event()).type == "translation.delta"
        assert (await realtime.next_event()).text == "Guten Tag."
        audio = await realtime.next_event()
        assert audio.type == "translation.audio.delta"
        assert audio.audio == "AQI="
        assert audio.sample_rate_hz == 24000
        assert (await realtime.next_event()).type == "translation.audio.final"
        closed = await realtime.next_event()
        assert closed.type == "error"
        assert closed.code == "provider_connection_closed"
        await realtime.append("AQI=")
        assert socket.sent[-1] == {
            "type": "session.input_audio_buffer.append",
            "audio": "AQI=",
        }
        await realtime.close()
        assert socket.sent[-1] == {"type": "session.close"}
        await client.aclose()

    asyncio.run(exercise())


def test_gemini_live_transcription_and_translation_protocols() -> None:
    async def exercise() -> None:
        transcription_socket = _FakeSocket(
            [
                {"serverContent": {"interimInputTranscription": {"text": "Interim"}}},
                {
                    "serverContent": {
                        "inputTranscription": {"text": "Final transcript."}
                    }
                },
            ]
        )
        translation_socket = _FakeSocket(
            [
                {"serverContent": {"outputTranscription": {"text": "Guten"}}},
                {
                    "serverContent": {
                        "modelTurn": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "data": "AQI=",
                                        "mimeType": "audio/pcm;rate=24000",
                                    }
                                }
                            ]
                        },
                        "outputTranscription": {"text": "Tag."},
                        "turnComplete": True,
                    }
                },
            ]
        )
        sockets = iter((transcription_socket, translation_socket))
        connections: list[str] = []

        async def connect(url: str, **kwargs):
            connections.append(url)
            return next(sockets)

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(500, json={"error": "unused"})
            )
        )
        provider = GeminiProvider(
            api_key="gemini-test-key",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout_seconds=5,
            client=client,
            connect=connect,
        )
        transcription = await provider.open_realtime(
            model="gemini-3.5-transcribe-live",
            source_language="en-US",
            terminology_prompt="Biology",
            terminology_keywords=("ATP", "mitochondria"),
            audio_sample_rate_hz=16000,
        )
        setup = transcription_socket.sent[0]["setup"]
        assert setup["model"] == "models/gemini-3.5-transcribe-live"
        assert setup["generationConfig"] == {"responseModalities": ["TEXT"]}
        assert setup["inputAudioTranscription"] == {
            "languageCodes": ["en-US"],
            "mode": "SMART",
            "customVocabulary": ["ATP", "mitochondria"],
        }
        await transcription.append("AQI=")
        await transcription.commit()
        assert transcription_socket.sent[-2:] == [
            {
                "realtimeInput": {
                    "audio": {"data": "AQI=", "mimeType": "audio/pcm;rate=16000"}
                }
            },
            {"realtimeInput": {"audioStreamEnd": True}},
        ]
        assert (await transcription.next_event()).type == "transcript.delta"
        assert (await transcription.next_event()).text == "Final transcript."
        closed = await transcription.next_event()
        assert closed.type == "error"
        assert closed.code == "provider_connection_closed"

        translation = await provider.open_realtime_translation(
            model="gemini-3.5-live-translate-preview",
            target_language="de-DE",
            instructions="Translate faithfully.",
            audio_sample_rate_hz=16000,
        )
        setup = translation_socket.sent[0]["setup"]
        assert setup["model"] == "models/gemini-3.5-live-translate-preview"
        assert setup["generationConfig"]["translationConfig"] == {
            "targetLanguageCode": "de-DE",
            "echoTargetLanguage": True,
        }
        assert setup["inputAudioTranscription"] == {}
        assert setup["outputAudioTranscription"] == {}
        assert (await translation.next_event()).text == "Guten"
        audio = await translation.next_event()
        assert audio.type == "translation.audio.delta"
        assert audio.audio == "AQI="
        assert (await translation.next_event()).text == "Tag."
        final = await translation.next_event()
        assert final.type == "translation.final"
        assert final.text == "Guten Tag."
        assert (await translation.next_event()).type == "translation.audio.final"
        closed = await translation.next_event()
        assert closed.type == "error"
        assert closed.code == "provider_connection_closed"
        assert all("gemini-test-key" in value for value in connections)
        await transcription.close()
        await translation.close()
        await client.aclose()

    asyncio.run(exercise())


def test_gemini_live_session_requests_rotation_before_provider_limit() -> None:
    async def exercise() -> None:
        socket = _BlockingFakeSocket()

        async def connect(url: str, **kwargs):
            return socket

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(500, json={"error": "unused"})
            )
        )
        provider = GeminiProvider(
            api_key="gemini-test-key",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout_seconds=5,
            realtime_session_max_seconds=0.01,
            client=client,
            connect=connect,
        )

        realtime = await provider.open_realtime(
            model="gemini-3.5-transcribe-live",
            source_language="en-US",
            terminology_prompt="",
            terminology_keywords=(),
            audio_sample_rate_hz=16000,
        )

        event = await asyncio.wait_for(realtime.next_event(), timeout=1)
        assert event.type == "error"
        assert event.code == "provider_session_rotation_required"
        await realtime.close()
        await client.aclose()

    asyncio.run(exercise())


class _FakeSocket:
    def __init__(self, incoming: list[dict[str, object]]) -> None:
        self._incoming = incoming
        self.sent: list[dict[str, object]] = []
        self.closed = False

    async def send(self, value: str) -> None:
        self.sent.append(json.loads(value))

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        async def values():
            for value in self._incoming:
                yield json.dumps(value)

        return values()


class _BlockingFakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self._closed = asyncio.Event()

    async def send(self, value: str) -> None:
        self.sent.append(json.loads(value))

    async def close(self) -> None:
        self.closed = True
        self._closed.set()

    def __aiter__(self):
        async def values():
            await self._closed.wait()
            if False:
                yield ""

        return values()
