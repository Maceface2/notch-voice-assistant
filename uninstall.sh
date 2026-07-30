#!/usr/bin/env bash

set -euo pipefail

readonly APP_NAME="notch-voice-assistant"
readonly USER_HOME="${HOME:?HOME must be set}"
readonly CONFIG_HOME="${XDG_CONFIG_HOME:-${USER_HOME}/.config}"
readonly DATA_HOME="${XDG_DATA_HOME:-${USER_HOME}/.local/share}"
readonly STATE_HOME="${XDG_STATE_HOME:-${USER_HOME}/.local/state}"

purge_data=false

if [[ "${1:-}" == "--purge-data" ]]; then
    purge_data=true
    shift
fi
if (( $# > 0 )); then
    printf 'Usage: ./uninstall.sh [--purge-data]\n' >&2
    exit 2
fi

remove_app_tree() {
    local target="$1"
    case "$target" in
        "${USER_HOME}/.local/lib/notch_voice_assistant"|
        "${DATA_HOME}/${APP_NAME}"|
        "${STATE_HOME}/${APP_NAME}")
            rm -rf -- "$target"
            ;;
        *)
            printf 'Refusing unexpected removal target: %s\n' "$target" >&2
            exit 1
            ;;
    esac
}

systemctl --user stop "${APP_NAME}.service" 2>/dev/null || true

rm -f -- \
    "${USER_HOME}/.local/bin/${APP_NAME}" \
    "${CONFIG_HOME}/${APP_NAME}/style.css" \
    "${CONFIG_HOME}/${APP_NAME}/whisper-device" \
    "${CONFIG_HOME}/systemd/user/${APP_NAME}.service"
rmdir -- "${CONFIG_HOME}/${APP_NAME}" 2>/dev/null || true
remove_app_tree "${USER_HOME}/.local/lib/notch_voice_assistant"

if [[ "$purge_data" == true ]]; then
    remove_app_tree "${DATA_HOME}/${APP_NAME}"
    remove_app_tree "${STATE_HOME}/${APP_NAME}"
else
    printf 'Preserved models, venv, and session state. Use --purge-data to remove them.\n'
fi

systemctl --user daemon-reload
printf 'Uninstalled %s. Remove its snippets from Waybar manually.\n' "$APP_NAME"
