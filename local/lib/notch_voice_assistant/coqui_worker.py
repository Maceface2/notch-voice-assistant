from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import sys
import wave

import numpy as np


MODEL_NAME = "tts_models/en/ljspeech/vits"


def samples_to_wav(samples, sample_rate: int) -> bytes:
    audio = np.asarray(samples, dtype=np.float32)
    audio = np.nan_to_num(audio, nan=0.0, posinf=1.0, neginf=-1.0)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1.0:
        audio = audio / peak
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")

    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return output.getvalue()


def load_model():
    from TTS.api import TTS

    model = TTS(model_name=MODEL_NAME, progress_bar=False, gpu=False)
    return model, int(model.synthesizer.output_sample_rate)


def emit(stream, payload: dict) -> None:
    stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
    stream.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preload", action="store_true")
    arguments = parser.parse_args(argv)
    protocol_output = sys.stdout

    try:
        with contextlib.redirect_stdout(sys.stderr):
            model, sample_rate = load_model()
    except Exception as error:
        emit(protocol_output, {"ready": False, "error": str(error)})
        return 1

    if arguments.preload:
        emit(
            protocol_output,
            {"ready": True, "model": MODEL_NAME, "sample_rate": sample_rate},
        )
        return 0

    emit(
        protocol_output,
        {"ready": True, "model": MODEL_NAME, "sample_rate": sample_rate},
    )
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("command") == "quit":
                return 0
            text = str(request.get("text", "")).strip()
            if not text:
                raise ValueError("No text was provided.")
            with contextlib.redirect_stdout(sys.stderr):
                samples = model.tts(text=text)
            wav_bytes = samples_to_wav(samples, sample_rate)
            emit(
                protocol_output,
                {"ok": True, "wav": base64.b64encode(wav_bytes).decode("ascii")},
            )
        except Exception as error:
            emit(protocol_output, {"ok": False, "error": str(error)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
