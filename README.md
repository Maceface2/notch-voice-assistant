# Notch Voice Assistant

An on-demand Claude voice assistant that lives in the center notch of a
Waybar/Niri desktop.

The [`prototype-v0`](https://github.com/Maceface2/notch-voice-assistant/tree/prototype-v0)
tag captures the original working Nobara workstation prototype. The main
branch is the portable first iteration. Clicking the Claude starburst in Waybar
grows the center notch into a top-center GTK layer-shell panel and starts
a conversational loop:

1. PipeWire records speech into memory.
2. faster-whisper transcribes locally.
3. Claude Code handles the request in Auto permission mode.
4. Fish Audio S2 Pro speaks the response locally with a consistent American
   English voice.
5. The assistant listens for the next turn until stopped or closed.

There is no wake word and no background recording. Audio is not written to
disk.

## Prototype features

- Animated GTK layer-shell panel that expands the Waybar notch as one rectangle
- Claude starburst in Waybar and the panel header
- Voice-activity detection and automatic end-of-utterance detection
- CUDA `distil-large-v3` transcription with a CPU `small.en` fallback
- Persistent, resumable Claude Code sessions
- Sonnet by default with an Opus selector
- Spoken tool-status updates and final answers
- Persistent Fish Audio S2 Pro Q6_K server using the Vulkan `s2.cpp` runtime
- Reusable local voice profile with an American English default
- eSpeak fallback if Fish Audio S2 Pro is unavailable
- Waybar state indicators for listening, transcribing, thinking, acting,
  speaking, and errors
- On-demand systemd user service with a ten-minute hidden-idle timeout

## Controls

| Action | Result |
| --- | --- |
| Left-click Waybar Claude starburst | Expand/collapse; opening immediately listens |
| Right-click Waybar Claude starburst | Stop the conversational loop |
| Middle-click Waybar Claude starburst | Start a new Claude session |
| **Listen** / **Stop** in the popover | Start or stop the loop |
| **Sonnet** / **Opus** selector | Choose the model for the next turn |
| Speaker button | Mute or unmute speech |
| **New** | Clear the transcript and begin a new session |

Change the cloned voice with a clean 5–30 second WAV or MP3 and its exact
transcript:

```sh
notch-voice-assistant set-voice ./reference.wav \
  "The exact words spoken in the reference clip."
```

The command creates the profile atomically and writes a
`voice-preview.wav` beside it. Restore the bundled American voice with
`notch-voice-assistant reset-voice`.

## Requirements

- A Wayland compositor and Waybar; the current UI is tested with Niri
- PipeWire
- Python 3 with GTK 3, GTK layer-shell, and GStreamer introspection bindings
- `ffmpeg`, `aplay`, and `espeak-ng`
- `git`, CMake, `glslc`, SPIR-V/Vulkan headers, and Vulkan loader development
  files
- Claude Code available on `PATH` or at `~/.local/bin/claude`
- A Vulkan-capable GPU; the selected Q6_K model needs roughly 8–9GB VRAM

Distribution packages provide the GTK, layer-shell, GStreamer, and PipeWire
bindings. The main Python speech stack is installed in a user-local virtual
environment. Fish Audio synthesis runs in the separately built native
`s2.cpp` engine, so it does not require another Python or PyTorch environment.

On Fedora, install the native build requirements with:

```sh
sudo dnf install cmake git glslc spirv-headers-devel vulkan-headers vulkan-loader-devel
```

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

The default Fish model is the 4.5GB `s2-pro-q6_k.gguf` quantization. The model
weights use the Fish Audio Research License: research and non-commercial use
is permitted, while commercial use requires a separate Fish Audio license.

Merge the snippets under [`integrations/waybar`](integrations/waybar) into the
Waybar configuration and stylesheet, then reload Waybar. The installer copies
`claude.png` into `~/.config/waybar` for the relative CSS image reference.

The service is not enabled at login. The first Waybar click starts it, and it
exits after ten hidden-idle minutes.

Whisper backend selection defaults to automatic CUDA with a runtime CPU
fallback. It can also be overridden per launch with
`NOTCH_VOICE_WHISPER_DEVICE=cpu` or `NOTCH_VOICE_WHISPER_DEVICE=cuda`.
Fish S2 uses Vulkan device 0 by default. Override it with
`NOTCH_VOICE_FISH_S2_BACKEND=cpu`, `vulkan`, or `cuda` when using a binary
built for that backend.
The panel assumes Waybar has an 8px transparent bottom margin and 8px lower
corner radii. Its animated overlap fills those rounded corners only while the
panel is open. Set
`NOTCH_VOICE_TOP_OFFSET` if a different bar geometry needs another overlap.
Its default width is 446px to match the reference center notch; set
`NOTCH_VOICE_WIDTH` when a different Waybar layout needs another width.

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

- [Fish Audio S2 Pro](https://huggingface.co/fishaudio/s2-pro) provides
  synthesis through the community [s2.cpp](https://github.com/rodrigomatta/s2.cpp)
  Vulkan engine and the linked
  [Q6_K GGUF](https://huggingface.co/rodrigomt/s2-pro-gguf).
- The bundled synthetic American English reference was generated once with
  [Coqui TTS](https://github.com/coqui-ai/TTS); Coqui is not installed or used
  at runtime.
- The monochrome Claude starburst is adapted from the official
  [Claude website](https://claude.ai/) icon and remains an Anthropic trademark.

## Iteration status

- [x] Replace workstation-specific paths with runtime discovery
- [x] Add a repeatable installer and conservative uninstaller
- [x] Improve model setup and GPU/CPU fallback behavior
- [x] Add a same-width animated notch expansion and Claude visual identity
- [x] Replace Coqui with persistent Fish Audio S2 Pro Q6_K synthesis
- [ ] Formalize Python packaging and dependency metadata
- [ ] Add barge-in so speaking can be interrupted naturally
- [ ] Expand state-machine and failure-path tests
