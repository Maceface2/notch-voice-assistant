# Notch Voice Assistant

An on-demand Claude voice assistant that lives in the center notch of a
Waybar/Niri desktop.

The [`prototype-v0`](https://github.com/Maceface2/notch-voice-assistant/tree/prototype-v0)
tag captures the original working Nobara workstation prototype. The main
branch is the portable first iteration. Clicking the Waybar microphone opens a
top-center GTK layer-shell popover and starts a conversational loop:

1. PipeWire records speech into memory.
2. faster-whisper transcribes locally.
3. Claude Code handles the request in Auto permission mode.
4. Piper speaks the response locally.
5. The assistant listens for the next turn until stopped or closed.

There is no wake word and no background recording. Audio is not written to
disk.

## Prototype features

- Top-center GTK layer-shell popover attached to Waybar
- Voice-activity detection and automatic end-of-utterance detection
- CUDA `distil-large-v3` transcription with a CPU `small.en` fallback
- Persistent, resumable Claude Code sessions
- Sonnet by default with an Opus selector
- Spoken tool-status updates and final answers
- Local Piper voice with an eSpeak fallback
- Waybar state indicators for listening, transcribing, thinking, acting,
  speaking, and errors
- On-demand systemd user service with a ten-minute hidden-idle timeout

## Controls

| Action | Result |
| --- | --- |
| Left-click Waybar microphone | Open/close; opening immediately listens |
| Right-click Waybar microphone | Stop the conversational loop |
| Middle-click Waybar microphone | Start a new Claude session |
| **Listen** / **Stop** in the popover | Start or stop the loop |
| **Sonnet** / **Opus** selector | Choose the model for the next turn |
| Speaker button | Mute or unmute speech |
| **New** | Clear the transcript and begin a new session |

## Requirements

- A Wayland compositor and Waybar; the current UI is tested with Niri
- PipeWire
- Python 3 with GTK 3, GTK layer-shell, and GStreamer introspection bindings
- `ffmpeg`, `aplay`, and optionally `espeak-ng`
- Claude Code available on `PATH` or at `~/.local/bin/claude`
- Optional NVIDIA CUDA acceleration

Distribution packages provide the GTK, layer-shell, GStreamer, and PipeWire
bindings. The Python speech stack and models are installed in a user-local
virtual environment.

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
Waybar configuration and stylesheet, then reload Waybar.

The service is not enabled at login. The first Waybar click starts it, and it
exits after ten hidden-idle minutes.

Whisper backend selection defaults to automatic CUDA with a runtime CPU
fallback. It can also be overridden per launch with
`NOTCH_VOICE_WHISPER_DEVICE=cpu` or `NOTCH_VOICE_WHISPER_DEVICE=cuda`.

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

## Iteration status

- [x] Replace workstation-specific paths with runtime discovery
- [x] Add a repeatable installer and conservative uninstaller
- [x] Improve model setup and GPU/CPU fallback behavior
- [ ] Formalize Python packaging and dependency metadata
- [ ] Add barge-in so speaking can be interrupted naturally
- [ ] Expand state-machine and failure-path tests
