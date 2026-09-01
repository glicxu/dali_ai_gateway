from __future__ import annotations

import asyncio
import audioop
import base64
import binascii
import io
import json
import wave
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect as websocket_connect

from app.core.errors import PROVIDER_UNAVAILABLE
from app.models import UsageMeasurement
from app.providers.base import (
    MediaResult,
    RealtimeEvent,
    SpeechResult,
    TextResult,
    TranscriptionResult,
)


class GeminiProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        realtime_session_max_seconds: float = 9 * 60,
        client: httpx.AsyncClient | None = None,
        connect: Callable[..., Awaitable[Any]] = websocket_connect,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._connect = connect
        self._realtime_session_max_seconds = realtime_session_max_seconds

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def probe(self) -> None:
        """Verify endpoint reachability and credentials without sending content."""
        try:
            response = await self._client.get(
                f"{self._base_url}/models",
                headers={"x-goog-api-key": self._api_key},
                params={"pageSize": "1"},
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise PROVIDER_UNAVAILABLE from error

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
        if model == "gemini-3.5-transcribe":
            return await self._transcribe_interaction(
                model=model,
                audio=audio,
                content_type=content_type,
                source_language=source_language,
            )
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
            if not isinstance(value, dict):
                raise ValueError("Gemini returned an invalid response.")
            output = _batch_transcription_text(value)
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise PROVIDER_UNAVAILABLE from error
        usage = value.get("usageMetadata", {})
        if not isinstance(usage, dict):
            usage = {}
        return TranscriptionResult(
            text=output,
            detected_language=None,
            usage=UsageMeasurement(
                input_tokens=_int(usage.get("promptTokenCount")),
                output_tokens=_int(usage.get("candidatesTokenCount")),
            ),
        )

    async def _transcribe_interaction(
        self,
        *,
        model: str,
        audio: bytes,
        content_type: str,
        source_language: str,
    ) -> TranscriptionResult:
        transcription_config: dict[str, object] = {"mode": "verbatim"}
        if source_language != "auto":
            transcription_config["language_codes"] = [source_language]
        try:
            response = await self._client.post(
                f"{self._base_url}/interactions",
                headers={
                    "x-goog-api-key": self._api_key,
                    "Api-Revision": "2026-05-20",
                },
                json={
                    "model": model,
                    "input": [
                        {
                            "type": "audio",
                            "data": base64.b64encode(audio).decode("ascii"),
                            "mime_type": content_type,
                        }
                    ],
                    "generation_config": {"transcription_config": transcription_config},
                },
            )
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError("Gemini returned an invalid interaction.")
            output = _interaction_text(value)
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise PROVIDER_UNAVAILABLE from error
        return TranscriptionResult(
            text=output,
            detected_language=_interaction_language(value),
            usage=_interaction_usage(value),
        )

    async def synthesize(
        self,
        *,
        model: str,
        input_text: str,
        voice: str,
        instructions: str,
    ) -> SpeechResult:
        spoken_input = (
            f"{instructions.strip()}\n\nText to speak:\n{input_text}"
            if instructions.strip()
            else input_text
        )
        try:
            response = await self._client.post(
                f"{self._base_url}/interactions",
                headers={
                    "x-goog-api-key": self._api_key,
                    "Api-Revision": "2026-05-20",
                },
                json={
                    "model": model,
                    "input": spoken_input,
                    "response_format": {
                        "type": "audio",
                    },
                    "generation_config": {"speech_config": [{"voice": voice}]},
                },
            )
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError("Gemini returned an invalid interaction.")
            output_audio, content_type = _interaction_audio(value)
        except (httpx.HTTPError, ValueError, TypeError, binascii.Error) as error:
            raise PROVIDER_UNAVAILABLE from error
        return SpeechResult(
            audio=output_audio,
            content_type=content_type,
            usage=_interaction_usage(value),
        )

    async def analyze_media(
        self,
        *,
        model: str,
        system_instruction: str,
        prompt: str,
        media: bytes,
        content_type: str,
        media_kind: str,
        temperature: float,
    ) -> MediaResult:
        if media_kind not in {"image", "video"} or not content_type.startswith(
            f"{media_kind}/"
        ):
            raise PROVIDER_UNAVAILABLE
        try:
            response = await self._client.post(
                f"{self._base_url}/models/{model}:generateContent",
                headers={"x-goog-api-key": self._api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system_instruction}]},
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {"text": prompt},
                                {
                                    "inlineData": {
                                        "mimeType": content_type,
                                        "data": base64.b64encode(media).decode("ascii"),
                                    }
                                },
                            ],
                        }
                    ],
                    "generationConfig": {"temperature": temperature},
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
        if not isinstance(usage, dict):
            usage = {}
        return MediaResult(
            output=output.strip(),
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
            max_duration_seconds=self._realtime_session_max_seconds,
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
        outputs: frozenset[str] = frozenset({"target_transcript", "translated_audio"}),
    ) -> GeminiRealtimeSession:
        del instructions
        setup_config: dict[str, object] = {
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "translationConfig": {
                    "targetLanguageCode": target_language,
                    "echoTargetLanguage": True,
                },
            }
        }
        if "source_transcript" in outputs:
            setup_config["inputAudioTranscription"] = {}
        if "target_transcript" in outputs:
            setup_config["outputAudioTranscription"] = {}
        session = GeminiRealtimeSession(
            url=_live_url(self._base_url, self._api_key),
            model=model,
            mode="translation",
            setup_config=setup_config,
            audio_sample_rate_hz=audio_sample_rate_hz,
            connect=self._connect,
            max_duration_seconds=self._realtime_session_max_seconds,
            outputs=outputs,
        )
        await session.start()
        return session


