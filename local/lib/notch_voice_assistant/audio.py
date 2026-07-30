from __future__ import annotations

import io
import os
import subprocess
import threading
import wave
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np

from .state import MODELS_DIR


SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2
PREROLL_FRAMES = 15
SILENCE_FRAMES = 50
MAX_CAPTURE_FRAMES = 6_000


class AudioUnavailable(RuntimeError):
    pass


class VoiceRecorder:
    """PipeWire/GStreamer recorder with WebRTC voice activity detection."""

    def __init__(
        self,
        on_complete: Callable[[bytes], None],
        on_error: Callable[[str], None],
        *,
        vad_aggressiveness: int = 2,
    ) -> None:
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import GLib, Gst
            import webrtcvad
        except (ImportError, ValueError) as error:
            raise AudioUnavailable(
                "Local recording needs GStreamer and python3-webrtcvad."
            ) from error

        self.Gst = Gst
        self.GLib = GLib
        self.on_complete = on_complete
        self.on_error = on_error
        self.vad = webrtcvad.Vad(vad_aggressiveness)
        self.pipeline = None
        self.pending = bytearray()
        self.preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        self.recent_voiced: deque[bool] = deque(maxlen=10)
        self.captured: list[bytes] = []
        self.speech_started = False
        self.silence_count = 0
        self.finished = False
        self._lock = threading.Lock()

    def start(self) -> None:
        Gst = self.Gst
        Gst.init(None)
        descriptions = [
            (
                "pipewiresrc ! audioconvert ! audioresample ! "
                "audio/x-raw,format=S16LE,rate=16000,channels=1 ! "
                "appsink name=voice_sink emit-signals=true sync=false max-buffers=50 drop=true"
            ),
            (
                "autoaudiosrc ! audioconvert ! audioresample ! "
                "audio/x-raw,format=S16LE,rate=16000,channels=1 ! "
                "appsink name=voice_sink emit-signals=true sync=false max-buffers=50 drop=true"
            ),
        ]
        last_error: Exception | None = None
        for description in descriptions:
            try:
                self.pipeline = Gst.parse_launch(description)
                break
            except Exception as error:
                last_error = error
        if self.pipeline is None:
            raise AudioUnavailable(f"Could not create an audio capture pipeline: {last_error}")

        sink = self.pipeline.get_by_name("voice_sink")
        sink.connect("new-sample", self._on_sample)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)
        change = self.pipeline.set_state(Gst.State.PLAYING)
        if change == Gst.StateChangeReturn.FAILURE:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            raise AudioUnavailable("The default microphone could not be opened.")

    def _on_sample(self, sink):
        Gst = self.Gst
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buffer = sample.get_buffer()
        success, mapped = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.ERROR
        try:
            self.pending.extend(mapped.data)
        finally:
            buffer.unmap(mapped)

        while len(self.pending) >= FRAME_BYTES:
            frame = bytes(self.pending[:FRAME_BYTES])
            del self.pending[:FRAME_BYTES]
            self._consume_frame(frame)
        return Gst.FlowReturn.OK

    def _consume_frame(self, frame: bytes) -> None:
        with self._lock:
            if self.finished:
                return
            try:
                voiced = self.vad.is_speech(frame, SAMPLE_RATE)
            except Exception:
                voiced = False

            if not self.speech_started:
                self.preroll.append(frame)
                self.recent_voiced.append(voiced)
                if sum(self.recent_voiced) >= 3:
                    self.speech_started = True
                    self.captured.extend(self.preroll)
                    self.preroll.clear()
                return

            self.captured.append(frame)
            self.silence_count = 0 if voiced else self.silence_count + 1
            should_finish = (
                self.silence_count >= SILENCE_FRAMES
                or len(self.captured) >= MAX_CAPTURE_FRAMES
            )
        if should_finish:
            self.GLib.idle_add(self._finish_from_idle)

    def _finish_from_idle(self) -> bool:
        self.finish(submit=True)
        return False

    def _on_bus_error(self, _bus, message) -> None:
        error, _debug = message.parse_error()
        self.finish(submit=False)
        self.on_error(str(error))

    def finish(self, *, submit: bool) -> None:
        with self._lock:
            if self.finished:
                return
            self.finished = True
            captured = b"".join(self.captured) if submit and self.speech_started else b""
        if self.pipeline is not None:
            self.pipeline.set_state(self.Gst.State.NULL)
            self.pipeline = None
        self.on_complete(captured)


