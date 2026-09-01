from __future__ import annotations

import asyncio
import audioop
import base64
import binascii
import json
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect as websocket_connect

from app.core.errors import PROVIDER_UNAVAILABLE, REQUEST_INVALID
from app.models import UsageMeasurement
from app.providers.base import (
    MediaResult,
    RealtimeEvent,
    SpeechResult,
    TextResult,
    TranscriptionResult,
)


class OpenAIProvider:
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

    async def probe(self) -> None:
        """Verify endpoint reachability and credentials without sending content."""
        try:
            response = await self._client.get(
                f"{self._base_url}/models",
                headers=self._headers,
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
        payload: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": input_text},
            ],
        }
        if not model.startswith("gpt-5.6"):
            payload["temperature"] = temperature
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        value = await self._post_json("/chat/completions", payload)
        try:
            output = value["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise PROVIDER_UNAVAILABLE from error
        if not isinstance(output, str) or not output.strip():
            raise PROVIDER_UNAVAILABLE
        usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
        return TextResult(
            output=output.strip(),
            usage=UsageMeasurement(
                input_tokens=_optional_int(usage.get("prompt_tokens")),
                output_tokens=_optional_int(usage.get("completion_tokens")),
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
        data: dict[str, str] = {"model": model}
        if source_language != "auto":
            data["language"] = source_language
        if terminology_prompt:
            data["prompt"] = terminology_prompt
        try:
            response = await self._client.post(
                f"{self._base_url}/audio/transcriptions",
                headers=self._headers,
                data=data,
                files={"file": (filename, audio, content_type)},
            )
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise PROVIDER_UNAVAILABLE from error
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            raise PROVIDER_UNAVAILABLE
        text = value["text"].strip()
        if not text:
            raise PROVIDER_UNAVAILABLE
        language = value.get("language")
        return TranscriptionResult(
            text=text,
            detected_language=language if isinstance(language, str) else None,
            usage=UsageMeasurement(),
        )

    async def synthesize(
        self,
        *,
        model: str,
        input_text: str,
        voice: str,
        instructions: str,
    ) -> SpeechResult:
        payload: dict[str, object] = {
            "model": model,
            "input": input_text,
            "voice": voice,
            "response_format": "wav",
        }
        if instructions:
            payload["instructions"] = instructions
        try:
            response = await self._client.post(
                f"{self._base_url}/audio/speech",
                headers=self._headers,
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise PROVIDER_UNAVAILABLE from error
        if not response.content:
            raise PROVIDER_UNAVAILABLE
        return SpeechResult(
            audio=response.content,
            content_type=response.headers.get("content-type", "audio/wav").split(
                ";", 1
            )[0],
            usage=UsageMeasurement(input_tokens=len(input_text)),
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
        if media_kind != "image" or not content_type.startswith("image/"):
            raise PROVIDER_UNAVAILABLE
        encoded = base64.b64encode(media).decode("ascii")
        value = await self._post_json(
            "/responses",
            {
                "model": model,
                "instructions": system_instruction,
                "temperature": temperature,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {
                                "type": "input_image",
                                "image_url": f"data:{content_type};base64,{encoded}",
                            },
                        ],
                    }
                ],
            },
        )
        output = _responses_output_text(value)
        usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
        return MediaResult(
            output=output,
            usage=UsageMeasurement(
                input_tokens=_optional_int(usage.get("input_tokens")),
                output_tokens=_optional_int(usage.get("output_tokens")),
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
    ) -> OpenAIRealtimeSession:
        session = OpenAIRealtimeSession(
            api_key=self._api_key,
            url=_realtime_url(self._base_url, model),
            model=model,
            source_language=source_language,
            terminology_prompt=terminology_prompt,
            terminology_keywords=terminology_keywords,
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
        outputs: frozenset[str] = frozenset({"target_transcript", "translated_audio"}),
    ) -> OpenAIRealtimeTranslationSession:
        if "source_transcript" in outputs:
            raise REQUEST_INVALID
        session = OpenAIRealtimeTranslationSession(
            api_key=self._api_key,
            url=_specialized_realtime_url(
                self._base_url, "/v1/realtime/translations", model
            ),
            target_language=target_language,
            instructions=instructions,
            audio_sample_rate_hz=audio_sample_rate_hz,
            connect=self._connect,
            outputs=outputs,
        )
        await session.start()
        return session

    async def _post_json(
        self, path: str, payload: dict[str, object]
    ) -> dict[str, object]:
        try:
            response = await self._client.post(
                f"{self._base_url}{path}",
                headers=self._headers | {"Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise PROVIDER_UNAVAILABLE from error
        if not isinstance(value, dict):
            raise PROVIDER_UNAVAILABLE
        return value

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Safety-Identifier": "dali-ai-gateway",
        }


class OpenAIRealtimeSession:
    def __init__(
        self,
        *,
        api_key: str,
        url: str,
        model: str,
        source_language: str,
        terminology_prompt: str,
        terminology_keywords: tuple[str, ...],
        audio_sample_rate_hz: int,
        connect: Callable[..., Awaitable[Any]],
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._model = model
        self._source_language = source_language
        self._terminology_prompt = terminology_prompt
        self._terminology_keywords = terminology_keywords
        self._audio_sample_rate_hz = audio_sample_rate_hz
        self._connect = connect
        self._socket: Any | None = None
        self._events: asyncio.Queue[RealtimeEvent] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self._delta_buffers: dict[str, str] = {}

    async def start(self) -> None:
        try:
            self._socket = await self._connect(
                self._url,
                additional_headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "OpenAI-Safety-Identifier": "dali-ai-gateway",
                },
                max_size=2**22,
            )
            transcription: dict[str, object] = {"model": self._model}
            if self._source_language != "auto":
                transcription["languages"] = [self._source_language]
            if self._terminology_prompt:
                transcription["prompt"] = self._terminology_prompt
            if self._terminology_keywords:
                transcription["keywords"] = list(self._terminology_keywords)
            await self._send(
                {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "format": {
                                    "type": "audio/pcm",
                                    "rate": self._audio_sample_rate_hz,
                                },
                                "transcription": transcription,
                                "turn_detection": None,
                            }
                        },
                    },
                }
            )
            self._reader = asyncio.create_task(self._read_events())
        except Exception as error:
            await self.close()
            raise PROVIDER_UNAVAILABLE from error

    async def append(self, audio_base64: str) -> None:
        await self._send({"type": "input_audio_buffer.append", "audio": audio_base64})

    async def commit(self) -> None:
        await self._send({"type": "input_audio_buffer.commit"})

    async def clear(self) -> None:
        await self._send({"type": "input_audio_buffer.clear"})

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
                event_type = value.get("type")
                if event_type == "conversation.item.input_audio_transcription.delta":
                    text = str(value.get("delta") or "")
                    if text:
                        item_id = _optional_str(value.get("item_id")) or ""
                        accumulated = self._delta_buffers.get(item_id, "") + text
                        self._delta_buffers[item_id] = accumulated
                        await self._events.put(
                            RealtimeEvent(
                                "transcript.delta",
                                text=accumulated,
                                item_id=item_id or None,
                            )
                        )
                elif (
                    event_type
                    == "conversation.item.input_audio_transcription.completed"
                ):
                    text = str(value.get("transcript") or "").strip()
                    item_id = _optional_str(value.get("item_id")) or ""
                    self._delta_buffers.pop(item_id, None)
                    if text:
                        await self._events.put(
                            RealtimeEvent(
                                "transcript.final",
                                text=text,
                                item_id=item_id or None,
                            )
                        )
                elif event_type == "error":
                    await self._events.put(
                        RealtimeEvent("error", code="provider_realtime_error")
                    )
                    return
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


class OpenAIRealtimeTranslationSession:
    def __init__(
        self,
        *,
        api_key: str,
        url: str,
        target_language: str,
        instructions: str,
        audio_sample_rate_hz: int,
        connect: Callable[..., Awaitable[Any]],
        outputs: frozenset[str],
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._target_language = _translation_language(target_language)
        self._instructions = instructions
        self._audio_sample_rate_hz = audio_sample_rate_hz
        self._connect = connect
        self._socket: Any | None = None
        self._events: asyncio.Queue[RealtimeEvent] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self._rate_state: tuple | None = None
        self._outputs = outputs

    async def start(self) -> None:
        try:
            self._socket = await self._connect(
                self._url,
                additional_headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "OpenAI-Safety-Identifier": "dali-ai-gateway",
                },
                max_size=2**22,
            )
            session: dict[str, object] = {
                "audio": {"output": {"language": self._target_language}}
            }
            # The dedicated GPT-Realtime-Translate endpoint does not accept
            # `session.instructions`; translation behavior is defined by the
            # selected target language and the model.  Keep the product-level
            # instruction in the provider-neutral interface, but do not send
            # an unsupported parameter to OpenAI.
            await self._send({"type": "session.update", "session": session})
            self._reader = asyncio.create_task(self._read_events())
        except Exception as error:
            await self.close()
            raise PROVIDER_UNAVAILABLE from error

    async def append(self, audio_base64: str) -> None:
        try:
            pcm = base64.b64decode(audio_base64, validate=True)
            if len(pcm) % 2:
                raise ValueError("PCM16 audio must contain complete samples")
            if self._audio_sample_rate_hz != 24000:
                pcm, self._rate_state = audioop.ratecv(
                    pcm,
                    2,
                    1,
                    self._audio_sample_rate_hz,
                    24000,
                    self._rate_state,
                )
            audio_base64 = base64.b64encode(pcm).decode("ascii")
        except (binascii.Error, ValueError) as error:
            raise PROVIDER_UNAVAILABLE from error
        await self._send(
            {"type": "session.input_audio_buffer.append", "audio": audio_base64}
        )

    async def commit(self) -> None:
        return None

    async def clear(self) -> None:
        self._rate_state = None
        await self._send({"type": "session.input_audio_buffer.clear"})

    async def next_event(self) -> RealtimeEvent:
        return await self._events.get()

    async def close(self) -> None:
        reader = self._reader
        self._reader = None
        if self._socket is not None:
            try:
                await self._send({"type": "session.close"})
            except Exception:
                pass
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
                event = _translation_event(value)
                if event is not None and self._selected(event):
                    await self._events.put(event)
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

    def _selected(self, event: RealtimeEvent) -> bool:
        if event.type.startswith("translation.audio."):
            return "translated_audio" in self._outputs
        if event.type.startswith("translation."):
            return "target_transcript" in self._outputs
        return True


def _realtime_url(base_url: str, model: str) -> str:
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/")
    if not path.endswith("/realtime"):
        path = f"{path}/realtime"
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["intent"] = "transcription"
    query["model"] = model
    return urlunsplit((scheme, parsed.netloc, path, urlencode(query), ""))


def _specialized_realtime_url(base_url: str, endpoint: str, model: str) -> str:
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/")
    if path.endswith("/v1") and endpoint.startswith("/v1/"):
        path = f"{path}{endpoint.removeprefix('/v1')}"
    else:
        path = f"{path}{endpoint}"
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["model"] = model
    return urlunsplit((scheme, parsed.netloc, path, urlencode(query), ""))


def _translation_language(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized.startswith("zh"):
        return "zh"
    return normalized.split("-", 1)[0]


def _translation_event(value: dict[str, object]) -> RealtimeEvent | None:
    event_type = str(value.get("type") or "")
    if event_type in {
        "session.output_audio.delta",
        "response.output_audio.delta",
    }:
        audio = str(value.get("delta") or value.get("audio") or "")
        return (
            RealtimeEvent(
                "translation.audio.delta",
                audio=audio,
                item_id=_optional_str(value.get("item_id") or value.get("response_id")),
                content_type="audio/pcm",
                sample_rate_hz=24000,
                channels=1,
            )
            if audio
            else None
        )
    if event_type in {
        "session.output_audio.done",
        "response.output_audio.done",
    }:
        return RealtimeEvent(
            "translation.audio.final",
            item_id=_optional_str(value.get("item_id") or value.get("response_id")),
            content_type="audio/pcm",
            sample_rate_hz=24000,
            channels=1,
        )
    delta_types = {
        "session.output_transcript.delta",
        "response.output_audio_transcript.delta",
        "response.output_text.delta",
    }
    final_types = {
        "session.output_transcript.completed",
        "session.output_transcript.done",
        "session.output_transcript.final",
        "response.output_audio_transcript.done",
        "response.output_text.done",
    }
    if event_type in delta_types:
        text = str(value.get("delta") or "")
        return (
            RealtimeEvent(
                "translation.delta",
                text=text,
                item_id=_optional_str(value.get("item_id") or value.get("response_id")),
            )
            if text
            else None
        )
    if event_type in final_types:
        text = str(
            value.get("transcript") or value.get("text") or value.get("delta") or ""
        ).strip()
        return (
            RealtimeEvent(
                "translation.final",
                text=text,
                item_id=_optional_str(value.get("item_id") or value.get("response_id")),
            )
            if text
            else None
        )
    if event_type == "error":
        return RealtimeEvent("error", code="provider_realtime_error")
    return None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _responses_output_text(value: dict[str, object]) -> str:
    output = value.get("output")
    if not isinstance(output, list):
        raise PROVIDER_UNAVAILABLE
    text = "".join(
        str(part.get("text") or "")
        for item in output
        if isinstance(item, dict)
        for part in (
            item.get("content") if isinstance(item.get("content"), list) else []
        )
        if isinstance(part, dict) and part.get("type") == "output_text"
    ).strip()
    if not text:
        raise PROVIDER_UNAVAILABLE
    return text
