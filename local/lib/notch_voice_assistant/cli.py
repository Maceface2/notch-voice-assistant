from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .fish_s2 import REFERENCE_TEXT
from .state import (
    CLAUDE_BIN,
    CONFIG_ROOT,
    FISH_S2_BINARY,
    FISH_S2_MODEL_PATH,
    FISH_S2_READY_PATH,
    FISH_S2_REFERENCE_PATH,
    FISH_S2_TOKENIZER_PATH,
    FISH_S2_VOICE_DIR,
    FISH_S2_VOICE_PATH,
    MODELS_DIR,
    SOCKET_PATH,
    VENV_DIR,
    read_status,
)


SERVICE_NAME = "notch-voice-assistant.service"


def print_waybar_status() -> int:
    status = read_status()
    output = {
        "text": status.get("text", "✳"),
        "class": status.get("class", ["idle", "closed"]),
        "tooltip": status.get("tooltip", "Claude voice assistant\nClick to open"),
    }
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    return 0


def send_command(command: str, *, start_service: bool = True) -> int:
    if not SOCKET_PATH.exists() and start_service:
        result = subprocess.run(
            ["systemctl", "--user", "start", SERVICE_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode:
            print(result.stderr.strip() or "Could not start the voice assistant service.", file=sys.stderr)
            return result.returncode
        deadline = time.monotonic() + 4
        while not SOCKET_PATH.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(SOCKET_PATH))
            client.sendall(json.dumps({"command": command}).encode("utf-8"))
            response = client.recv(1024)
    except OSError as error:
        print(f"Voice assistant is unavailable: {error}", file=sys.stderr)
        return 1
    return 0 if b'"ok":true' in response else 1


def doctor() -> int:
    checks = {
        "Claude Code": CLAUDE_BIN.is_file() and os.access(CLAUDE_BIN, os.X_OK),
        "GTK layer shell": _gi_available("GtkLayerShell", "0.1"),
        "GStreamer": _gi_available("Gst", "1.0"),
        "faster-whisper": importlib.util.find_spec("faster_whisper") is not None,
        "WebRTC VAD": importlib.util.find_spec("webrtcvad") is not None,
        "Fish Audio S2 Pro runtime": (
            FISH_S2_BINARY.is_file() and os.access(FISH_S2_BINARY, os.X_OK)
        ),
        "Fish Audio S2 Pro Q6_K": FISH_S2_MODEL_PATH.is_file(),
        "Fish S2 voice profile": (
            FISH_S2_READY_PATH.is_file() and FISH_S2_VOICE_PATH.is_file()
        ),
        "aplay": _command_exists("aplay"),
        "eSpeak fallback": _command_exists("espeak-ng"),
        "User service": (CONFIG_ROOT / "systemd/user" / SERVICE_NAME).is_file(),
    }
    for label, available in checks.items():
        print(f"{'OK' if available else 'MISSING':7} {label}")
    return 0 if all(checks.values()) else 1


def _gi_available(namespace: str, version: str) -> bool:
    try:
        import gi

        gi.require_version(namespace, version)
        return True
    except (ImportError, ValueError):
        return False


def _command_exists(command: str) -> bool:
    return any(
        (Path(directory) / command).is_file()
        for directory in os.environ.get("PATH", "").split(os.pathsep)
    )


def set_voice(audio_path: str, transcript: str) -> int:
    reference = Path(audio_path).expanduser().resolve()
    transcript = transcript.strip()
    if not reference.is_file():
        print(f"Voice reference does not exist: {reference}", file=sys.stderr)
        return 2
    if not transcript:
        print("The exact transcript of the voice reference is required.", file=sys.stderr)
        return 2
    for required in (FISH_S2_BINARY, FISH_S2_MODEL_PATH, FISH_S2_TOKENIZER_PATH):
        if not required.is_file():
            print(f"Fish Audio S2 Pro is missing: {required}", file=sys.stderr)
            return 1

    backend = os.environ.get("NOTCH_VOICE_FISH_S2_BACKEND", "vulkan").lower()
    if backend not in {"vulkan", "cuda", "cpu"}:
        print(
            "NOTCH_VOICE_FISH_S2_BACKEND must be vulkan, cuda, or cpu.",
            file=sys.stderr,
        )
        return 2

    subprocess.run(
        ["systemctl", "--user", "stop", SERVICE_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    FISH_S2_VOICE_DIR.mkdir(parents=True, exist_ok=True)
    pending_id = "notch-voice-pending"
    pending_path = FISH_S2_VOICE_DIR / f"{pending_id}.s2voice"
    pending_path.unlink(missing_ok=True)
    preview_path = FISH_S2_VOICE_DIR / "voice-preview.wav"
    command = [
        str(FISH_S2_BINARY),
        "--model",
        str(FISH_S2_MODEL_PATH),
        "--tokenizer",
        str(FISH_S2_TOKENIZER_PATH),
        "--prompt-audio",
        str(reference),
        "--prompt-text",
        transcript,
        "--voice",
        pending_id,
        "--voice-dir",
        str(FISH_S2_VOICE_DIR),
        "--save-voice",
        "--text",
        "Your new voice is ready.",
        "--output",
        str(preview_path),
        "--max-tokens",
        "128",
        "--log-level",
        "warn",
    ]
    if backend == "vulkan":
        command.extend(["--vulkan", "0"])
    elif backend == "cuda":
        command.extend(["--cuda", "0"])

    result = subprocess.run(command)
    if result.returncode or not pending_path.is_file():
        print("Fish Audio could not create the voice profile.", file=sys.stderr)
        return result.returncode or 1
    os.replace(pending_path, FISH_S2_VOICE_PATH)
    print(f"Voice updated. Preview: {preview_path}")
    return 0


def daemon() -> int:
    from .app import VoiceAssistantApplication

    return VoiceAssistantApplication().run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claude voice assistant for the Waybar notch")
    parser.add_argument(
        "command",
        nargs="?",
        default="toggle",
        choices=[
            "toggle",
            "open",
            "stop",
            "new-session",
            "status",
            "daemon",
            "doctor",
            "set-voice",
            "reset-voice",
            "quit",
        ],
    )
    parser.add_argument("voice_audio", nargs="?")
    parser.add_argument("voice_transcript", nargs="?")
    arguments = parser.parse_args(argv)

    if arguments.command == "status":
        return print_waybar_status()
    if arguments.command == "daemon":
        return daemon()
    if arguments.command == "doctor":
        return doctor()
    if arguments.command == "set-voice":
        if not arguments.voice_audio or not arguments.voice_transcript:
            parser.error('set-voice requires <audio> "<exact transcript>"')
        return set_voice(arguments.voice_audio, arguments.voice_transcript)
    if arguments.command == "reset-voice":
        return set_voice(str(FISH_S2_REFERENCE_PATH), REFERENCE_TEXT)
    return send_command(arguments.command, start_service=arguments.command != "quit")


if __name__ == "__main__":
    raise SystemExit(main())