class GeminiRealtimeSession:
    """Normalized Gemini Live transcription or native audio translation session."""

    def __init__(
        self,
        *,
        url: str,
        model: str,
        mode: Literal["transcription", "translation"],
        setup_config: dict[str, object],
        audio_sample_rate_hz: int,
        connect: Callable[..., Awaitable[Any]],
        max_duration_seconds: float,
        outputs: frozenset[str] = frozenset(),
    ) -> None:
        self._url = url
        self._model = model
        self._mode = mode
        self._setup_config = setup_config
        self._audio_sample_rate_hz = audio_sample_rate_hz
        self._connect = connect
        self._max_duration_seconds = max_duration_seconds
        self._socket: Any | None = None
        self._events: asyncio.Queue[RealtimeEvent] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self._deadline: asyncio.Task[None] | None = None
        self._rate_state: tuple | None = None
        self._translation = ""
        self._source_transcript = ""
        self._item_number = 1
        self._outputs = outputs

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
            self._deadline = asyncio.create_task(self._enforce_deadline())
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
        self._source_transcript = ""

    async def next_event(self) -> RealtimeEvent:
        return await self._events.get()

    async def close(self) -> None:
        deadline = self._deadline
        self._deadline = None
        if deadline is not None and deadline is not asyncio.current_task():
            deadline.cancel()
            await asyncio.gather(deadline, return_exceptions=True)
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
        self._source_transcript = ""

    async def _enforce_deadline(self) -> None:
        try:
            await asyncio.sleep(self._max_duration_seconds)
            if self._socket is not None:
                await self._events.put(
                    RealtimeEvent("error", code="provider_session_rotation_required")
                )
        except asyncio.CancelledError:
            raise

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
                    return
                content = value.get("serverContent")
                if not isinstance(content, dict):
                    continue
                if self._mode == "transcription":
                    await self._read_transcription(content)
                else:
                    await self._read_translation(content)
            if self._socket is not None:
                await self._events.put(
                    RealtimeEvent("error", code="provider_connection_closed")
                )
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
        interim_source = _transcription_text(content.get("interimInputTranscription"))
        if interim_source and "source_transcript" in self._outputs:
            await self._events.put(
                RealtimeEvent(
                    "transcript.delta",
                    text=interim_source,
                    item_id=self._item_id,
                )
            )
        source = _transcription_text(content.get("inputTranscription"))
        if source and "source_transcript" in self._outputs:
            self._source_transcript = _merge_text(self._source_transcript, source)
            await self._events.put(
                RealtimeEvent(
                    "transcript.delta",
                    text=source,
                    item_id=self._item_id,
                )
            )
        model_turn = content.get("modelTurn")
        if "translated_audio" in self._outputs and isinstance(model_turn, dict):
            parts = model_turn.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    inline = part.get("inlineData")
                    if not isinstance(inline, dict):
                        continue
                    audio = inline.get("data")
                    mime_type = inline.get("mimeType")
                    if isinstance(audio, str) and audio:
                        await self._events.put(
                            RealtimeEvent(
                                "translation.audio.delta",
                                audio=audio,
                                item_id=self._item_id,
                                content_type=(
                                    mime_type
                                    if isinstance(mime_type, str)
                                    else "audio/pcm;rate=24000"
                                ),
                                sample_rate_hz=24000,
                                channels=1,
                            )
                        )
        translated = _transcription_text(content.get("outputTranscription"))
        if translated and "target_transcript" in self._outputs:
            self._translation = _merge_text(self._translation, translated)
            await self._events.put(
                RealtimeEvent(
                    "translation.delta",
                    text=translated,
                    item_id=self._item_id,
                )
            )
        if content.get("turnComplete") is True and self._source_transcript.strip():
            await self._events.put(
                RealtimeEvent(
                    "transcript.final",
                    text=self._source_transcript.strip(),
                    item_id=self._item_id,
                )
            )
            self._source_transcript = ""
        if content.get("turnComplete") is True and self._translation.strip():
            await self._events.put(
                RealtimeEvent(
                    "translation.final",
                    text=self._translation.strip(),
                    item_id=self._item_id,
                )
            )
            self._translation = ""
        if content.get("turnComplete") is True and "translated_audio" in self._outputs:
            await self._events.put(
                RealtimeEvent(
                    "translation.audio.final",
                    item_id=self._item_id,
                    content_type="audio/pcm;rate=24000",
                    sample_rate_hz=24000,
                    channels=1,
                )
            )
        if content.get("turnComplete") is True:
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


