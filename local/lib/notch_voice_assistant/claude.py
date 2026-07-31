from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .state import CLAUDE_BIN, HOME_DIR


VOICE_SYSTEM_PROMPT = """
You are the user's on-demand Linux desktop voice assistant. You may use the
available Claude Code tools and enabled plugins proactively. Follow the
configured Auto permission mode and all hooks. Your final response will be
displayed and spoken aloud: make it direct, conversational, and normally no
more than 75 words unless the user explicitly asks for detail. Do not repeat
raw tool output or narrate every tool call in the final response. If an action
is irreversible or materially risky, respect any existing permission or
guardrail requirement instead of trying to bypass it.
""".strip()


TOOL_STATUS = {
    "Bash": "I’m working on the system.",
    "Read": "I’m checking the relevant files.",
    "Glob": "I’m finding the right files.",
    "Grep": "I’m searching for that.",
    "Edit": "I’m updating the configuration.",
    "Write": "I’m writing the requested change.",
    "WebFetch": "I’m checking the source.",
    "WebSearch": "I’m researching that.",
    "Task": "I’m working through that.",
}


@dataclass(slots=True)
class ClaudeResult:
    text: str
    session_id: str | None
    error: str | None = None
    permission_blocked: bool = False
    return_code: int = 0


def friendly_tool_status(tool_name: str) -> str:
    return TOOL_STATUS.get(tool_name, "I’m working on that.")


def sanitize_for_speech(text: str, limit: int = 1200) -> str:
    cleaned = re.sub(r"```.*?```", " The details are in the transcript. ", text, flags=re.S)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+", "the linked page", cleaned)
    cleaned = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*[-*+]\s+", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*\d+\.\s+", "", cleaned)
    cleaned = re.sub(r"[*_~>|]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned

    first_paragraph = re.split(r"(?<=[.!?])\s+", cleaned[:limit])
    summary = " ".join(first_paragraph[:-1]).strip() or cleaned[:limit].rsplit(" ", 1)[0]
    return f"{summary}. The complete answer is in the transcript."


def first_spoken_sentence(text: str, minimum_chars: int = 20) -> tuple[str, int] | None:
    match = re.match(
        rf"(?s)^(.{{{minimum_chars},}}?[.!?][\"'”’)\]]*)(?=\s|$)",
        text,
    )
    if match is None:
        return None
    spoken = sanitize_for_speech(match.group(1))
    return (spoken, match.end()) if spoken else None


class ClaudeRunner:
    def __init__(self, claude_bin: Path = CLAUDE_BIN) -> None:
        self.claude_bin = claude_bin
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def build_command(
        self,
        prompt: str,
        *,
        model: str,
        session_id: str | None,
    ) -> list[str]:
        command = [
            str(self.claude_bin),
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode",
            "auto",
            "--model",
            model,
            "--append-system-prompt",
            VOICE_SYSTEM_PROMPT,
        ]
        if session_id:
            command.extend(["--resume", session_id])
        else:
            command.extend(["--name", "Notch Voice Assistant"])
        command.append(prompt)
        return command

    def run(
        self,
        prompt: str,
        *,
        model: str,
        session_id: str | None,
        on_delta: Callable[[str], None] | None = None,
        on_tool: Callable[[str], None] | None = None,
        on_retry: Callable[[str], None] | None = None,
    ) -> ClaudeResult:
        if not self.claude_bin.is_file():
            return ClaudeResult("", session_id, f"Claude is not installed at {self.claude_bin}", return_code=127)

        command = self.build_command(prompt, model=model, session_id=session_id)
        try:
            process = subprocess.Popen(
                command,
                cwd=HOME_DIR,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=True,
                env=os.environ.copy(),
            )
        except OSError as error:
            return ClaudeResult("", session_id, str(error), return_code=126)

        with self._lock:
            self._process = process

        text_parts: list[str] = []
        resolved_session_id = session_id
        result_text = ""
        permission_blocked = False
        stream_error: str | None = None

        assert process.stdout is not None
        for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            resolved_session_id = event.get("session_id") or resolved_session_id
            event_type = event.get("type")
            subtype = event.get("subtype")

            if event_type == "stream_event":
                inner = event.get("event", {})
                if (
                    inner.get("type") == "content_block_delta"
                    and inner.get("delta", {}).get("type") == "text_delta"
                ):
                    delta = inner["delta"].get("text", "")
                    text_parts.append(delta)
                    if delta and on_delta:
                        on_delta(delta)
                elif inner.get("type") == "content_block_start":
                    block = inner.get("content_block", {})
                    if block.get("type") == "tool_use" and on_tool:
                        on_tool(str(block.get("name", "Tool")))
            elif event_type == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use" and on_tool:
                        on_tool(str(block.get("name", "Tool")))
            elif event_type == "system" and subtype == "api_retry":
                message = f"Retrying Claude ({event.get('attempt', '?')}/{event.get('max_retries', '?')})"
                if on_retry:
                    on_retry(message)
            elif event_type == "result":
                result_text = str(event.get("result", "") or "")
                if event.get("is_error"):
                    stream_error = result_text or "Claude returned an error."
                denials = event.get("permission_denials") or []
                permission_blocked = bool(denials)

        assert process.stderr is not None
        stderr = process.stderr.read().strip()
        return_code = process.wait()
        process.stdout.close()
        process.stderr.close()

        with self._lock:
            if self._process is process:
                self._process = None

        final_text = result_text.strip() or "".join(text_parts).strip()
        combined_error = stream_error
        if return_code and not combined_error:
            combined_error = stderr or final_text or f"Claude exited with status {return_code}."

        error_lower = f"{combined_error or ''} {stderr}".lower()
        if "permission" in error_lower and ("denied" in error_lower or "prompt" in error_lower):
            permission_blocked = True
        return ClaudeResult(
            final_text if not stream_error else "",
            resolved_session_id,
            combined_error,
            permission_blocked,
            return_code,
        )

    def cancel(self) -> None:
        with self._lock:
            process = self._process
        self._cancel_process(process)

    def cancel_in_background(self) -> None:
        with self._lock:
            process = self._process
        if not process or process.poll() is not None:
            return
        threading.Thread(
            target=self._cancel_process,
            args=(process,),
            name="claude-cancel",
            daemon=True,
        ).start()

    @staticmethod
    def _cancel_process(process: subprocess.Popen[str] | None) -> None:
        if not process or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=1)
                except ProcessLookupError:
                    pass
                except subprocess.TimeoutExpired:
                    pass
        except ProcessLookupError:
            pass
