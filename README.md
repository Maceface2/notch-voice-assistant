# Notch Voice Assistant

An on-demand Claude voice assistant that lives in the center notch of a
Waybar/Niri desktop.

The [`prototype-v0`](https://github.com/Maceface2/notch-voice-assistant/tree/prototype-v0)
tag captures the original working Nobara workstation prototype. The main
branch is the portable first iteration. Clicking the Anthropic mark in Waybar
slides a top-center GTK layer-shell panel directly out of the notch and starts
a conversational loop:

1. PipeWire records speech into memory.
2. faster-whisper transcribes locally.
3. Claude Code handles the request in Auto permission mode.
4. Coqui TTS speaks the response locally with an American English voice.
5. The assistant listens for the next turn until stopped or closed.

There is no wake word and no background recording. Audio is not written to
disk.

## Prototype features

- Animated GTK layer-shell panel visually attached to the Waybar notch
- Anthropic mark in Waybar and the panel header
- Voice-activity detection and automatic end-of-utterance detection
- CUDA `distil-large-v3` transcription with a CPU `small.en` fallback
- Persistent, resumable Claude Code sessions
- Sonnet by default with an Opus selector
- Spoken tool-status updates and final answers
- Persistent Coqui TTS worker using the American LJSpeech VITS voice
- eSpeak fallback if Coqui is unavailable
- Waybar state indicators for listening, transcribing, thinking, acting,
  speaking, and errors
- On-demand systemd user service with a ten-minute hidden-idle timeout

## Controls

| Action | Result |
| --- | --- |
| Left-click Waybar Anthropic mark | Expand/collapse; opening immediately listens |
| Right-click Waybar Anthropic mark | Stop the conversational loop |
| Middle-click Waybar Anthropic mark | Start a new Claude session |
| **Listen** / **Stop** in the popover | Start or stop the loop |
| **Sonnet** / **Opus** selector | Choose the model for the next turn |
| Speaker button | Mute or unmute speech |
| **New** | Clear the transcript and begin a new session |

## Requirements

- A Wayland compositor and Waybar; the current UI is tested with Niri
- PipeWire
- Python 3 with GTK 3, GTK layer-shell, and GStreamer introspection bindings
- `ffmpeg`, `aplay`, and `espeak-ng`
- Claude Code available on `PATH` or at `~/.local/bin/claude`
- Optional NVIDIA CUDA acceleration

Distribution packages provide the GTK, layer-shell, GStreamer, and PipeWire
bindings. The main Python speech stack is installed in a user-local virtual
environment. Because the official Coqui package supports Python 3.9 through
3.11, the installer uses `uv` to create a separate managed Python 3.11
environment for its persistent synthesis worker.

## Installation

Clone the repository and run the installer:

```sh
git clone https://github.com/Maceface2/notch-voice-assistant.git
cd notch-voice-assistant
./install.sh
```

Use `./install.sh --cpu-only` to skip the NVIDIA Python runtime and pin Whisper
to its CPU backend. `./install.sh --app-only` updates just the application
files without recreating or downloading the speech environment.

Merge the snippets under [`integrations/waybar`](integrations/waybar) into the
Waybar configuration and stylesheet, then reload Waybar. The installer copies
`anthropic.png` into `~/.config/waybar` for the relative CSS image reference.

The service is not enabled at login. The first Waybar click starts it, and it
exits after ten hidden-idle minutes.

Whisper backend selection defaults to automatic CUDA with a runtime CPU
fallback. It can also be overridden per launch with
`NOTCH_VOICE_WHISPER_DEVICE=cpu` or `NOTCH_VOICE_WHISPER_DEVICE=cuda`.
The panel assumes Waybar has an 8px transparent bottom margin; set
`NOTCH_VOICE_TOP_OFFSET` if a different bar geometry needs another overlap.

## Verification

```sh
~/.local/bin/notch-voice-assistant doctor
PYTHONPATH=local/lib \
  ~/.local/share/notch-voice-assistant/venv/bin/python \
  -m unittest discover -s tests -v
systemd-analyze --user verify ~/.config/systemd/user/notch-voice-assistant.service
```

## Uninstallation

```sh
./uninstall.sh
```

This preserves downloaded models, the virtual environment, and session state.
Use `./uninstall.sh --purge-data` to remove those as well. Waybar snippets are
always left for manual removal.

## Upstream projects and assets

- [Coqui TTS](https://github.com/coqui-ai/TTS) provides synthesis. The selected
  `tts_models/en/ljspeech/vits` model is Coqui's single-speaker American English
  LJSpeech VITS release.
- The Anthropic mark is sourced from Anthropic's
  [verified GitHub organization](https://github.com/anthropics) and remains an
  Anthropic trademark.

## Iteration status

- [x] Replace workstation-specific paths with runtime discovery
- [x] Add a repeatable installer and conservative uninstaller
- [x] Improve model setup and GPU/CPU fallback behavior
- [x] Add animated notch attachment and Anthropic visual identity
- [x] Replace Piper with an isolated persistent Coqui American voice
- [ ] Formalize Python packaging and dependency metadata
- [ ] Add barge-in so speaking can be interrupted naturally
- [ ] Expand state-machine and failure-path tests
