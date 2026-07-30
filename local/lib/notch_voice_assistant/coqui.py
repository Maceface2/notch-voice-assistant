from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import threading
from collections import deque
from pathlib import Path

from .state import COQUI_MODEL_DIR, COQUI_PYTHON


class CoquiUnavailable(RuntimeError):
    pass


class CoquiSynthesizer:
    """Persistent bridge to Coqui's Python 3.11 runtime."""

    START_TIMEOUT_SECONDS = 90
    SYNTHESIS_TIMEOUT_SECONDS = 120

    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=12)
        self._lock = threading.Lock()

    def synthesize_wav(self, text: str) -> bytes:
        with self._lock:
            self._ensure_worker()
            process = self._process
            if process is None or process.stdin is None:
                raise CoquiUnavailable("Coqui worker did not start.")

            try:
                process.stdin.write(json.dumps({"text": text}) + "\n")
                process.stdin.flush()
                response = self._responses.get(timeout=self.SYNTHESIS_TIMEOUT_SECONDS)
            except (BrokenPipeError, OSError, queue.Empty) as error:
                detail = self._worker_error()
                self._stop_worker()
                raise CoquiUnavailable(detail or str(error)) from error

            if not response.get("ok"):
                raise CoquiUnavailable(str(response.get("error") or "Coqui synthesis failed."))
            try:
                return base64.b64decode(response["wav"], validate=True)
            except (KeyError, ValueError) as error:
                raise CoquiUnavailable("Coqui returned invalid audio data.") from error

    def _ensure_worker(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if not COQUI_PYTHON.is_file():
            raise CoquiUnavailable(
                f"Coqui's Python 3.11 environment is missing: {COQUI_PYTHON}"
            )

        while True:
            try:
                self._responses.get_nowait()
            except queue.Empty:
                break
        self._stderr_tail.clear()

        environment = os.environ.copy()
        package_root = str(self._package_root())
        environment["PYTHONPATH"] = (
            f"{package_root}{os.pathsep}{environment['PYTHONPATH']}"
            if environment.get("PYTHONPATH")
            else package_root
        )
        environment["TTS_HOME"] = str(COQUI_MODEL_DIR)
        process = subprocess.Popen(
            [str(COQUI_PYTHON), "-m", "notch_voice_assistant.coqui_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=environment,
            start_new_session=True,
        )
        self._process = process
        threading.Thread(
            target=self._read_responses,
            args=(process,),
            name="coqui-responses",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(process,),
            name="coqui-stderr",
            daemon=True,
        ).start()

        try:
            ready = self._responses.get(timeout=self.START_TIMEOUT_SECONDS)
        except queue.Empty as error:
            detail = self._worker_error()
            self._stop_worker()
            raise CoquiUnavailable(detail or "Timed out loading the Coqui voice.") from error
        if not ready.get("ready"):
            detail = str(ready.get("error") or self._worker_error())
            self._stop_worker()
            raise CoquiUnavailable(detail)

    @staticmethod
    def _package_root() -> Path:
        return Path(__file__).resolve().parents[1]

    def _read_responses(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(response, dict):
                self._responses.put(response)

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            if line.strip():
                self._stderr_tail.append(line.strip())

    def _worker_error(self) -> str:
        if self._stderr_tail:
            return self._stderr_tail[-1]
        if self._process is not None and self._process.poll() is not None:
            return f"Coqui worker exited with status {self._process.returncode}."
        return ""

    def close(self) -> None:
        with self._lock:
            self._stop_worker()

    def _stop_worker(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None and process.stdin is not None:
            try:
                process.stdin.write('{"command":"quit"}\n')
                process.stdin.flush()
                process.wait(timeout=2)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


if __name__ == "__main__":
    print("Run notch_voice_assistant.coqui_worker with the Coqui Python environment.")
    raise SystemExit(2)