def _batch_transcription_text(value: dict[str, object]) -> str:
    prompt_feedback = value.get("promptFeedback")
    if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
        raise ValueError("Gemini blocked the transcription response.")
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return "<no audio>"
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        return "<no audio>"
    if candidate.get("finishReason") in {
        "SAFETY",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "RECITATION",
    }:
        raise ValueError("Gemini blocked the transcription response.")
    content = candidate.get("content")
    if not isinstance(content, dict):
        return "<no audio>"
    parts = content.get("parts")
    if not isinstance(parts, list):
        return "<no audio>"
    text = " ".join(
        part["text"].strip()
        for part in parts
        if isinstance(part, dict)
        and isinstance(part.get("text"), str)
        and part["text"].strip()
    )
    return text or "<no audio>"


def _interaction_text(value: dict[str, object]) -> str:
    direct = value.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for item in _walk_dicts(value):
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            text = str(item["text"]).strip()
            if text:
                return text
    raise ValueError("Gemini interaction did not return text.")


def _interaction_audio(value: dict[str, object]) -> tuple[bytes, str]:
    candidates: list[dict[str, object]] = []
    direct = value.get("output_audio")
    if isinstance(direct, dict):
        candidates.append(direct)
    candidates.extend(
        item for item in _walk_dicts(value) if item.get("type") == "audio"
    )
    for item in candidates:
        encoded = item.get("data")
        if not isinstance(encoded, str) or not encoded:
            continue
        content_type = str(item.get("mime_type") or "audio/wav")
        audio = base64.b64decode(encoded, validate=True)
        if audio:
            if content_type.split(";", 1)[0].strip().lower() == "audio/l16":
                audio = _pcm_to_wav(
                    audio,
                    sample_rate=_int(item.get("sample_rate")) or 24_000,
                    channels=_int(item.get("channels")) or 1,
                )
                content_type = "audio/wav"
            return audio, content_type
    raise ValueError("Gemini interaction did not return audio.")


def _interaction_language(value: dict[str, object]) -> str | None:
    for item in _walk_dicts(value):
        language = item.get("language_code") or item.get("language")
        if isinstance(language, str) and language:
            return language
    return None


def _interaction_usage(value: dict[str, object]) -> UsageMeasurement:
    usage = value.get("usage")
    if not isinstance(usage, dict):
        usage = value.get("usage_metadata")
    if not isinstance(usage, dict):
        return UsageMeasurement()
    return UsageMeasurement(
        input_tokens=_int(
            usage.get("input_tokens")
            or usage.get("prompt_token_count")
            or usage.get("promptTokenCount")
        ),
        output_tokens=_int(
            usage.get("output_tokens")
            or usage.get("candidates_token_count")
            or usage.get("candidatesTokenCount")
        ),
    )


def _walk_dicts(value: object):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(item)


def _pcm_to_wav(audio: bytes, *, sample_rate: int, channels: int) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(audio)
    return target.getvalue()


def _merge_text(current: str, addition: str) -> str:
    if not current:
        return addition
    if addition.startswith(current):
        return addition
    if current.endswith(addition):
        return current
    separator = "" if current[-1:].isspace() or addition[:1].isspace() else " "
    return current + separator + addition