class SpeechServices:
    def __init__(self) -> None:
        self.whisper_model = None
        self.whisper_backend = ""
        self.piper_voice = None
        self.piper_error: str | None = None
        self._model_lock = threading.Lock()
        self._speech_lock = threading.Lock()
        self._playback_process: subprocess.Popen[bytes] | None = None
        MODELS_DIR.mkdir(parents=True, exist_ok=True)

    def _load_whisper(self) -> None:
        if self.whisper_model is not None:
            return
        with self._model_lock:
            if self.whisper_model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:
                raise AudioUnavailable(
                    "Install python3-faster-whisper to enable local transcription."
                ) from error

            try:
                self.whisper_model = WhisperModel(
                    "distil-large-v3",
                    device="cuda",
                    compute_type="int8_float16",
                    download_root=str(MODELS_DIR),
                )
                self.whisper_backend = "CUDA · distil-large-v3"
            except Exception:
                self.whisper_model = WhisperModel(
                    "small.en",
                    device="cpu",
                    compute_type="int8",
                    download_root=str(MODELS_DIR),
                )
                self.whisper_backend = "CPU fallback · small.en"

    def transcribe(self, pcm_bytes: bytes) -> str:
        if not pcm_bytes:
            return ""
        self._load_whisper()
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self.whisper_model.transcribe(
            audio,
            language="en",
            beam_size=5,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    def _load_piper(self) -> None:
        if self.piper_voice is not None or self.piper_error:
            return
        with self._model_lock:
            if self.piper_voice is not None or self.piper_error:
                return
            model_path = MODELS_DIR / "en_US-lessac-medium.onnx"
            if not model_path.is_file():
                self.piper_error = f"Piper voice model is missing: {model_path}"
                return
            try:
                from piper import PiperVoice

                self.piper_voice = PiperVoice.load(str(model_path), use_cuda=False)
            except Exception as error:
                self.piper_error = str(error)

    def synthesize_wav(self, text: str) -> bytes:
        self._load_piper()
        if self.piper_voice is None:
            raise AudioUnavailable(self.piper_error or "Piper is unavailable.")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            self.piper_voice.synthesize_wav(text, wav_file)
        return output.getvalue()

    def speak(self, text: str) -> str | None:
        if not text:
            return None
        with self._speech_lock:
            try:
                wav_bytes = self.synthesize_wav(text)
                process = subprocess.Popen(
                    ["aplay", "-q"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                self._playback_process = process
                _stdout, stderr = process.communicate(wav_bytes)
                self._playback_process = None
                if process.returncode:
                    raise AudioUnavailable(stderr.decode(errors="replace").strip())
                return None
            except Exception as piper_error:
                self._playback_process = None
                try:
                    process = subprocess.Popen(
                        ["espeak-ng", "-s", "175", text],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                    )
                    self._playback_process = process
                    _stdout, stderr = process.communicate()
                    self._playback_process = None
                    if process.returncode:
                        raise AudioUnavailable(stderr.decode(errors="replace").strip())
                    return f"Piper unavailable; using eSpeak ({piper_error})"
                except Exception as fallback_error:
                    self._playback_process = None
                    raise AudioUnavailable(
                        f"Speech output failed: {piper_error}; fallback failed: {fallback_error}"
                    ) from fallback_error

    def stop_playback(self) -> None:
        process = self._playback_process
        if not process or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, 15)
        except ProcessLookupError:
            pass
