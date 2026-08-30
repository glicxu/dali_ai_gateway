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
        assert (await translation.next_event()).text == "Tag."
        final = await translation.next_event()
        assert final.type == "translation.final"
        assert final.text == "Guten Tag."
        assert all("gemini-test-key" in value for value in connections)
        await transcription.close()
        await translation.close()
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
