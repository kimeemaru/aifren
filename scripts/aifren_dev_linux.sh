#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
runtime="$repository_root/.venv-aifren/bin/python"
ensure_backend="$repository_root/scripts/ensure_aifren_backend_linux.py"
build_script="$repository_root/scripts/build_aifren_linux.sh"
project="$repository_root/unity/AIFrenUnityPoc"

action="current"
development_build=false
memory_authority=v2
mode="landscape"
reset_arguments=()
validate_arguments=false

# A staged acceptance owner supplies these values before this launcher loads
# ordinary private configuration. Preserve that already-attested selection so
# a stale .env cannot redirect the disposable data root or diagnostic output.
inherited_session_diagnostics_dir="${AIFREN_DEVELOPMENT_SESSION_DIAGNOSTICS_DIR:-}"
inherited_session_capture_id="${AIFREN_DEVELOPMENT_SESSION_CAPTURE_ID:-}"
inherited_staged_data_root="${AIFREN_DEVELOPMENT_STAGED_DATA_ROOT:-}"
inherited_staged_character_id="${AIFREN_DEVELOPMENT_STAGED_CHARACTER_ID:-}"
inherited_resource_root="${AIFREN_RESOURCE_ROOT:-}"
inherited_player_log="${AIFREN_UNITY_PLAYER_LOG:-}"
inherited_native_plan="${AIFREN_DEVELOPMENT_QA_PLAN:-}"
inherited_native_gate="${AIFREN_ENABLE_DEVELOPMENT_QA:-}"
inherited_data_root="${AIFREN_DATA_ROOT:-}"

usage() {
    cat <<'EOF'
Usage: scripts/aifren_dev_linux.sh [current|rebuild] [development] [v1-memory|v2-memory] [landscape|portrait] [reset-console] [reset-ui] [--validate-arguments]

current (default) starts the existing Linux player, building it only if missing.
rebuild builds the Linux player before starting it.
development selects the separately built Development player and never builds it implicitly.
V2 is the ordinary memory authority; v2-memory is a compatibility spelling.
v1-memory selects process-local V1 rollback with a Development player; no V2 writes.
--validate-arguments checks only syntax and never starts a build, backend, or player.
EOF
}

for argument in "$@"; do
    case "$argument" in
        current|rebuild) action="$argument" ;;
        development) development_build=true ;;
        v2-memory) memory_authority=v2 ;;
        v1-memory) memory_authority=v1 ;;
        landscape|portrait) mode="$argument" ;;
        reset-console) reset_arguments+=("-aifren-reset-console-unlock") ;;
        reset-ui) reset_arguments+=("-aifren-reset-ui") ;;
        --validate-arguments) validate_arguments=true ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown argument: $argument" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ "$memory_authority" == v1 && "$development_build" != true ]]; then
    echo "v1-memory rollback requires the Development player." >&2
    exit 2
fi

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

# Reuse an explicitly selected environment without relocating source or data.
# Keep the path as supplied: resolving a venv's Python symlink loses its packages.
runtime="${AIFREN_PYTHON:-$runtime}"

if [[ -n "$inherited_session_diagnostics_dir" || -n "$inherited_session_capture_id" ]]; then
    export AIFREN_DEVELOPMENT_SESSION_DIAGNOSTICS_DIR="$inherited_session_diagnostics_dir"
    export AIFREN_DEVELOPMENT_SESSION_CAPTURE_ID="$inherited_session_capture_id"
    export AIFREN_DEVELOPMENT_STAGED_DATA_ROOT="$inherited_staged_data_root"
    export AIFREN_DEVELOPMENT_STAGED_CHARACTER_ID="$inherited_staged_character_id"
    export AIFREN_RESOURCE_ROOT="$inherited_resource_root"
    export AIFREN_UNITY_PLAYER_LOG="$inherited_player_log"
fi

# Command-line authority selection is the explicit final say even when a
# developer's private .env contains stale experimental values.
export AIFREN_MEMORY_AUTHORITY="$memory_authority"
# Disposable application routing is separately gated by its attested owner.
# Ordinary V2 authority never grants test automation permissions.

