from __future__ import annotations

import io
import wave


def finalize_pcm_wav(audio: bytes) -> bytes:
    """Finalize a completed PCM WAV response, including streaming length headers.

    Some providers leave RIFF/data sizes at 0xffffffff even after the HTTP body
    has completed. Android MediaPlayer cannot reliably prepare such a source.
    Rebuild the container in memory using the actual frames; do not transcode
    samples or retain any content. Other response formats pass through.
    """
    if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio
    with wave.open(io.BytesIO(audio), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if not frames or len(frames) % (channels * sample_width):
        raise ValueError("Invalid PCM frame boundary")
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(sample_width)
        target.setframerate(sample_rate)
        target.writeframes(frames)
    return output.getvalue()
