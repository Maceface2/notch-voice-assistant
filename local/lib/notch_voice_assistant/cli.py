from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .elevenlabs import (
    DEFAULT_MODEL_ID,
    MAXIMUM_SPEED,
    MINIMUM_SPEED,
    SUPPORTED_MODELS,
    ElevenLabsSettings,
    ElevenLabsSynthesizer,
    ElevenLabsUnavailable,
)
from .state import (
    CLAUDE_BIN,
    CONFIG_ROOT,
    ELEVENLABS_CONFIG_PATH,
    SOCKET_PATH,
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
    elevenlabs_settings = ElevenLabsSettings.load()
    checks = {
        "Claude Code": CLAUDE_BIN.is_file() and os.access(CLAUDE_BIN, os.X_OK),
        "GTK layer shell": _gi_available("GtkLayerShell", "0.1"),
        "GStreamer": _gi_available("Gst", "1.0"),
        "faster-whisper": importlib.util.find_spec("faster_whisper") is not None,
        "WebRTC VAD": importlib.util.find_spec("webrtcvad") is not None,
        "ElevenLabs API key": bool(elevenlabs_settings.api_key),
        "ElevenLabs voice": bool(elevenlabs_settings.voice_id),
        "ffplay": _command_exists("ffplay"),
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


def configure_elevenlabs() -> int:
    current = ElevenLabsSettings.load()
    prompt = "ElevenLabs API key"
    if current.api_key:
        prompt += " (Enter keeps the saved key)"
    api_key = getpass.getpass(f"{prompt}: ").strip() or current.api_key
    if not api_key:
        print("An ElevenLabs API key is required.", file=sys.stderr)
        return 2
    try:
        voices = ElevenLabsSynthesizer.list_voices(api_key)
    except ElevenLabsUnavailable as error:
        print(error, file=sys.stderr)
        return 1

    _print_voices(voices)
    available_ids = {
        str(voice.get("voice_id", "")).strip()
        for voice in voices
        if voice.get("voice_id")
    }
    recommended = _recommended_voice_id(voices, current.voice_id)
    voice_id = input(f"Voice ID [{recommended}]: ").strip() or recommended
    if available_ids and voice_id not in available_ids:
        print(
            "That voice ID was not returned for this ElevenLabs account.",
            file=sys.stderr,
        )
        return 2

    ElevenLabsSettings(
        api_key=api_key,
        voice_id=voice_id,
        model_id=DEFAULT_MODEL_ID,
        speed=current.speed,
        stability=current.stability,
    ).save()
    print(f"Saved ElevenLabs configuration to {ELEVENLABS_CONFIG_PATH}.")
    return 0


def list_voices() -> int:
    settings = ElevenLabsSettings.load()
    if not settings.api_key:
        print(
            "Run `notch-voice-assistant configure-elevenlabs` first.",
            file=sys.stderr,
        )
        return 2
    try:
        voices = ElevenLabsSynthesizer.list_voices(settings.api_key)
    except ElevenLabsUnavailable as error:
        print(error, file=sys.stderr)
        return 1
    _print_voices(voices)
    return 0


def set_voice(voice_id: str) -> int:
    voice_id = voice_id.strip()
    if not voice_id or not voice_id.replace("-", "").replace("_", "").isalnum():
        print("Provide a valid ElevenLabs voice ID.", file=sys.stderr)
        return 2
    settings = ElevenLabsSettings.load()
    settings = ElevenLabsSettings(
        api_key=settings.api_key,
        voice_id=voice_id,
        model_id=settings.model_id,
        speed=settings.speed,
        stability=settings.stability,
    )
    settings.save()
    print(f"ElevenLabs voice set to {voice_id}.")
    return 0


def set_speed(raw_speed: str) -> int:
    try:
        speed = float(raw_speed)
    except ValueError:
        print("Speaking speed must be a number from 0.7 to 1.2.", file=sys.stderr)
        return 2
    if not MINIMUM_SPEED <= speed <= MAXIMUM_SPEED:
        print("Speaking speed must be from 0.7 to 1.2.", file=sys.stderr)
        return 2
    settings = ElevenLabsSettings.load()
    ElevenLabsSettings(
        api_key=settings.api_key,
        voice_id=settings.voice_id,
        model_id=settings.model_id,
        speed=speed,
        stability=settings.stability,
    ).save()
    print(f"ElevenLabs speaking speed set to {speed:.2f}x.")
    return 0


def set_tts_model(model_id: str) -> int:
    model_id = model_id.strip()
    if model_id not in SUPPORTED_MODELS:
        print(
            "TTS model must be eleven_turbo_v2_5, eleven_flash_v2_5, "
            "or eleven_multilingual_v2.",
            file=sys.stderr,
        )
        return 2
    settings = ElevenLabsSettings.load()
    ElevenLabsSettings(
        api_key=settings.api_key,
        voice_id=settings.voice_id,
        model_id=model_id,
        speed=settings.speed,
        stability=settings.stability,
    ).save()
    print(f"ElevenLabs TTS model set to {model_id}.")
    return 0


def set_stability(raw_stability: str) -> int:
    try:
        stability = float(raw_stability)
    except ValueError:
        print("Stability must be a number from 0 to 1.", file=sys.stderr)
        return 2
    if not 0.0 <= stability <= 1.0:
        print("Stability must be from 0 to 1.", file=sys.stderr)
        return 2
    settings = ElevenLabsSettings.load()
    ElevenLabsSettings(
        api_key=settings.api_key,
        voice_id=settings.voice_id,
        model_id=settings.model_id,
        speed=settings.speed,
        stability=stability,
    ).save()
    print(f"ElevenLabs stability set to {stability:.2f}.")
    return 0


def _print_voices(voices: list[dict]) -> None:
    if not voices:
        print("No voices were returned for this ElevenLabs account.")
        return
    ordered = sorted(
        voices,
        key=lambda voice: (
            str((voice.get("labels") or {}).get("accent", "")).lower()
            not in {"american", "us"},
            str(voice.get("name", "")).lower(),
        ),
    )
    print("\nAvailable voices:")
    for voice in ordered:
        labels = voice.get("labels") or {}
        details = " · ".join(
            value
            for value in (
                str(labels.get("accent", "")).strip(),
                str(labels.get("gender", "")).strip(),
                str(labels.get("description", "")).strip(),
            )
            if value
        )
        suffix = f" — {details}" if details else ""
        print(f"  {voice.get('voice_id', '')}  {voice.get('name', 'Unnamed')}{suffix}")
    print()


def _recommended_voice_id(voices: list[dict], current_voice_id: str) -> str:
    voice_ids = {
        str(voice.get("voice_id", "")).strip()
        for voice in voices
        if voice.get("voice_id")
    }
    if current_voice_id in voice_ids:
        return current_voice_id
    for voice in voices:
        labels = voice.get("labels") or {}
        if str(labels.get("accent", "")).lower() in {"american", "us"}:
            return str(voice.get("voice_id", "")).strip()
    return next(iter(voice_ids), current_voice_id)


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
            "configure-elevenlabs",
            "voices",
            "set-voice",
            "set-speed",
            "set-tts-model",
            "set-stability",
            "quit",
        ],
    )
    parser.add_argument("value", nargs="?")
    arguments = parser.parse_args(argv)

    if arguments.command == "status":
        return print_waybar_status()
    if arguments.command == "daemon":
        return daemon()
    if arguments.command == "doctor":
        return doctor()
    if arguments.command == "configure-elevenlabs":
        return configure_elevenlabs()
    if arguments.command == "voices":
        return list_voices()
    if arguments.command == "set-voice":
        if not arguments.value:
            parser.error("set-voice requires <voice-id>")
        return set_voice(arguments.value)
    if arguments.command == "set-speed":
        if not arguments.value:
            parser.error("set-speed requires <0.7-1.2>")
        return set_speed(arguments.value)
    if arguments.command == "set-tts-model":
        if not arguments.value:
            parser.error("set-tts-model requires a model ID")
        return set_tts_model(arguments.value)
    if arguments.command == "set-stability":
        if not arguments.value:
            parser.error("set-stability requires <0-1>")
        return set_stability(arguments.value)
    return send_command(arguments.command, start_service=arguments.command != "quit")


if __name__ == "__main__":
    raise SystemExit(main())