# Forward the existing finite native plan only for an explicitly isolated
# Development check. The player validates the plan before accessing preferences.
if [[ -n "$inherited_native_plan" || -n "${AIFREN_DEVELOPMENT_QA_PLAN:-}" ]]; then
    if [[ "$development_build" != true || "$inherited_native_gate" != 1 ||
          -z "$inherited_data_root" || ! -f "$inherited_native_plan" ]]; then
        echo "A finite player plan requires explicit Development QA, data root and existing plan." >&2
        exit 2
    fi
    # Saved normal configuration cannot redirect a finite check into live data.
    export AIFREN_DATA_ROOT="$inherited_data_root"
    export AIFREN_ENABLE_DEVELOPMENT_QA=1
    export AIFREN_RESOURCE_ROOT="$inherited_resource_root"
    export AIFREN_DEVELOPMENT_STAGED_DATA_ROOT="$inherited_staged_data_root"
    export AIFREN_DEVELOPMENT_STAGED_CHARACTER_ID="$inherited_staged_character_id"
    reset_arguments+=("-aifren-qa-plan" "$inherited_native_plan")
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
    # Public builds validate bundled resources and reject unreviewed additions.
    "$build_script" "${build_arguments[@]}"
fi

if [[ ! -x "$player" ]]; then
    if [[ "$development_build" == true ]]; then
        echo "The Development player is not built: $player" >&2
        echo "Build it explicitly with scripts/build_aifren_linux.sh --development." >&2
        exit 1
    fi
    echo "The Linux player was not built: $player" >&2
    exit 1
fi

ownership_file="$(mktemp "${TMPDIR:-/tmp}/aifren-dev-backend.XXXXXX.pid")"
rm -f "$ownership_file"
session_diagnostics_dir="${AIFREN_DEVELOPMENT_SESSION_DIAGNOSTICS_DIR:-}"
session_capture_id="${AIFREN_DEVELOPMENT_SESSION_CAPTURE_ID:-}"
player_pid=""
warning_forwarder_pid=""
cleanup_started=false
cleanup() {
    cleanup_status=$?
    if [[ "$cleanup_started" == true ]]; then
        return "$cleanup_status"
    fi
    cleanup_started=true
    trap - EXIT INT TERM
    if [[ -n "$player_pid" ]] && kill -0 "$player_pid" 2>/dev/null; then
        kill -INT "$player_pid" 2>/dev/null || true
        for _ in {1..25}; do
            kill -0 "$player_pid" 2>/dev/null || break
            sleep 0.2
        done
        if kill -0 "$player_pid" 2>/dev/null; then
            kill -TERM "$player_pid" 2>/dev/null || true
        fi
        wait "$player_pid" 2>/dev/null || true
    fi
    if [[ -n "$warning_forwarder_pid" ]] && kill -0 "$warning_forwarder_pid" 2>/dev/null; then
        kill "$warning_forwarder_pid" 2>/dev/null || true
        wait "$warning_forwarder_pid" 2>/dev/null || true
    fi
    "$runtime" "$ensure_backend" \
        --stop \
        --repository-root "$repository_root" \
        --python "$runtime" \
        --ownership-file "$ownership_file" || true
    rm -f "$ownership_file"
    return "$cleanup_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -n "$session_diagnostics_dir" || -n "$session_capture_id" ]]; then
    if [[ "$development_build" != true || "$memory_authority" != v2 ]]; then
        echo "Development session diagnostics require Development V2 authority." >&2
        exit 2
    fi
    if [[ -z "$session_diagnostics_dir" || -z "$session_capture_id" ]]; then
        echo "Development session diagnostics require both internal session values." >&2
        exit 2
    fi
fi

"$runtime" "$ensure_backend" \
    --start \
    --repository-root "$repository_root" \
    --python "$runtime" \
    --ownership-file "$ownership_file"

if [[ -n "$session_diagnostics_dir" ]]; then
    provider_ready=false
    for _ in {1..120}; do
        if "$runtime" "$repository_root/scripts/check_backend_protocol.py" \
                --v2-acceptance-ready >/dev/null 2>&1; then
            provider_ready=true
            break
        fi
        sleep 1
    done
    if [[ "$provider_ready" != true ]]; then
        echo "The staged V2 provider did not become ready; refusing to launch the player." >&2
        exit 1
    fi
    echo "Staged V2 provider: ready"
fi

# Arbitrary Unity/native stdout is not an incident record. Keep only the
# existing structured Development recorder and console/reconnect transport.
# Legacy Player.log files are untouched; no tail/copy of prior private output.
player_log="/dev/null"
echo "Launching Linux player: selected build"
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
    "${reset_arguments[@]}" >/dev/null 2>&1 &
player_pid=$!

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
set -e
exit "$player_exit"
