from __future__ import annotations

import asyncio
import audioop
import base64
import binascii
import json
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect as websocket_connect

from app.core.errors import PROVIDER_UNAVAILABLE
from app.models import UsageMeasurement
from app.providers.base import RealtimeEvent, TextResult, TranscriptionResult


class GeminiProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
        connect: Callable[..., Awaitable[Any]] = websocket_connect,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._connect = connect

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate(
        self,
        *,
        model: str,
        system_instruction: str,
        input_text: str,
        response_format: str,
        temperature: float,
    ) -> TextResult:
        generation_config: dict[str, object] = {"temperature": temperature}
        if response_format == "json":
            generation_config["responseMimeType"] = "application/json"
        try:
            response = await self._client.post(
                f"{self._base_url}/models/{model}:generateContent",
                headers={"x-goog-api-key": self._api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system_instruction}]},
                    "contents": [{"role": "user", "parts": [{"text": input_text}]}],
                    "generationConfig": generation_config,
                },
            )
            response.raise_for_status()
            value = response.json()
            output = value["candidates"][0]["content"]["parts"][0]["text"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            raise PROVIDER_UNAVAILABLE from error
        if not isinstance(output, str) or not output.strip():
            raise PROVIDER_UNAVAILABLE
        usage = value.get("usageMetadata", {})
        return TextResult(
            output=output.strip(),
            usage=UsageMeasurement(
                input_tokens=_int(usage.get("promptTokenCount")),
                output_tokens=_int(usage.get("candidatesTokenCount")),
            ),
        )

    async def transcribe(
        self,
        *,
        model: str,
        audio: bytes,
        filename: str,
        content_type: str,
        source_language: str,
        terminology_prompt: str,
    ) -> TranscriptionResult:
        del filename
        language_instruction = (
            "Detect the spoken language."
            if source_language == "auto"
            else f"The spoken language is {source_language}."
        )
        prompt = (
            "Transcribe the classroom audio faithfully. Return only the spoken "
            "transcript, without commentary, timestamps, or translation. "
            f"{language_instruction} Preserve uncertainty rather than inventing "
            f"words. Terminology context: {terminology_prompt}"
        )
        try:
            response = await self._client.post(
                f"{self._base_url}/models/{model}:generateContent",
                headers={"x-goog-api-key": self._api_key},
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {"text": prompt},
                                {
                                    "inlineData": {
                                        "mimeType": content_type,
                                        "data": base64.b64encode(audio).decode("ascii"),
                                    }
                                },
                            ],
                        }
                    ],
                    "generationConfig": {"temperature": 0},
                },
            )
            response.raise_for_status()
            value = response.json()
            output = value["candidates"][0]["content"]["parts"][0]["text"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            raise PROVIDER_UNAVAILABLE from error
        if not isinstance(output, str) or not output.strip():
            raise PROVIDER_UNAVAILABLE
        usage = value.get("usageMetadata", {})
        return TranscriptionResult(
            text=output.strip(),
            detected_language=None,
            usage=UsageMeasurement(
                input_tokens=_int(usage.get("promptTokenCount")),
                output_tokens=_int(usage.get("candidatesTokenCount")),
            ),
        )

    async def open_realtime(
        self,
        *,
        model: str,
        source_language: str,
        terminology_prompt: str,
        terminology_keywords: tuple[str, ...],
        audio_sample_rate_hz: int,
    ) -> GeminiRealtimeSession:
        del terminology_prompt
        input_transcription: dict[str, object] = {
            "languageCodes": [] if source_language == "auto" else [source_language],
            "mode": "SMART",
        }
        if terminology_keywords:
            input_transcription["customVocabulary"] = list(terminology_keywords)
        session = GeminiRealtimeSession(
            url=_live_url(self._base_url, self._api_key),
            model=model,
            mode="transcription",
            setup_config={
                "generationConfig": {"responseModalities": ["TEXT"]},
                "inputAudioTranscription": input_transcription,
            },
            audio_sample_rate_hz=audio_sample_rate_hz,
            connect=self._connect,
        )
        await session.start()
        return session

    async def open_realtime_translation(
        self,
        *,
        model: str,
        target_language: str,
        instructions: str,
        audio_sample_rate_hz: int,
    ) -> GeminiRealtimeSession:
        del instructions
        session = GeminiRealtimeSession(
            url=_live_url(self._base_url, self._api_key),
            model=model,
            mode="translation",
            setup_config={
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "translationConfig": {
                        "targetLanguageCode": target_language,
                        "echoTargetLanguage": True,
                    },
                },
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
            },
            audio_sample_rate_hz=audio_sample_rate_hz,
            connect=self._connect,
        )
        await session.start()
        return session


