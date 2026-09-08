import asyncio
import io
import struct
import wave

import httpx
import pytest

from app.providers.audio import finalize_pcm_wav
from app.providers.openai import OpenAIProvider


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x12\x01" * 12000)
    return output.getvalue()


def streaming_wav():
    audio = bytearray(wav_bytes())
    struct.pack_into("<I", audio, 4, 0xFFFFFFFF)
    struct.pack_into("<I", audio, 40, 0xFFFFFFFF)
    return bytes(audio)


def test_streaming_wav_is_finalized_without_changing_samples():
    completed = finalize_pcm_wav(streaming_wav())
    assert completed == wav_bytes()
    assert struct.unpack_from("<I", completed, 4)[0] == len(completed) - 8
    with wave.open(io.BytesIO(completed), "rb") as wav:
        assert wav.getnframes() / wav.getframerate() == 0.5
        assert wav.readframes(wav.getnframes()) == b"\x12\x01" * 12000


def test_partial_pcm_frame_is_rejected():
    with pytest.raises(ValueError):
        finalize_pcm_wav(streaming_wav()[:-1])


def test_other_formats_are_unchanged():
    assert finalize_pcm_wav(b"ID3-other-format") == b"ID3-other-format"


def test_openai_speech_returns_a_complete_wav_container():
    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, content=streaming_wav(), headers={"content-type": "audio/wav"}
                )
            )
        ) as client:
            provider = OpenAIProvider(
                api_key="test-key",
                base_url="https://example.test/v1",
                timeout_seconds=5,
                client=client,
            )
            result = await provider.synthesize(
                model="gpt-4o-mini-tts",
                input_text="Test.",
                voice="alloy",
                instructions="",
            )
            assert result.audio == wav_bytes()
            assert result.usage.input_tokens is None

    asyncio.run(exercise())
