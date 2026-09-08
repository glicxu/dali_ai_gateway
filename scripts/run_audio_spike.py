"""Run isolated loopback Gateway + existing Chat backend; no deployment changes."""

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider", choices=["fake", "openai", "gemini"], default="fake"
    )
    parser.add_argument(
        "--chat-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "mobile_app/dali_chat",
    )
    parser.add_argument(
        "--check", action="store_true", help="Exercise both services once, then stop"
    )
    parser.add_argument("--gateway-port", type=int, default=15040)
    parser.add_argument("--chat-port", type=int, default=15050)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    chat_server = args.chat_root.resolve() / "server"
    chat_python = chat_server / (
        ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    )
    if not chat_python.is_file():
        parser.error(
            "Create the Chat server virtual environment first; see its README."
        )
    # Only explicitly selected provider credentials cross into this isolated demo.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("AI_GATEWAY_", "DALI_CHAT_", "DALI_AUDIO_SPIKE_"))
    }
    if args.provider != "fake":
        key = f"AI_GATEWAY_{args.provider.upper()}_API_KEY"
        if not os.environ.get(key):
            parser.error(
                f"Set {key} from your environment-managed secret before a live trial."
            )
        env[key] = os.environ[key]
    token = secrets.token_urlsafe(32)
    profile = f"dali_chat.speech.{'gemini' if args.provider == 'gemini' else 'openai'}"
    env.update(
        {
            "DALI_AUDIO_SPIKE_PROVIDER": args.provider,
            "DALI_AUDIO_SPIKE_SERVICE_TOKEN": token,
            "DALI_CHAT_GATEWAY_SERVICE_TOKEN": token,
            "DALI_CHAT_GATEWAY_URL": f"http://127.0.0.1:{args.gateway_port}",
            "DALI_CHAT_ENABLED_PROFILES_JSON": json.dumps([profile]),
            "DALI_CHAT_ALLOWED_ORIGINS_JSON": '["http://localhost:18080","http://127.0.0.1:18080"]',
        }
    )
    processes = []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "scripts.audio_spike_gateway:app_factory",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.gateway_port),
                    "--no-access-log",
                    "--log-level",
                    "error",
                ],
                cwd=root,
                env=env,
                creationflags=flags,
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    str(chat_python),
                    "-c",
                    "import uvicorn; from app.main import create_app; from app.config import Settings; "
                    f"uvicorn.run(create_app(Settings(_env_file=None)), host='127.0.0.1', port={args.chat_port}, access_log=False, log_level='error')",
                ],
                cwd=chat_server,
                env=env,
                creationflags=flags,
            )
        )
        base = f"http://127.0.0.1:{args.chat_port}"
        with httpx.Client(timeout=120) as client:
            ready = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("A local spike service could not start.")
                try:
                    response = client.get(f"{base}/v1/capabilities", timeout=2)
                    if response.status_code == 200:
                        ready = next(
                            (
                                item
                                for item in response.json()
                                if item["profile"] == profile and item["available"]
                            ),
                            None,
                        )
                        if ready:
                            break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            if not ready:
                raise RuntimeError("Speech capability discovery did not become ready.")
            if args.check:
                started = time.monotonic()
                result = client.post(
                    f"{base}/v1/audio/speech",
                    json={
                        "profile": profile,
                        "voice": "narrator_main",
                        "configuration_id": ready["configuration_id"],
                        "input": "Welcome to the Dali Audio narration trial.",
                        "instructions": "Read faithfully and clearly.",
                    },
                )
                if result.status_code != 200 or not result.content.startswith(b"RIFF"):
                    raise RuntimeError("The end-to-end speech trial failed.")
                print(
                    f"PASS: Chat -> Gateway -> {args.provider}; WAV received, "
                    f"{len(result.content)} bytes, {time.monotonic() - started:.2f}s. No audio saved."
                )
                return
        print(f"Audio spike ready at {base}; provider={args.provider}.", flush=True)
        if args.provider == "fake":
            print(
                "Synthetic tone only: validates transport/playback, not narration quality.",
                flush=True,
            )
        print(
            "Run the Chat audio_spike_main.dart entrypoint. Ctrl+C stops both services.",
            flush=True,
        )
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
