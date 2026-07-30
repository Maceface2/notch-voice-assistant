from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

from .audio import AudioUnavailable, SpeechServices, VoiceRecorder
from .claude import ClaudeResult, ClaudeRunner, friendly_tool_status, sanitize_for_speech
from .state import (
    AssistantState,
    CLAUDE_BIN,
    HOME_DIR,
    SESSION_PATH,
    SOCKET_PATH,
    STATUS_PATH,
    STYLE_PATH,
    SessionPreferences,
    atomic_json_write,
    prepare_runtime_dir,
    status_payload,
)


class VoiceAssistantApplication:
    IDLE_EXIT_SECONDS = 600
    TOOL_SPEECH_INTERVAL = 8

    def __init__(self) -> None:
        self.Gtk, self.GLib, self.GtkLayerShell = self._load_gtk()
        self.preferences = SessionPreferences.load()
        self.claude = ClaudeRunner()
        self.speech = SpeechServices()
        self.recorder: VoiceRecorder | None = None
        self.state = AssistantState.IDLE
        self.detail = "Ready"
        self.degraded: str | None = None
        self.loop_active = False
        self.generation = 0
        self.last_activity = time.monotonic()
        self.last_tool_spoken_at = 0.0
        self.current_reply = ""
        self.control_socket: socket.socket | None = None
        self.control_thread: threading.Thread | None = None
        self._build_window()

    @staticmethod
    def _load_gtk():
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GLib, Gtk, GtkLayerShell

        return Gtk, GLib, GtkLayerShell

    def _build_window(self) -> None:
        Gtk = self.Gtk
        layer_shell = self.GtkLayerShell

        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_name("notch-voice-assistant")
        self.window.set_title("Claude Voice Assistant")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_default_size(640, 480)
        self.window.set_size_request(640, 380)
        self.window.connect("delete-event", self._on_delete)
        self.window.connect("key-press-event", self._on_key_press)

        layer_shell.init_for_window(self.window)
        layer_shell.set_namespace(self.window, "notch-voice-assistant")
        layer_shell.set_layer(self.window, layer_shell.Layer.OVERLAY)
        layer_shell.set_anchor(self.window, layer_shell.Edge.TOP, True)
        layer_shell.set_margin(self.window, layer_shell.Edge.TOP, 39)
        layer_shell.set_keyboard_mode(self.window, layer_shell.KeyboardMode.ON_DEMAND)
        layer_shell.set_exclusive_zone(self.window, 0)

        self._load_css()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.get_style_context().add_class("assistant-card")
        self.window.add(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.get_style_context().add_class("assistant-header")
        root.pack_start(header, False, False, 0)

        self.status_dot = Gtk.Label(label="●")
        self.status_dot.get_style_context().add_class("status-dot")
        header.pack_start(self.status_dot, False, False, 0)

        self.status_label = Gtk.Label(label="Ready", xalign=0)
        self.status_label.get_style_context().add_class("status-label")
        header.pack_start(self.status_label, True, True, 0)

        self.model_combo = Gtk.ComboBoxText()
        self.model_combo.append("sonnet", "Sonnet")
        self.model_combo.append("opus", "Opus")
        self.model_combo.set_active_id(self.preferences.model)
        self.model_combo.set_tooltip_text("Claude model for the next turn")
        self.model_combo.connect("changed", self._on_model_changed)
        header.pack_start(self.model_combo, False, False, 0)

        self.mute_button = Gtk.ToggleButton(label="󰝟" if self.preferences.muted else "")
        self.mute_button.set_active(self.preferences.muted)
        self.mute_button.set_tooltip_text("Mute spoken replies")
        self.mute_button.connect("toggled", self._on_mute_toggled)
        header.pack_start(self.mute_button, False, False, 0)

        new_button = Gtk.Button(label="New")
        new_button.set_tooltip_text("Start a new Claude conversation")
        new_button.connect("clicked", lambda *_: self.new_session())
        header.pack_start(new_button, False, False, 0)

        close_button = Gtk.Button(label="×")
        close_button.set_tooltip_text("Stop and close")
        close_button.connect("clicked", lambda *_: self.stop_and_hide())
        header.pack_start(close_button, False, False, 0)

        transcript_scroll = Gtk.ScrolledWindow()
        transcript_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        transcript_scroll.set_min_content_height(270)
        transcript_scroll.set_max_content_height(390)
        transcript_scroll.get_style_context().add_class("transcript-scroll")
        root.pack_start(transcript_scroll, True, True, 0)

        self.transcript = Gtk.TextView()
        self.transcript.set_name("voice-transcript")
        self.transcript.set_editable(False)
        self.transcript.set_cursor_visible(False)
        self.transcript.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.transcript.set_left_margin(16)
        self.transcript.set_right_margin(16)
        self.transcript.set_top_margin(14)
        self.transcript.set_bottom_margin(14)
        transcript_scroll.add(self.transcript)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        controls.set_halign(Gtk.Align.CENTER)
        controls.get_style_context().add_class("assistant-controls")
        root.pack_start(controls, False, False, 0)

        self.action_button = Gtk.Button(label="󰍬  Listen")
        self.action_button.set_size_request(150, 44)
        self.action_button.get_style_context().add_class("primary-action")
        self.action_button.connect("clicked", self._on_action_clicked)
        controls.pack_start(self.action_button, False, False, 0)

        self.continue_button = Gtk.Button(label="Continue in terminal")
        self.continue_button.set_no_show_all(True)
        self.continue_button.connect("clicked", lambda *_: self.continue_in_terminal())
        controls.pack_start(self.continue_button, False, False, 0)

        self._render_transcript()
        self._set_state(AssistantState.IDLE, "Ready")

    def _load_css(self) -> None:
        if not STYLE_PATH.is_file():
            return
        provider = self.Gtk.CssProvider()
        provider.load_from_path(str(STYLE_PATH))
        screen = self.window.get_screen()
        self.Gtk.StyleContext.add_provider_for_screen(
            screen,
            provider,
            self.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def run(self) -> int:
        prepare_runtime_dir()
        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
        self._start_control_server()
        self.GLib.timeout_add_seconds(30, self._idle_check)
        self.GLib.unix_signal_add(self.GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._quit)
        self.GLib.unix_signal_add(self.GLib.PRIORITY_DEFAULT, signal.SIGINT, self._quit)
        self.Gtk.main()
        self._cleanup()
        return 0

    def _start_control_server(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o600)
        server.listen(4)
        server.settimeout(1)
        self.control_socket = server

        def serve() -> None:
            while self.control_socket is server:
                try:
                    connection, _address = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with connection:
                    try:
                        request = json.loads(connection.recv(4096).decode("utf-8"))
                        command = str(request.get("command", ""))
                        self.GLib.idle_add(self.handle_command, command)
                        connection.sendall(b'{"ok":true}\n')
                    except (json.JSONDecodeError, OSError):
                        pass

        self.control_thread = threading.Thread(target=serve, name="voice-control", daemon=True)
        self.control_thread.start()

    def handle_command(self, command: str) -> bool:
        self.last_activity = time.monotonic()
        if command in {"toggle", "open"}:
            if self.window.get_visible():
                self.stop_and_hide()
            else:
                self.show_and_listen()
        elif command == "stop":
            self.stop_loop()
        elif command == "new-session":
            self.new_session()
        elif command == "quit":
            self._quit()
        return False

    def show_and_listen(self) -> None:
        self.loop_active = True
        self.continue_button.hide()
        self.window.show_all()
        self.continue_button.hide()
        self.window.present()
        self._write_status()
        self.start_listening()

    def start_listening(self) -> None:
        if not self.loop_active:
            return
        self.last_activity = time.monotonic()
        self.generation += 1
        generation = self.generation
        self.speech.stop_playback()
        self._set_state(AssistantState.LISTENING, "Listening…")

        try:
            self.recorder = VoiceRecorder(
                lambda pcm: self.GLib.idle_add(self._recording_finished, generation, pcm),
                lambda error: self.GLib.idle_add(self._operation_error, generation, error),
            )
            self.recorder.start()
        except AudioUnavailable as error:
            self.recorder = None
            self._operation_error(generation, str(error))

    def _recording_finished(self, generation: int, pcm_bytes: bytes) -> bool:
        if generation != self.generation or not self.loop_active:
            return False
        self.recorder = None
        if not pcm_bytes:
            self._set_state(AssistantState.IDLE, "No speech detected")
            self.loop_active = False
            return False

        self._set_state(AssistantState.TRANSCRIBING, "Transcribing locally…")

        def transcribe() -> None:
            try:
                transcript = self.speech.transcribe(pcm_bytes)
                self.GLib.idle_add(self._transcription_ready, generation, transcript)
            except Exception as error:
                self.GLib.idle_add(self._operation_error, generation, str(error))

        threading.Thread(target=transcribe, name="voice-transcribe", daemon=True).start()
        return False

    def _transcription_ready(self, generation: int, text: str) -> bool:
        if generation != self.generation or not self.loop_active:
            return False
        if not text:
            self._operation_error(generation, "I could not make out any speech.")
            return False

        self.preferences.messages.append({"role": "user", "text": text})
        self.preferences.messages = self.preferences.messages[-12:]
        self.preferences.save()
        self.current_reply = ""
        self._render_transcript(pending_reply=True)
        self._set_state(AssistantState.THINKING, "Claude is thinking…")

        model = self.preferences.model
        session_id = self.preferences.session_id

        def run_claude() -> None:
            result = self.claude.run(
                text,
                model=model,
                session_id=session_id,
                on_delta=lambda delta: self.GLib.idle_add(
                    self._reply_delta, generation, delta
                ),
                on_tool=lambda tool: self.GLib.idle_add(
                    self._tool_started, generation, tool
                ),
                on_retry=lambda message: self.GLib.idle_add(
                    self._retry_status, generation, message
                ),
            )
            self.GLib.idle_add(self._claude_finished, generation, result)

        threading.Thread(target=run_claude, name="voice-claude", daemon=True).start()
        return False

    def _reply_delta(self, generation: int, delta: str) -> bool:
        if generation != self.generation:
            return False
        self.current_reply += delta
        self._render_transcript(pending_reply=True)
        return False

    def _tool_started(self, generation: int, tool_name: str) -> bool:
        if generation != self.generation:
            return False
        spoken_status = friendly_tool_status(tool_name)
        self._set_state(AssistantState.ACTING, f"Using {tool_name}…")
        now = time.monotonic()
        if (
            not self.preferences.muted
            and now - self.last_tool_spoken_at >= self.TOOL_SPEECH_INTERVAL
        ):
            self.last_tool_spoken_at = now
            threading.Thread(
                target=self._speak_status,
                args=(generation, spoken_status),
                name="voice-tool-status",
                daemon=True,
            ).start()
        return False

    def _speak_status(self, generation: int, text: str) -> None:
        try:
            degraded = self.speech.speak(text)
            if degraded:
                self.GLib.idle_add(self._set_degraded, generation, degraded)
        except Exception:
            pass

    def _retry_status(self, generation: int, message: str) -> bool:
        if generation == self.generation:
            self._set_state(AssistantState.THINKING, message)
        return False

    def _claude_finished(self, generation: int, result: ClaudeResult) -> bool:
        if generation != self.generation or not self.loop_active:
            return False
        if result.session_id:
            self.preferences.session_id = result.session_id

        if result.error:
            self.preferences.save()
            self._set_state(AssistantState.ERROR, result.error)
            self.loop_active = False
            if result.permission_blocked and result.session_id:
                self.continue_button.show()
            return False

        reply = result.text.strip() or self.current_reply.strip()
        self.current_reply = ""
        if reply:
            self.preferences.messages.append({"role": "assistant", "text": reply})
            self.preferences.messages = self.preferences.messages[-12:]
        self.preferences.save()
        self._render_transcript()

        if self.preferences.muted or not reply:
            self._set_state(AssistantState.SPEAKING, "Reply ready · audio muted")
            self.GLib.timeout_add(400, self._resume_after_speech, generation)
            return False

        self._set_state(AssistantState.SPEAKING, "Speaking…")
        speech_text = sanitize_for_speech(reply)

        def speak_final() -> None:
            try:
                degraded = self.speech.speak(speech_text)
                self.GLib.idle_add(self._speech_finished, generation, degraded)
            except Exception as error:
                self.GLib.idle_add(self._speech_failed, generation, str(error))

        threading.Thread(target=speak_final, name="voice-final", daemon=True).start()
        return False

    def _speech_finished(self, generation: int, degraded: str | None) -> bool:
        if generation != self.generation:
            return False
        if degraded:
            self.degraded = degraded
        self.GLib.timeout_add(400, self._resume_after_speech, generation)
        return False

    def _speech_failed(self, generation: int, error: str) -> bool:
        if generation != self.generation:
            return False
        self._set_state(AssistantState.ERROR, error)
        self.loop_active = False
        return False

    def _resume_after_speech(self, generation: int) -> bool:
        if generation == self.generation and self.loop_active and self.window.get_visible():
            self.start_listening()
        return False

    def _operation_error(self, generation: int, error: str) -> bool:
        if generation == self.generation:
            self.loop_active = False
            self._set_state(AssistantState.ERROR, error)
        return False

    def _set_degraded(self, generation: int, degraded: str) -> bool:
        if generation == self.generation:
            self.degraded = degraded
            self._write_status()
        return False

    def _on_action_clicked(self, _button) -> None:
        if self.state == AssistantState.LISTENING and self.recorder:
            self.recorder.finish(submit=True)
        elif self.state in {
            AssistantState.TRANSCRIBING,
            AssistantState.THINKING,
            AssistantState.ACTING,
            AssistantState.SPEAKING,
        }:
            self.stop_loop()
        else:
            self.loop_active = True
            self.start_listening()

    def stop_loop(self) -> None:
        self.loop_active = False
        self.generation += 1
        if self.recorder:
            recorder = self.recorder
            self.recorder = None
            recorder.finish(submit=False)
        self.claude.cancel()
        self.speech.stop_playback()
        self._set_state(AssistantState.IDLE, "Stopped")

    def stop_and_hide(self) -> None:
        self.stop_loop()
        self.window.hide()
        self._write_status()

    def new_session(self) -> None:
        self.stop_loop()
        self.preferences.reset_conversation()
        self.current_reply = ""
        self.continue_button.hide()
        self._render_transcript()
        self._set_state(AssistantState.IDLE, "New conversation")
        if self.window.get_visible():
            self.loop_active = True
            self.GLib.timeout_add(250, self._begin_if_visible)

    def _begin_if_visible(self) -> bool:
        if self.window.get_visible() and self.loop_active:
            self.start_listening()
        return False

    def continue_in_terminal(self) -> None:
        session_id = self.preferences.session_id
        if not session_id:
            return
        subprocess.Popen(
            [
                "kitty",
                "--detach",
                "--title",
                "Claude Voice Assistant",
                str(CLAUDE_BIN),
                "--resume",
                session_id,
            ],
            cwd=HOME_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.stop_and_hide()

    def _on_model_changed(self, combo) -> None:
        model = combo.get_active_id()
        if model in {"sonnet", "opus"}:
            self.preferences.model = model
            self.preferences.save()

    def _on_mute_toggled(self, button) -> None:
        self.preferences.muted = bool(button.get_active())
        button.set_label("󰝟" if self.preferences.muted else "")
        self.preferences.save()
        if self.preferences.muted:
            self.speech.stop_playback()

    def _on_delete(self, *_args) -> bool:
        self.stop_and_hide()
        return True

    def _on_key_press(self, _window, event) -> bool:
        if event.keyval == 65307:  # Escape
            self.stop_and_hide()
            return True
        return False

    def _set_state(self, state: AssistantState, detail: str) -> None:
        self.state = state
        self.detail = detail
        self.last_activity = time.monotonic()
        if hasattr(self, "status_label"):
            self.status_label.set_text(detail)
            context = self.status_dot.get_style_context()
            for state_name in AssistantState:
                context.remove_class(state_name.value)
            context.add_class(state.value)
            self._update_action_button()
        self._write_status()

    def _update_action_button(self) -> None:
        labels = {
            AssistantState.IDLE: "󰍬  Listen",
            AssistantState.LISTENING: "󰓛  Send",
            AssistantState.TRANSCRIBING: "󰓛  Stop",
            AssistantState.THINKING: "󰓛  Stop",
            AssistantState.ACTING: "󰓛  Stop",
            AssistantState.SPEAKING: "󰍬  Interrupt",
            AssistantState.ERROR: "󰍬  Try again",
        }
        self.action_button.set_label(labels[self.state])

    def _render_transcript(self, *, pending_reply: bool = False) -> None:
        lines: list[str] = []
        if not self.preferences.messages:
            lines.append(
                "Click the microphone and speak naturally. Claude can use your "
                "existing tools and Linux-helper plugin."
            )
        else:
            for message in self.preferences.messages[-8:]:
                role = "You" if message["role"] == "user" else "Claude"
                lines.append(f"{role}\n{message['text'].strip()}")
        if pending_reply:
            lines.append(f"Claude\n{self.current_reply or '…'}")

        buffer = self.transcript.get_buffer()
        buffer.set_text("\n\n".join(lines))
        end = buffer.get_end_iter()
        self.transcript.scroll_to_iter(end, 0.0, False, 0.0, 1.0)

    def _write_status(self) -> None:
        prepare_runtime_dir()
        payload = status_payload(
            self.state,
            self.detail,
            visible=getattr(self, "window", None) is not None and self.window.get_visible(),
            degraded=self.degraded,
        )
        atomic_json_write(STATUS_PATH, payload, mode=0o600)

    def _idle_check(self) -> bool:
        if (
            not self.window.get_visible()
            and self.state == AssistantState.IDLE
            and time.monotonic() - self.last_activity >= self.IDLE_EXIT_SECONDS
        ):
            self._quit()
            return False
        return True

    def _quit(self) -> bool:
        self.stop_loop()
        self.Gtk.main_quit()
        return False

    def _cleanup(self) -> None:
        if self.control_socket:
            server = self.control_socket
            self.control_socket = None
            server.close()
        for path in (SOCKET_PATH, STATUS_PATH):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
