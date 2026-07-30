from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


APP_NAME = "notch-voice-assistant"
HOME_DIR = Path("/home/masono")
RUNTIME_ROOT = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
RUNTIME_DIR = RUNTIME_ROOT / APP_NAME
SOCKET_PATH = RUNTIME_DIR / "control.sock"
STATUS_PATH = RUNTIME_DIR / "status.json"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", HOME_DIR / ".local/state")) / APP_NAME
SESSION_PATH = STATE_DIR / "session.json"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME_DIR / ".local/share")) / APP_NAME
MODELS_DIR = DATA_DIR / "models"
VENV_DIR = DATA_DIR / "venv"
STYLE_PATH = Path("/home/masono/.config/notch-voice-assistant/style.css")
CLAUDE_BIN = Path("/home/masono/.local/bin/claude")


class AssistantState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    ACTING = "acting"
    SPEAKING = "speaking"
    ERROR = "error"


STATE_LABELS = {
    AssistantState.IDLE: "Ready",
    AssistantState.LISTENING: "Listening…",
    AssistantState.TRANSCRIBING: "Transcribing locally…",
    AssistantState.THINKING: "Claude is thinking…",
    AssistantState.ACTING: "Claude is working…",
    AssistantState.SPEAKING: "Speaking…",
    AssistantState.ERROR: "Needs attention",
}


@dataclass(slots=True)
class SessionPreferences:
    session_id: str | None = None
    model: str = "sonnet"
    muted: bool = False
    messages: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls) -> "SessionPreferences":
        try:
            raw = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return cls()

        model = raw.get("model", "sonnet")
        if model not in {"sonnet", "opus"}:
            model = "sonnet"

        messages = raw.get("messages", [])
        if not isinstance(messages, list):
            messages = []
        messages = [
            {"role": str(item.get("role", "")), "text": str(item.get("text", ""))}
            for item in messages[-12:]
            if isinstance(item, dict)
        ]
        return cls(
            session_id=raw.get("session_id") or None,
            model=model,
            muted=bool(raw.get("muted", False)),
            messages=messages,
        )

    def save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "session_id": self.session_id,
            "model": self.model,
            "muted": self.muted,
            "messages": self.messages[-12:],
        }
        atomic_json_write(SESSION_PATH, payload, mode=0o600)

    def reset_conversation(self) -> None:
        self.session_id = None
        self.messages.clear()
        self.save()


def atomic_json_write(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary_file:
            json.dump(payload, temporary_file, ensure_ascii=False, separators=(",", ":"))
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def status_payload(
    state: AssistantState = AssistantState.IDLE,
    detail: str | None = None,
    *,
    visible: bool = False,
    degraded: str | None = None,
) -> dict[str, Any]:
    label = STATE_LABELS[state]
    tooltip_lines = ["Claude voice assistant", label]
    if detail and detail != label:
        tooltip_lines.append(detail)
    if degraded:
        tooltip_lines.append(degraded)
    tooltip_lines.append("Click to toggle")
    return {
        "text": "󰚩",
        "class": [state.value, "open" if visible else "closed"],
        "tooltip": "\n".join(tooltip_lines),
        "state": state.value,
        "detail": detail or label,
        "visible": visible,
        "degraded": degraded,
        "updated_at": time.time(),
    }


def read_status() -> dict[str, Any]:
    try:
        raw = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            AssistantState(str(raw.get("state")))
            return raw
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        pass
    return status_payload()


def prepare_runtime_dir() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(RUNTIME_DIR, 0o700)
