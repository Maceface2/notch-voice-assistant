#!/usr/bin/env bash

set -euo pipefail

readonly APP_NAME="notch-voice-assistant"
readonly SOURCE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly USER_HOME="${HOME:?HOME must be set}"
readonly CONFIG_HOME="${XDG_CONFIG_HOME:-${USER_HOME}/.config}"
readonly DATA_HOME="${XDG_DATA_HOME:-${USER_HOME}/.local/share}"
readonly LOCAL_BIN="${USER_HOME}/.local/bin"
readonly LOCAL_LIB="${USER_HOME}/.local/lib"
readonly STAGING_ROOT="${DESTDIR:-}"

app_only=false
cpu_only=false

usage() {
    cat <<'EOF'
Usage: ./install.sh [--app-only] [--cpu-only]

  --app-only  Install application files without creating the speech venv.
  --cpu-only  Skip NVIDIA runtime packages and use Whisper's CPU fallback.

Set DESTDIR to stage files without touching the user service or downloading
dependencies.
EOF
}

while (( $# > 0 )); do
    case "$1" in
        --app-only)
            app_only=true
            ;;
        --cpu-only)
            cpu_only=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

staged_path() {
    printf '%s%s' "$STAGING_ROOT" "$1"
}

install_application() {
    local package_target
    package_target="$(staged_path "${LOCAL_LIB}/notch_voice_assistant")"

    install -d \
        "$package_target" \
        "$(staged_path "$LOCAL_BIN")" \
        "$(staged_path "${CONFIG_HOME}/${APP_NAME}")" \
        "$(staged_path "${CONFIG_HOME}/waybar")" \
        "$(staged_path "${CONFIG_HOME}/systemd/user")" \
        "$(staged_path "${DATA_HOME}/${APP_NAME}/fish-s2")"

    install -m 0644 \
        "${SOURCE_ROOT}"/local/lib/notch_voice_assistant/*.py \
        "$package_target/"
    rm -f -- \
        "${package_target}/coqui.py" \
        "${package_target}/coqui_worker.py"
    install -m 0755 \
        "${SOURCE_ROOT}/local/bin/${APP_NAME}" \
        "$(staged_path "${LOCAL_BIN}/${APP_NAME}")"
    install -m 0644 \
        "${SOURCE_ROOT}/config/notch-voice-assistant/style.css" \
        "$(staged_path "${CONFIG_HOME}/${APP_NAME}/style.css")"
    install -m 0644 \
        "${SOURCE_ROOT}/assets/claude.png" \
        "$(staged_path "${CONFIG_HOME}/${APP_NAME}/claude.png")"
    install -m 0644 \
        "${SOURCE_ROOT}/assets/claude.png" \
        "$(staged_path "${CONFIG_HOME}/waybar/claude.png")"
    install -m 0644 \
        "${SOURCE_ROOT}/assets/fish-s2-american-reference.wav" \
        "$(staged_path "${DATA_HOME}/${APP_NAME}/fish-s2/american-reference.wav")"
    install -m 0644 \
        "${SOURCE_ROOT}/config/systemd/user/${APP_NAME}.service" \
        "$(staged_path "${CONFIG_HOME}/systemd/user/${APP_NAME}.service")"
}

install_speech_stack() {
    local venv="${DATA_HOME}/${APP_NAME}/venv"
    local models="${DATA_HOME}/${APP_NAME}/models"
    local -a packages=(
        "faster-whisper==1.2.1"
        "webrtcvad-wheels==2.0.14"
        "uv"
    )

    if [[ "$cpu_only" == false ]] && command -v nvidia-smi >/dev/null 2>&1; then
        packages+=("nvidia-cublas-cu12" "nvidia-cudnn-cu12==9.*")
    fi

    python3 -m venv --system-site-packages "$venv"
    "$venv/bin/pip" install "${packages[@]}"
    if [[ "$cpu_only" == false ]]; then
        "$venv/bin/hf" download \
            Systran/faster-distil-whisper-large-v3 \
            --cache-dir "$models"
    fi
    "$venv/bin/hf" download \
        Systran/faster-whisper-small.en \
        --cache-dir "$models"
    install_fish_s2_stack "$venv" "$models"
}

install_fish_s2_stack() {
    local assistant_venv="$1"
    local models="$2"
    local fish_root="${DATA_HOME}/${APP_NAME}/fish-s2"
    local source_dir="${fish_root}/source"
    local build_dir="${source_dir}/build-vulkan"
    local binary_dir="${fish_root}/bin"
    local model_dir="${models}/fish-s2"
    local voice_dir="${fish_root}/voices"
    local model_path="${model_dir}/s2-pro-q6_k.gguf"
    local tokenizer_path="${model_dir}/tokenizer.json"
    local reference_path="${fish_root}/american-reference.wav"
    local ready_path="${DATA_HOME}/${APP_NAME}/fish-s2-ready.json"
    local ready_temporary="${ready_path}.tmp"
    local ready_wav="${fish_root}/voice-ready.wav"
    local reference_text
    reference_text="Hello, I am your local voice assistant. I speak with a clear, calm American English voice. I can help you plan, build, explore, and understand whatever is on your mind. Whenever you are ready, just ask me a question."

    install -d "$binary_dir" "$model_dir" "$voice_dir"

    if [[ ! -x "${binary_dir}/s2" ]]; then
        for command in cmake git glslc; do
            if ! command -v "$command" >/dev/null 2>&1; then
                printf 'Fish Audio S2 Pro needs %s to build s2.cpp with Vulkan.\n' "$command" >&2
                printf 'On Fedora: sudo dnf install cmake git glslc spirv-headers-devel vulkan-headers vulkan-loader-devel\n' >&2
                return 1
            fi
        done

        if [[ ! -d "${source_dir}/.git" ]]; then
            git clone --recurse-submodules \
                https://github.com/rodrigomatta/s2.cpp.git \
                "$source_dir"
        else
            git -C "$source_dir" submodule update --init --recursive
        fi

        cmake -S "$source_dir" -B "$build_dir" \
            -DCMAKE_BUILD_TYPE=Release \
            -DS2_VULKAN=ON
        cmake --build "$build_dir" --parallel "$(nproc)"
        install -m 0755 "${build_dir}/s2" "${binary_dir}/s2"
    fi

    "$assistant_venv/bin/hf" download \
        rodrigomt/s2-pro-gguf \
        s2-pro-q6_k.gguf \
        tokenizer.json \
        --local-dir "$model_dir"

    if [[ ! -f "${voice_dir}/notch-voice.s2voice" ]]; then
        "${binary_dir}/s2" \
            --model "$model_path" \
            --tokenizer "$tokenizer_path" \
            --prompt-audio "$reference_path" \
            --prompt-text "$reference_text" \
            --voice notch-voice \
            --voice-dir "$voice_dir" \
            --save-voice \
            --text "Fish Audio S2 Pro is ready." \
            --output "$ready_wav" \
            --vulkan 0 \
            --log-level warn
    fi
    printf '{"runtime":"s2.cpp","model":"s2-pro-q6_k.gguf","voice":"notch-voice"}\n' \
        >"$ready_temporary"
    mv -- "$ready_temporary" "$ready_path"
}

warn_for_missing_system_dependencies() {
    local command
    local -a missing=()
    for command in claude ffmpeg aplay espeak-ng; do
        command -v "$command" >/dev/null 2>&1 || missing+=("$command")
    done
    if (( ${#missing[@]} > 0 )); then
        printf 'Warning: install these system commands before use: %s\n' "${missing[*]}" >&2
    fi
}

install_application

if [[ -n "$STAGING_ROOT" ]]; then
    printf 'Staged %s under %s\n' "$APP_NAME" "$STAGING_ROOT"
    exit 0
fi

if [[ "$app_only" == false ]]; then
    install_speech_stack
    if [[ "$cpu_only" == true ]]; then
        printf 'cpu\n' >"${CONFIG_HOME}/${APP_NAME}/whisper-device"
    else
        printf 'auto\n' >"${CONFIG_HOME}/${APP_NAME}/whisper-device"
    fi
elif [[ "$cpu_only" == true ]]; then
    printf 'cpu\n' >"${CONFIG_HOME}/${APP_NAME}/whisper-device"
fi

systemctl --user daemon-reload
warn_for_missing_system_dependencies

printf '\nInstalled %s.\n' "$APP_NAME"
printf 'Merge integrations/waybar/module.jsonc and style.css into Waybar, then run:\n'
printf '  %s doctor\n' "${LOCAL_BIN}/${APP_NAME}"
