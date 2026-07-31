from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .state import ELEVENLABS_CONFIG_PATH, atomic_json_write


DEFAULT_VOICE_ID = "cgSgspJ2msm6clMCkdW9"
DEFAULT_MODEL_ID = "eleven_flash_v2_5"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
DEFAULT_SPEED = 1.15
MINIMUM_SPEED = 0.7
MAXIMUM_SPEED = 1.2
API_ROOT = "https://api.elevenlabs.io"


class ElevenLabsUnavailable(RuntimeError):
    pass


class ElevenLabsCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ElevenLabsSettings:
    api_key: str
    voice_id: str = DEFAULT_VOICE_ID
    model_id: str = DEFAULT_MODEL_ID
    speed: float = DEFAULT_SPEED

    @classmethod
    def load(cls) -> "ElevenLabsSettings":
        saved: dict[str, Any] = {}
        try:
            raw = json.loads(ELEVENLABS_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                saved = raw
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

        api_key = os.environ.get(
            "ELEVENLABS_API_KEY",
            str(saved.get("api_key", "")),
        ).strip()
        voice_id = os.environ.get(
            "ELEVENLABS_VOICE_ID",
            str(saved.get("voice_id", DEFAULT_VOICE_ID)),
        ).strip()
        model_id = os.environ.get(
            "ELEVENLABS_MODEL_ID",
            str(saved.get("model_id", DEFAULT_MODEL_ID)),
        ).strip()
        raw_speed = os.environ.get(
            "ELEVENLABS_SPEED",
            str(saved.get("speed", DEFAULT_SPEED)),
        ).strip()
        try:
            speed = float(raw_speed)
        except ValueError:
            speed = DEFAULT_SPEED
        if not MINIMUM_SPEED <= speed <= MAXIMUM_SPEED:
            speed = DEFAULT_SPEED
        return cls(
            api_key=api_key,
            voice_id=voice_id or DEFAULT_VOICE_ID,
            model_id=model_id or DEFAULT_MODEL_ID,
            speed=speed,
        )

    def save(self) -> None:
        atomic_json_write(
            ELEVENLABS_CONFIG_PATH,
            {
                "api_key": self.api_key,
                "voice_id": self.voice_id,
                "model_id": self.model_id,
                "speed": self.speed,
            },
            mode=0o600,
        )


class ElevenLabsSynthesizer:
    """Low-latency ElevenLabs Flash streaming client."""

    REQUEST_TIMEOUT_SECONDS = 120

    def stream_mp3(self, text: str, on_chunk: Callable[[bytes], None]) -> None:
        settings = ElevenLabsSettings.load()
        if not settings.api_key:
            raise ElevenLabsUnavailable(
                "ElevenLabs is not configured. Run "
                "`notch-voice-assistant configure-elevenlabs`."
            )

        query = urllib.parse.urlencode({"output_format": DEFAULT_OUTPUT_FORMAT})
        voice_id = urllib.parse.quote(settings.voice_id, safe="")
        request = urllib.request.Request(
            f"{API_ROOT}/v1/text-to-speech/{voice_id}/stream?{query}",
            data=json.dumps(
                {
                    "text": text,
                    "model_id": settings.model_id,
                    "language_code": "en",
                    "voice_settings": {
                        "stability": 0.35,
                        "similarity_boost": 0.75,
                        "style": 0.0,
                        "use_speaker_boost": True,
                        "speed": settings.speed,
                    },
                },
                separators=(",", ":"),
            ).encode("utf-8"),
            headers={
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": settings.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            ) as response:
                while chunk := response.read1(4 * 1024):
                    on_chunk(chunk)
        except ElevenLabsCancelled:
            raise
        except urllib.error.HTTPError as error:
            raise ElevenLabsUnavailable(self._http_error(error)) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ElevenLabsUnavailable(f"ElevenLabs request failed: {error}") from error

    @staticmethod
    def list_voices(api_key: str) -> list[dict[str, Any]]:
        if not api_key.strip():
            raise ElevenLabsUnavailable("An ElevenLabs API key is required.")
        query = urllib.parse.urlencode(
            {"page_size": 100, "include_total_count": "false"}
        )
        request = urllib.request.Request(
            f"{API_ROOT}/v2/voices?{query}",
            headers={"Accept": "application/json", "xi-api-key": api_key.strip()},
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=30,
            ) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            raise ElevenLabsUnavailable(
                ElevenLabsSynthesizer._http_error(error)
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise ElevenLabsUnavailable(f"Could not list ElevenLabs voices: {error}") from error
        voices = payload.get("voices", []) if isinstance(payload, dict) else []
        return [voice for voice in voices if isinstance(voice, dict)]

    @staticmethod
    def _http_error(error: urllib.error.HTTPError) -> str:
        detail = ""
        try:
            payload = json.loads(error.read(4096).decode("utf-8", errors="replace"))
            raw_detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            if isinstance(raw_detail, dict):
                detail = str(
                    raw_detail.get("message")
                    or raw_detail.get("status")
                    or raw_detail
                )
            else:
                detail = str(raw_detail)
        except (json.JSONDecodeError, OSError):
            pass
        suffix = f": {detail}" if detail else ""
        return f"ElevenLabs returned HTTP {error.code}{suffix}"