class GeminiRealtimeSession:
    """Normalized Gemini Live session; audio output is intentionally discarded."""

    def __init__(
        self,
        *,
        url: str,
        model: str,
        mode: Literal["transcription", "translation"],
        setup_config: dict[str, object],
        audio_sample_rate_hz: int,
        connect: Callable[..., Awaitable[Any]],
    ) -> None:
        self._url = url
        self._model = model
        self._mode = mode
        self._setup_config = setup_config
        self._audio_sample_rate_hz = audio_sample_rate_hz
        self._connect = connect
        self._socket: Any | None = None
        self._events: asyncio.Queue[RealtimeEvent] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self._rate_state: tuple | None = None
        self._translation = ""
        self._item_number = 1

    async def start(self) -> None:
        try:
            self._socket = await self._connect(self._url, max_size=2**22)
            await self._send(
                {
                    "setup": {
                        "model": f"models/{self._model}",
                        **self._setup_config,
                    }
                }
            )
            self._reader = asyncio.create_task(self._read_events())
        except Exception as error:
            await self.close()
            raise PROVIDER_UNAVAILABLE from error

    async def append(self, audio_base64: str) -> None:
        try:
            pcm = base64.b64decode(audio_base64, validate=True)
            if len(pcm) % 2:
                raise ValueError("PCM16 audio must contain complete samples")
            if self._audio_sample_rate_hz != 16000:
                pcm, self._rate_state = audioop.ratecv(
                    pcm,
                    2,
                    1,
                    self._audio_sample_rate_hz,
                    16000,
                    self._rate_state,
                )
            encoded = base64.b64encode(pcm).decode("ascii")
        except (binascii.Error, ValueError) as error:
            raise PROVIDER_UNAVAILABLE from error
        await self._send(
            {
                "realtimeInput": {
                    "audio": {
                        "data": encoded,
                        "mimeType": "audio/pcm;rate=16000",
                    }
                }
            }
        )

    async def commit(self) -> None:
        await self._send({"realtimeInput": {"audioStreamEnd": True}})

    async def clear(self) -> None:
        self._rate_state = None
        self._translation = ""

    async def next_event(self) -> RealtimeEvent:
        return await self._events.get()

    async def close(self) -> None:
        reader = self._reader
        self._reader = None
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        socket = self._socket
        self._socket = None
        if socket is not None:
            try:
                await socket.close()
            except Exception:
                pass
        self._rate_state = None
        self._translation = ""

    async def _send(self, value: dict[str, object]) -> None:
        if self._socket is None:
            raise PROVIDER_UNAVAILABLE
        try:
            await self._socket.send(json.dumps(value, separators=(",", ":")))
        except Exception as error:
            raise PROVIDER_UNAVAILABLE from error

    async def _read_events(self) -> None:
        try:
            if self._socket is None:
                raise PROVIDER_UNAVAILABLE
            async for raw in self._socket:
                try:
                    value = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(value, dict):
                    continue
                if value.get("error") is not None:
                    await self._events.put(
                        RealtimeEvent("error", code="provider_realtime_error")
                    )
                    continue
                content = value.get("serverContent")
                if not isinstance(content, dict):
                    continue
                if self._mode == "transcription":
                    await self._read_transcription(content)
                else:
                    await self._read_translation(content)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._events.put(
                RealtimeEvent("error", code="provider_connection_closed")
            )

    async def _read_transcription(self, content: dict[str, object]) -> None:
        interim = _transcription_text(content.get("interimInputTranscription"))
        if interim:
            await self._events.put(
                RealtimeEvent(
                    "transcript.delta",
                    text=interim,
                    item_id=self._item_id,
                )
            )
        final = _transcription_text(content.get("inputTranscription"))
        if final:
            await self._events.put(
                RealtimeEvent(
                    "transcript.final",
                    text=final,
                    item_id=self._item_id,
                )
            )
            self._item_number += 1

    async def _read_translation(self, content: dict[str, object]) -> None:
        translated = _transcription_text(content.get("outputTranscription"))
        if translated:
            self._translation = _merge_text(self._translation, translated)
            await self._events.put(
                RealtimeEvent(
                    "translation.delta",
                    text=translated,
                    item_id=self._item_id,
                )
            )
        if content.get("turnComplete") is True and self._translation.strip():
            await self._events.put(
                RealtimeEvent(
                    "translation.final",
                    text=self._translation.strip(),
                    item_id=self._item_id,
                )
            )
            self._translation = ""
            self._item_number += 1

    @property
    def _item_id(self) -> str:
        return f"gemini-{self._item_number}"


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _live_url(base_url: str, api_key: str) -> str:
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = (
        "/ws/google.ai.generativelanguage.v1beta.GenerativeService."
        "BidiGenerateContent"
    )
    return urlunsplit((scheme, parsed.netloc, path, urlencode({"key": api_key}), ""))


def _transcription_text(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    text_value = value.get("text")
    if not isinstance(text_value, str):
        return None
    stripped = text_value.strip()
    return stripped or None


def _merge_text(current: str, addition: str) -> str:
    if not current:
        return addition
    if addition.startswith(current):
        return addition
    if current.endswith(addition):
        return current
    separator = "" if current[-1:].isspace() or addition[:1].isspace() else " "
    return current + separator + addition
