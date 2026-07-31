from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from collections.abc import Callable

from .state import (
    FISH_S2_BINARY,
    FISH_S2_MODEL_PATH,
    FISH_S2_REFERENCE_PATH,
    FISH_S2_TOKENIZER_PATH,
    FISH_S2_VOICE_DIR,
    FISH_S2_VOICE_PATH,
)


REFERENCE_TEXT = (
    "Hello, I am your local voice assistant. I speak with a clear, calm "
    "American English voice. I can help you plan, build, explore, and "
    "understand whatever is on your mind. Whenever you are ready, just ask "
    "me a question."
)


class FishS2Unavailable(RuntimeError):
    pass


class FishS2Cancelled(RuntimeError):
    pass


class FishS2Synthesizer:
    """Persistent bridge to the local s2.cpp HTTP inference server."""

    START_TIMEOUT_SECONDS = 180
    SYNTHESIS_TIMEOUT_SECONDS = 300

    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._port: int | None = None
        self._stderr_tail: deque[str] = deque(maxlen=16)
        self._lock = threading.Lock()

    def synthesize_wav(self, text: str) -> bytes:
        with self._lock:
            self._ensure_server()
            if self._port is None:
                raise FishS2Unavailable("Fish Audio S2 Pro server did not start.")

            fields, files = self._request_parts(text, streaming=False)
            body, content_type = self._multipart_body(fields, files)
            request = urllib.request.Request(
                f"http://127.0.0.1:{self._port}/generate",
                data=body,
                headers={"Content-Type": content_type},
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.SYNTHESIS_TIMEOUT_SECONDS,
                ) as response:
                    wav_bytes = response.read()
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                detail = self._server_error()
                self._stop_server()
                raise FishS2Unavailable(detail or str(error)) from error

            if not wav_bytes.startswith(b"RIFF") or b"WAVE" not in wav_bytes[:16]:
                raise FishS2Unavailable("Fish Audio S2 Pro returned invalid WAV audio.")
            return wav_bytes

    def stream_pcm(
        self,
        text: str,
        on_chunk: Callable[[bytes, int], None],
    ) -> None:
        with self._lock:
            self._ensure_server()
            if self._port is None:
                raise FishS2Unavailable("Fish Audio S2 Pro server did not start.")

            fields, files = self._request_parts(text, streaming=True)
            body, content_type = self._multipart_body(fields, files)
            request = urllib.request.Request(
                f"http://127.0.0.1:{self._port}/generate",
                data=body,
                headers={"Content-Type": content_type},
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.SYNTHESIS_TIMEOUT_SECONDS,
                ) as response:
                    sample_rate = int(response.headers.get("X-Audio-Sample-Rate", "44100"))
                    while chunk := response.read(64 * 1024):
                        on_chunk(chunk, sample_rate)
            except FishS2Cancelled:
                raise
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                detail = self._server_error()
                self._stop_server()
                raise FishS2Unavailable(detail or str(error)) from error

    def prewarm(self) -> None:
        with self._lock:
            self._ensure_server()

    @staticmethod
    def _generation_params(*, streaming: bool) -> dict[str, object]:
        params: dict[str, object] = {
            "stream": True,
            "segment_sentences": True,
            "sentence_pause_ms": 150,
            "max_new_tokens": 768,
            "temperature": 0.58,
            "top_p": 0.88,
            "top_k": 40,
        }
        if streaming:
            params.update(
                {
                    "chunked": True,
                    "output_format": "pcm_s16le",
                    "stream_start_buffer_ms": 1200,
                }
            )
        if os.environ.get("NOTCH_VOICE_FISH_S2_BACKEND", "vulkan").lower() != "cpu":
            params["codec_follow_backend"] = True
            params["codec_auto_backend"] = False
        return params

    def _request_parts(
        self,
        text: str,
        *,
        streaming: bool,
    ) -> tuple[dict[str, str], dict[str, tuple[str, str, bytes]]]:
        fields = {
            "text": text,
            "params": json.dumps(
                self._generation_params(streaming=streaming),
                separators=(",", ":"),
            ),
        }
        files: dict[str, tuple[str, str, bytes]] = {}
        if FISH_S2_VOICE_PATH.is_file():
            fields["voice"] = FISH_S2_VOICE_PATH.stem
            fields["voice_dir"] = str(FISH_S2_VOICE_DIR)
        elif FISH_S2_REFERENCE_PATH.is_file():
            fields["reference_text"] = REFERENCE_TEXT
            files["reference"] = (
                FISH_S2_REFERENCE_PATH.name,
                "audio/wav",
                FISH_S2_REFERENCE_PATH.read_bytes(),
            )
        return fields, files

    def _ensure_server(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        missing = [
            path
            for path in (FISH_S2_BINARY, FISH_S2_MODEL_PATH, FISH_S2_TOKENIZER_PATH)
            if not path.is_file()
        ]
        if missing:
            raise FishS2Unavailable(f"Fish Audio S2 Pro is missing: {missing[0]}")

        self._stderr_tail.clear()
        port = self._available_port()
        backend = os.environ.get("NOTCH_VOICE_FISH_S2_BACKEND", "vulkan").lower()
        command = [
            str(FISH_S2_BINARY),
            "--model",
            str(FISH_S2_MODEL_PATH),
            "--tokenizer",
            str(FISH_S2_TOKENIZER_PATH),
            "--server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--voice-dir",
            str(FISH_S2_VOICE_DIR),
            "--log-level",
            "warn",
        ]
        if backend == "vulkan":
            command.extend(["--vulkan", "0", "--codec-follow-backend"])
        elif backend == "cuda":
            command.extend(["--cuda", "0", "--codec-follow-backend"])
        elif backend != "cpu":
            raise FishS2Unavailable(
                "NOTCH_VOICE_FISH_S2_BACKEND must be vulkan, cuda, or cpu."
            )

        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self._process = process
        self._port = port
        threading.Thread(
            target=self._read_stderr,
            args=(process,),
            name="fish-s2-stderr",
            daemon=True,
        ).start()

        deadline = time.monotonic() + self.START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                    return
            except OSError:
                time.sleep(0.1)
        detail = self._server_error()
        self._stop_server()
        raise FishS2Unavailable(detail or "Timed out loading Fish Audio S2 Pro.")

    @staticmethod
    def _available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            candidate.bind(("127.0.0.1", 0))
            return int(candidate.getsockname()[1])

    @staticmethod
    def _multipart_body(
        fields: dict[str, str],
        files: dict[str, tuple[str, str, bytes]],
    ) -> tuple[bytes, str]:
        boundary = f"notch-voice-{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    (
                        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    ).encode(),
                    value.encode(),
                    b"\r\n",
                ]
            )
        for name, (filename, media_type, content) in files.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    (
                        f'Content-Disposition: form-data; name="{name}"; '
                        f'filename="{filename}"\r\n'
                    ).encode(),
                    f"Content-Type: {media_type}\r\n\r\n".encode(),
                    content,
                    b"\r\n",
                ]
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            if line.strip():
                self._stderr_tail.append(line.strip())

    def _server_error(self) -> str:
        if self._stderr_tail:
            return self._stderr_tail[-1]
        if self._process is not None and self._process.poll() is not None:
            return f"Fish Audio S2 Pro exited with status {self._process.returncode}."
        return ""

    def close(self) -> None:
        with self._lock:
            self._stop_server()

    def _stop_server(self) -> None:
        process = self._process
        self._process = None
        self._port = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process.stderr is not None:
            process.stderr.close()
