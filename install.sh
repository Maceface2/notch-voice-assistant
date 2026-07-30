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
        "$(staged_path "${CONFIG_HOME}/systemd/user")"

    install -m 0644 \
        "${SOURCE_ROOT}"/local/lib/notch_voice_assistant/*.py \
        "$package_target/"
    install -m 0755 \
        "${SOURCE_ROOT}/local/bin/${APP_NAME}" \
        "$(staged_path "${LOCAL_BIN}/${APP_NAME}")"
    install -m 0644 \
        "${SOURCE_ROOT}/config/notch-voice-assistant/style.css" \
        "$(staged_path "${CONFIG_HOME}/${APP_NAME}/style.css")"
    install -m 0644 \
        "${SOURCE_ROOT}/config/systemd/user/${APP_NAME}.service" \
        "$(staged_path "${CONFIG_HOME}/systemd/user/${APP_NAME}.service")"
}

install_speech_stack() {
    local venv="${DATA_HOME}/${APP_NAME}/venv"
    local models="${DATA_HOME}/${APP_NAME}/models"
    local -a packages=(
        "piper-tts==1.5.0"
        "faster-whisper==1.2.1"
        "webrtcvad-wheels==2.0.14"
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
    "$venv/bin/python" -m piper.download_voices \
        --data-dir "$models" \
        en_US-lessac-medium
}

warn_for_missing_system_dependencies() {
    local command
    local -a missing=()
    for command in claude ffmpeg aplay; do
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
