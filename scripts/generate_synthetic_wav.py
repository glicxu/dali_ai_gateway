"""Generate a short, content-free PCM fixture for realtime smoke tests."""

from __future__ import annotations

import argparse
import math
import wave
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()
    if not 0.1 <= args.seconds <= 10:
        raise SystemExit("seconds must be between 0.1 and 10")
    frames = int(16_000 * args.seconds)
    with wave.open(str(args.output), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(
            b"".join(
                int(8_000 * math.sin(2 * math.pi * 440 * index / 16_000)).to_bytes(
                    2, "little", signed=True
                )
                for index in range(frames)
            )
        )


if __name__ == "__main__":
    main()
