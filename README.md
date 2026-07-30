# Notch Voice Assistant

An on-demand Claude voice assistant that lives in the center notch of a
Waybar/Niri desktop.

This repository currently captures the working first prototype from Mason's
Nobara workstation. Clicking the Waybar microphone opens a top-center GTK
layer-shell popover and starts a conversational loop:

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

## Current target

The prototype is configured for:

- Nobara Linux with Niri and Waybar
- PipeWire
- Python 3.14 with GTK 3 and GStreamer introspection bindings
- NVIDIA CUDA acceleration
- Claude Code installed at `~/.local/bin/claude`

The launcher and service intentionally preserve the absolute paths from the
working workstation snapshot. Making installation portable is an iteration-one
task.

## Prototype installation

Install the tracked files:

```sh
mkdir -p ~/.local/lib ~/.local/bin ~/.config/notch-voice-assistant \
  ~/.config/systemd/user
cp -r local/lib/notch_voice_assistant ~/.local/lib/
cp local/bin/notch-voice-assistant ~/.local/bin/
cp config/notch-voice-assistant/style.css ~/.config/notch-voice-assistant/
cp config/systemd/user/notch-voice-assistant.service ~/.config/systemd/user/
chmod +x ~/.local/bin/notch-voice-assistant
```

Create the user-local speech environment and preload the models:

```sh
python3 -m venv --system-site-packages \
  ~/.local/share/notch-voice-assistant/venv
~/.local/share/notch-voice-assistant/venv/bin/pip install \
  'piper-tts==1.5.0' \
  'faster-whisper==1.2.1' \
  'webrtcvad-wheels==2.0.14' \
  'nvidia-cublas-cu12' \
  'nvidia-cudnn-cu12==9.*'
~/.local/share/notch-voice-assistant/venv/bin/hf download \
  Systran/faster-distil-whisper-large-v3 \
  --cache-dir ~/.local/share/notch-voice-assistant/models/huggingface
~/.local/share/notch-voice-assistant/venv/bin/hf download \
  Systran/faster-whisper-small.en \
  --cache-dir ~/.local/share/notch-voice-assistant/models/huggingface
~/.local/share/notch-voice-assistant/venv/bin/python \
  -m piper.download_voices \
  --data-dir ~/.local/share/notch-voice-assistant/models \
  en_US-lessac-medium
systemctl --user daemon-reload
```

Merge the snippets under [`integrations/waybar`](integrations/waybar) into the
Waybar configuration and stylesheet, then reload Waybar.

## Verification

```sh
~/.local/bin/notch-voice-assistant doctor
PYTHONPATH=local/lib \
  ~/.local/share/notch-voice-assistant/venv/bin/python \
  -m unittest discover -s tests -v
systemd-analyze --user verify \
  ~/.config/systemd/user/notch-voice-assistant.service
```

The user service is not enabled at login. The first Waybar click starts it.

## Iteration-one direction

- Replace workstation-specific paths with runtime discovery
- Add a repeatable installer and uninstaller
- Formalize Python packaging and dependency metadata
- Improve first-run model setup and GPU/CPU selection
- Add barge-in so speaking can be interrupted naturally
- Expand state-machine and failure-path tests
