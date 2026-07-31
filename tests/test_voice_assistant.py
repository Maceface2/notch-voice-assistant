from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PACKAGE_ROOT = Path(__file__).parents[1] / "local/lib"
sys.path.insert(0, str(PACKAGE_ROOT))

from notch_voice_assistant.claude import (  # noqa: E402
    ClaudeRunner,
    first_spoken_sentence,
    friendly_tool_status,
    sanitize_for_speech,
)
from notch_voice_assistant.audio import SpeechServices  # noqa: E402
from notch_voice_assistant.cli import set_speed, set_voice  # noqa: E402
from notch_voice_assistant.elevenlabs import (  # noqa: E402
    DEFAULT_MODEL_ID,
    DEFAULT_OUTPUT_FORMAT,
    ElevenLabsSettings,
    ElevenLabsSynthesizer,
)
from notch_voice_assistant.state import (  # noqa: E402
    AssistantState,
    HOME_DIR,
    SessionPreferences,
    status_payload,
)


class SpeechSanitizerTests(unittest.TestCase):
    def test_removes_markdown_and_links(self) -> None:
        text = "## Result\n- Open [the docs](https://example.com) and run `systemctl status`."
        self.assertEqual(
            sanitize_for_speech(text),
            "Result Open the docs and run systemctl status.",
        )

    def test_long_response_points_to_transcript(self) -> None:
        text = "A complete sentence. " * 100
        spoken = sanitize_for_speech(text, limit=80)
        self.assertLess(len(spoken), 150)
        self.assertTrue(spoken.endswith("The complete answer is in the transcript."))

    def test_unknown_tool_has_friendly_fallback(self) -> None:
        self.assertEqual(friendly_tool_status("CustomTool"), "I’m working on that.")

    def test_extracts_first_complete_sentence_for_early_speech(self) -> None:
        text = "Here is the answer you need. The remaining detail is still streaming."
        self.assertEqual(
            first_spoken_sentence(text),
            ("Here is the answer you need.", 28),
        )
        self.assertIsNone(first_spoken_sentence("This sentence is not complete yet"))


