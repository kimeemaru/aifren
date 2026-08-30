#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
runtime="$repository_root/.venv-aifren/bin/python"
ensure_backend="$repository_root/scripts/ensure_aifren_backend_linux.py"
build_script="$repository_root/scripts/build_aifren_linux.sh"
project="$repository_root/unity/AIFrenUnityPoc"

action="current"
development_build=false
mode="landscape"
reset_arguments=()
validate_arguments=false

usage() {
    cat <<'EOF'
Usage: scripts/aifren_dev_linux.sh [current|rebuild] [development] [landscape|portrait] [reset-console] [reset-ui] [--validate-arguments]

current (default) starts the existing Linux player, building it only if missing.
rebuild builds the Linux player before starting it.
development selects the separately built Development player and never builds it implicitly.
--validate-arguments checks only syntax and never starts a build, backend, or player.
EOF
}

for argument in "$@"; do
    case "$argument" in
        current|rebuild) action="$argument" ;;
        development) development_build=true ;;
        landscape|portrait) mode="$argument" ;;
        reset-console) reset_arguments+=("-aifren-reset-console-unlock") ;;
        reset-ui) reset_arguments+=("-aifren-reset-ui") ;;
        --validate-arguments) validate_arguments=true ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown argument: $argument" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ "$validate_arguments" == true ]]; then
    exit 0
fi

# Local runtime/provider configuration is intentionally ignored and excluded
# from packaging. Export it for the backend without putting voice references,
# transcripts, credentials, or machine-specific paths in tracked settings.
if [[ -f "$repository_root/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$repository_root/.env"
    set +a
fi

if [[ ! -x "$runtime" ]]; then
    echo "AIFren's Linux runtime is missing." >&2
    echo "Run scripts/setup_aifren_runtime_linux.sh once, then launch again." >&2
    exit 1
fi

if [[ "$mode" == "portrait" ]]; then
    width=900
    height=1600
    monitor="${AIFREN_PORTRAIT_MONITOR:-1}"
else
    width=1920
    height=1080
    monitor="${AIFREN_LANDSCAPE_MONITOR:-1}"
fi

player="$project/Builds/Linux/AIFrenPoc.x86_64"
build_arguments=()
if [[ "$development_build" == true ]]; then
    player="$project/Builds/LinuxDevelopment/AIFrenPoc.x86_64"
    build_arguments+=(--development)
fi
if [[ "$action" == "rebuild" || ( "$development_build" == false && ! -x "$player" ) ]]; then
    # This launcher is for local review. Keep shareable builds safe by default
    # in build_aifren_linux.sh, but include this checkout's ignored local
    # presentation assets whenever the developer launcher builds a player.
    AIFREN_INCLUDE_LOCAL_PRESENTATION_ASSETS=1 "$build_script" "${build_arguments[@]}"
fi

if [[ ! -x "$player" ]]; then
    if [[ "$development_build" == true ]]; then
        echo "The Development player is not built: $player" >&2
        echo "Build it explicitly with AIFREN_INCLUDE_LOCAL_PRESENTATION_ASSETS=1 scripts/build_aifren_linux.sh --development." >&2
        exit 1
    fi
    echo "The Linux player was not built: $player" >&2
    exit 1
fi

ownership_file="$(mktemp "${TMPDIR:-/tmp}/aifren-dev-backend.XXXXXX.pid")"
rm -f "$ownership_file"
cleanup() {
    "$runtime" "$ensure_backend" \
        --stop \
        --repository-root "$repository_root" \
        --python "$runtime" \
        --ownership-file "$ownership_file" || true
    rm -f "$ownership_file"
}
trap cleanup EXIT INT TERM

"$runtime" "$ensure_backend" \
    --start \
    --repository-root "$repository_root" \
    --python "$runtime" \
    --ownership-file "$ownership_file"

player_log="${AIFREN_UNITY_PLAYER_LOG:-/tmp/aifren-unity-player.log}"
echo "Launching Linux player: $player"
stop_request_file="${AIFREN_STOP_REQUEST_FILE:-}"
AIFREN_REPOSITORY_ROOT="$repository_root" \
AIFREN_BACKEND_OWNERSHIP_FILE="$ownership_file" \
"$player" \
    -screen-width "$width" \
    -screen-height "$height" \
    -screen-fullscreen 1 \
    -monitor "$monitor" \
    -display-diagnostics \
    -logFile "$player_log" \
    "${reset_arguments[@]}" &
player_pid=$!

# The GUI launcher captures this script's stdout. Surface a transport-loss
# warning there as it happens, instead of requiring developers to inspect the
# Unity Player.log manually. Start at EOF so an old log cannot be mistaken for
# this player session; tail exits when this exact player exits.
forward_disconnect_warnings() {
    tail --pid="$player_pid" -n 0 -F "$player_log" 2>/dev/null |
        while IFS= read -r line; do
            case "$line" in
                *"[AIFren Transport] Warning: backend disconnected"*|*"[AIFren Transport] Reconnect:"*)
                    printf '%s\n' "$line"
                    ;;
            esac
        done
}
forward_disconnect_warnings &
warning_forwarder_pid=$!

stop_requested=false
stop_requested_at=0
stop_force_sent=false
while kill -0 "$player_pid" 2>/dev/null; do
    if [[ -n "$stop_request_file" && -e "$stop_request_file" && "$stop_requested" == false ]]; then
        echo "Stop requested by AIFren Dev Launcher; closing the player before backend cleanup."
        kill -INT "$player_pid" 2>/dev/null || true
        stop_requested=true
        stop_requested_at=$SECONDS
    elif [[ "$stop_requested" == true && "$stop_force_sent" == false && $((SECONDS - stop_requested_at)) -ge 5 ]]; then
        echo "Player did not exit after SIGINT; sending SIGTERM before backend cleanup."
        kill -TERM "$player_pid" 2>/dev/null || true
        stop_force_sent=true
    fi
    sleep 0.2
done

set +e
wait "$player_pid"
player_exit=$?
if kill -0 "$warning_forwarder_pid" 2>/dev/null; then
    kill "$warning_forwarder_pid" 2>/dev/null || true
fi
wait "$warning_forwarder_pid" 2>/dev/null || true
set -e
exit "$player_exit"
