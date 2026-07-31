# Notch Voice Assistant

An on-demand Claude voice assistant that grows out of the center notch on a
Waybar/Niri desktop.

The
[`prototype-v0`](https://github.com/Maceface2/notch-voice-assistant/tree/prototype-v0)
tag captures the original Nobara workstation prototype. The main branch is the
portable iteration. Clicking the white Claude starburst:

1. Expands the center notch into a GTK layer-shell panel.
2. Records speech through PipeWire and transcribes it locally with
   faster-whisper.
3. Sends the request to Claude Code.
4. Streams the reply from ElevenLabs directly into the audio player.
5. Listens for the next turn until stopped or closed.

There is no wake word or background recording. Recorded audio stays in memory.
Transcription remains local; reply text is sent to ElevenLabs for speech
generation.

## Features

- Animated full-width notch expansion with filled rounded-corner seams
- Voice activity detection and automatic end-of-utterance detection
- CUDA `distil-large-v3` transcription with a CPU `small.en` fallback
- Persistent Claude Code sessions with Sonnet and Opus selection
- ElevenLabs Flash v2.5 streaming for low first-audio latency
- Account voice discovery and on-demand voice switching
- eSpeak fallback when ElevenLabs or the network is unavailable
- Waybar indicators for listening, transcribing, thinking, acting, and speaking
- On-demand systemd user service with a ten-minute hidden-idle timeout

## ElevenLabs setup

Create an API key in ElevenLabs, then run the hidden-input configurator:

```sh
notch-voice-assistant configure-elevenlabs
```

The command validates the key, lists the voices available to the account, and
asks which voice to use. It stores the key with mode `0600` in
`~/.config/notch-voice-assistant/elevenlabs.json`.

List voices or switch later:

```sh
notch-voice-assistant voices
notch-voice-assistant set-voice VOICE_ID
```

The defaults are the `eleven_flash_v2_5` low-latency model and
`mp3_22050_32`, which avoids paid-tier PCM requirements and minimizes transfer
and decoder startup time. Environment variables can override saved settings:

```sh
ELEVENLABS_API_KEY=... \
ELEVENLABS_VOICE_ID=... \
ELEVENLABS_MODEL_ID=eleven_flash_v2_5 \
notch-voice-assistant daemon
```

ElevenLabs is a metered cloud service. Generation consumes account credits and
requires internet access.

## Controls

| Action | Result |
| --- | --- |
| Left-click Waybar starburst | Expand/collapse; opening immediately listens |
| Right-click Waybar starburst | Stop the conversational loop |
| Middle-click Waybar starburst | Start a new Claude session |
| **Listen** / **Stop** | Start or stop the loop |
| **Sonnet** / **Opus** | Choose the Claude model for the next turn |
| Speaker button | Mute or unmute replies |
| **New** | Clear the transcript and begin a new session |

## Requirements

- A Wayland compositor and Waybar; the UI is tested with Niri
- PipeWire
- Python 3 with GTK 3, GTK layer-shell, and GStreamer introspection bindings
- `ffmpeg`/`ffplay` and `espeak-ng`
- Claude Code on `PATH` or at `~/.local/bin/claude`
- An ElevenLabs account and API key
- An NVIDIA GPU is optional but recommended for local Whisper transcription

## Installation

```sh
git clone https://github.com/Maceface2/notch-voice-assistant.git
cd notch-voice-assistant
./install.sh
notch-voice-assistant configure-elevenlabs
```

Use `./install.sh --cpu-only` to skip NVIDIA Python packages and pin Whisper to
its CPU backend. Use `./install.sh --app-only` to update the application
without rebuilding the speech environment. An active daemon is restarted
after an update; an inactive daemon remains inactive.

Merge the files under [`integrations/waybar`](integrations/waybar) into the
Waybar configuration and stylesheet, then reload Waybar. The installer copies
`claude.png` into `~/.config/waybar`.

The service is not enabled at login. The first Waybar click starts it, and it
exits after ten hidden-idle minutes.

Whisper defaults to automatic CUDA with a runtime CPU fallback. Override it
with `NOTCH_VOICE_WHISPER_DEVICE=cpu` or
`NOTCH_VOICE_WHISPER_DEVICE=cuda`.

The panel assumes Waybar has an 8px transparent bottom margin and 8px lower
corner radii. Set `NOTCH_VOICE_TOP_OFFSET` for different bar geometry and
`NOTCH_VOICE_WIDTH` to override the default 446px width.

## Verification

```sh
notch-voice-assistant doctor
PYTHONPATH=local/lib \
  ~/.local/share/notch-voice-assistant/venv/bin/python \
  -m unittest discover -s tests -v
systemd-analyze --user verify \
  ~/.config/systemd/user/notch-voice-assistant.service
```

## Uninstallation

```sh
./uninstall.sh
```

This preserves downloaded Whisper models, the virtual environment, and session
state. Use `./uninstall.sh --purge-data` to remove those as well. Waybar
snippets are always left for manual removal.

Fish S2 files installed by earlier versions are deliberately not deleted
during an update. After confirming ElevenLabs works, the old cache under
`~/.local/share/notch-voice-assistant/fish-s2` and the Fish GGUF under
`~/.local/share/notch-voice-assistant/models/fish-s2` can be removed manually.

## Upstream services and assets

- [ElevenLabs Text to Speech](https://elevenlabs.io/docs/api-reference/text-to-speech/stream)
  provides streaming speech with
  [Flash v2.5](https://elevenlabs.io/docs/overview/models).
- The monochrome Claude starburst is adapted from the official
  [Claude website](https://claude.ai/) icon and remains an Anthropic trademark.

## Iteration status

- [x] Replace workstation-specific paths with runtime discovery
- [x] Add a repeatable installer and conservative uninstaller
- [x] Add a same-width animated notch expansion and Claude visual identity
- [x] Add local GPU transcription with CPU fallback
- [x] Replace local Fish synthesis with ElevenLabs Flash streaming
- [ ] Formalize Python packaging and dependency metadata
- [ ] Add barge-in so speaking can be interrupted naturally
- [ ] Expand state-machine and failure-path tests