class StatusTests(unittest.TestCase):
    def test_home_directory_is_discovered_at_runtime(self) -> None:
        self.assertEqual(HOME_DIR, Path.home())

    def test_waybar_status_contains_state_and_visibility_classes(self) -> None:
        payload = status_payload(
            AssistantState.LISTENING,
            "Listening…",
            visible=True,
        )
        self.assertEqual(payload["state"], "listening")
        self.assertEqual(payload["text"], "✳")
        self.assertEqual(payload["class"], ["listening", "open"])
        self.assertIn("Click to toggle", payload["tooltip"])

    def test_session_preferences_validate_model_and_trim_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session_path = Path(directory) / "session.json"
            session_path.write_text(
                json.dumps(
                    {
                        "model": "invalid",
                        "muted": True,
                        "messages": [
                            {"role": "user", "text": str(index)} for index in range(20)
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "notch_voice_assistant.state.SESSION_PATH",
                session_path,
            ):
                preferences = SessionPreferences.load()
        self.assertEqual(preferences.model, "sonnet")
        self.assertTrue(preferences.muted)
        self.assertEqual(len(preferences.messages), 12)
        self.assertEqual(preferences.messages[0]["text"], "8")


class SpeechServiceTests(unittest.TestCase):
    def test_elevenlabs_stream_uses_flash_and_low_latency_mp3(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read1.side_effect = [b"ID3", b"audio", b""]
        settings = ElevenLabsSettings(
            api_key="test-key",
            voice_id="voice-123",
            model_id=DEFAULT_MODEL_ID,
        )
        chunks: list[bytes] = []
        with (
            mock.patch.object(ElevenLabsSettings, "load", return_value=settings),
            mock.patch(
                "notch_voice_assistant.elevenlabs.urllib.request.urlopen",
                return_value=response,
            ) as urlopen,
        ):
            ElevenLabsSynthesizer().stream_mp3("Hello", chunks.append)

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertIn(f"output_format={DEFAULT_OUTPUT_FORMAT}", request.full_url)
        self.assertEqual(DEFAULT_OUTPUT_FORMAT, "mp3_44100_128")
        self.assertIn("/voice-123/stream", request.full_url)
        self.assertEqual(payload["model_id"], "eleven_flash_v2_5")
        self.assertEqual(payload["language_code"], "en")
        self.assertEqual(payload["voice_settings"]["speed"], 1.15)
        self.assertEqual(chunks, [b"ID3", b"audio"])

    def test_elevenlabs_config_environment_overrides_saved_values(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch(
                "notch_voice_assistant.elevenlabs.ELEVENLABS_CONFIG_PATH",
                Path(directory) / "elevenlabs.json",
            ),
            mock.patch.dict(
                os.environ,
                {
                    "ELEVENLABS_API_KEY": "environment-key",
                    "ELEVENLABS_VOICE_ID": "environment-voice",
                    "ELEVENLABS_SPEED": "1.1",
                },
            ),
        ):
            settings = ElevenLabsSettings.load()
        self.assertEqual(settings.api_key, "environment-key")
        self.assertEqual(settings.voice_id, "environment-voice")
        self.assertEqual(settings.speed, 1.1)

    def test_set_voice_saves_an_elevenlabs_voice_id(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch(
                "notch_voice_assistant.elevenlabs.ELEVENLABS_CONFIG_PATH",
                Path(directory) / "elevenlabs.json",
            ),
        ):
            self.assertEqual(set_voice("voice_123"), 0)
            settings = ElevenLabsSettings.load()
        self.assertEqual(settings.voice_id, "voice_123")

    def test_set_speed_validates_and_saves_supported_value(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch(
                "notch_voice_assistant.elevenlabs.ELEVENLABS_CONFIG_PATH",
                Path(directory) / "elevenlabs.json",
            ),
        ):
            self.assertEqual(set_speed("1.15"), 0)
            settings = ElevenLabsSettings.load()
            self.assertEqual(set_speed("1.25"), 2)
        self.assertEqual(settings.speed, 1.15)

    def test_whisper_device_can_be_forced_with_environment(self) -> None:
        with mock.patch.dict(os.environ, {"NOTCH_VOICE_WHISPER_DEVICE": "cpu"}):
            self.assertEqual(SpeechServices._read_whisper_device_preference(), "cpu")

    def test_rejects_thank_you_silence_hallucination(self) -> None:
        class Segment:
            text = "Thank you."
            no_speech_prob = 0.82

        self.assertEqual(SpeechServices._segments_to_text([Segment()]), "")

    def test_keeps_confident_thank_you_transcription(self) -> None:
        class Segment:
            text = "Thank you."
            no_speech_prob = 0.05

        self.assertEqual(
            SpeechServices._segments_to_text([Segment()]),
            "Thank you.",
        )

    def test_cuda_inference_failure_retries_on_cpu(self) -> None:
        class FailingCudaModel:
            def transcribe(self, *_args, **_kwargs):
                raise RuntimeError("libcublas unavailable")

        class Segment:
            text = "CPU fallback worked."

        class CpuModel:
            def transcribe(self, *_args, **_kwargs):
                return iter([Segment()]), object()

        speech = SpeechServices.__new__(SpeechServices)
        speech.whisper_model = FailingCudaModel()
        speech.whisper_backend = "CUDA · distil-large-v3"
        speech.whisper_device_preference = "auto"

        def load_cpu(*, force_cpu: bool = False) -> None:
            if not force_cpu:
                return
            speech.whisper_model = CpuModel()
            speech.whisper_backend = "CPU fallback · small.en"

        with mock.patch.object(speech, "_load_whisper", side_effect=load_cpu) as loader:
            transcript = speech.transcribe(b"\0\0" * 100)

        loader.assert_any_call(force_cpu=True)
        self.assertEqual(transcript, "CPU fallback worked.")
        self.assertEqual(speech.whisper_backend, "CPU fallback · small.en")


class ClaudeRunnerTests(unittest.TestCase):
    def _mock_claude(self, directory: str, events: list[dict]) -> Path:
        script = Path(directory) / "mock-claude"
        encoded_events = json.dumps(events)
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            f"events = json.loads({encoded_events!r})\n"
            "for event in events:\n"
            "    print(json.dumps(event), flush=True)\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script

    def test_build_command_uses_auto_mode_and_resumes(self) -> None:
        runner = ClaudeRunner(Path("/tmp/claude"))
        command = runner.build_command(
            "hello",
            model="opus",
            session_id="session-123",
        )
        self.assertIn("--permission-mode", command)
        self.assertIn("auto", command)
        self.assertIn("--resume", command)
        self.assertIn("session-123", command)
        self.assertEqual(command[-1], "hello")

    def test_parses_stream_text_tool_and_session(self) -> None:
        events = [
            {"type": "system", "subtype": "init", "session_id": "session-abc"},
            {
                "type": "stream_event",
                "session_id": "session-abc",
                "event": {
                    "type": "content_block_start",
                    "content_block": {"type": "tool_use", "name": "Read"},
                },
            },
            {
                "type": "stream_event",
                "session_id": "session-abc",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Done"},
                },
            },
            {
                "type": "result",
                "session_id": "session-abc",
                "result": "Done",
                "is_error": False,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            executable = self._mock_claude(directory, events)
            deltas: list[str] = []
            tools: list[str] = []
            result = ClaudeRunner(executable).run(
                "hello",
                model="sonnet",
                session_id=None,
                on_delta=deltas.append,
                on_tool=tools.append,
            )
        self.assertEqual(result.text, "Done")
        self.assertEqual(result.session_id, "session-abc")
        self.assertEqual(deltas, ["Done"])
        self.assertEqual(tools, ["Read"])
        self.assertIsNone(result.error)

    def test_marks_permission_failure(self) -> None:
        events = [
            {
                "type": "result",
                "session_id": "session-abc",
                "result": "Permission denied",
                "is_error": True,
                "permission_denials": [{"tool_name": "Bash"}],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            executable = self._mock_claude(directory, events)
            result = ClaudeRunner(executable).run(
                "change it",
                model="sonnet",
                session_id=None,
            )
        self.assertTrue(result.permission_blocked)
        self.assertEqual(result.error, "Permission denied")


if __name__ == "__main__":
    unittest.main()
