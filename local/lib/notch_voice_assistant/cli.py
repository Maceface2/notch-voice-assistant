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

from .state import (
    CLAUDE_BIN,
    CONFIG_ROOT,
    MODELS_DIR,
    SOCKET_PATH,
    VENV_DIR,
    read_status,
)


SERVICE_NAME = "notch-voice-assistant.service"


def print_waybar_status() -> int:
    status = read_status()
    output = {
        "text": status.get("text", "󰚩"),
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
        "Piper": importlib.util.find_spec("piper") is not None,
        "Piper voice": (MODELS_DIR / "en_US-lessac-medium.onnx").is_file(),
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
            "quit",
        ],
    )
    arguments = parser.parse_args(argv)

    if arguments.command == "status":
        return print_waybar_status()
    if arguments.command == "daemon":
        return daemon()
    if arguments.command == "doctor":
        return doctor()
    return send_command(arguments.command, start_service=arguments.command != "quit")


if __name__ == "__main__":
    raise SystemExit(main())
