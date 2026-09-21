#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
project="$repository_root/unity/AIFrenUnityPoc"
player="$project/Builds/Linux/AIFrenPoc.x86_64"
log_file="${AIFREN_UNITY_BUILD_LOG:-${TMPDIR:-/tmp}/unity-linux-build.log}"
unity_version="2022.3.62f3"
build_method="AIFren.UnityPoc.Editor.BuildAIFrenPoc.BuildLinux"
preflight=false

for argument in "$@"; do
    case "$argument" in
        --development)
            build_method="AIFren.UnityPoc.Editor.BuildAIFrenPoc.BuildLinuxDevelopment"
            player="$project/Builds/LinuxDevelopment/AIFrenPoc.x86_64" ;;
        --preflight) preflight=true ;;
        *) echo "Usage: $0 [--development] [--preflight]" >&2; exit 2 ;;
    esac
done

unity_editor="${UNITY_EDITOR:-}"
if [[ -z "$unity_editor" ]]; then
    for candidate in \
        "$HOME/Unity/Hub/Editor/$unity_version/Editor/Unity" \
        "$HOME/.local/bin/unity-editor"; do
        if [[ -x "$candidate" ]]; then
            unity_editor="$candidate"
            break
        fi
    done
fi
if [[ -z "$unity_editor" ]] && command -v unity-editor >/dev/null 2>&1; then
    unity_editor="$(command -v unity-editor)"
fi
if [[ -z "$unity_editor" ]] && [[ -x "$HOME/.local/bin/unity" ]]; then
    unity_install="$("$HOME/.local/bin/unity" editors path "$unity_version" 2>/dev/null || true)"
    if [[ -x "$unity_install/Editor/Unity" ]]; then
        unity_editor="$unity_install/Editor/Unity"
    fi
fi
if [[ -z "$unity_editor" ]] || [[ ! -x "$unity_editor" ]]; then
    echo "Unity $unity_version Editor was not found." >&2
    echo "Install it under ~/Unity/Hub/Editor/$unity_version or set UNITY_EDITOR." >&2
    exit 1
fi

if [[ "$preflight" == true ]]; then
    build_method="AIFren.UnityPoc.Editor.BuildAIFrenPoc.PreflightEditorExecution"
    # A fresh log prevents a previous successful marker from masking a failed
    # launch. Use the normal host environment/licensing, not application QA roots.
    log_file="$(mktemp "${log_file}.preflight.XXXXXX")"
    echo "Checking project execution with $unity_editor (log: $log_file)..."
else
    echo "Building AIFren Linux presentation player with $unity_editor..."
fi
"$unity_editor" \
    -batchmode \
    -quit \
    -projectPath "$project" \
    -executeMethod "$build_method" \
    -logFile "$log_file" || {
        echo "Unity project execution failed; see $log_file. Check normal Editor sign-in/licensing before retrying." >&2
        exit 1
    }

if [[ "$preflight" == true ]]; then
    if ! grep -Fxq 'AIFren Editor execution preflight passed.' "$log_file"; then
        echo "Editor did not complete the project preflight; see $log_file." >&2
        exit 1
    fi
    echo "Editor project execution passed. Tests and a current player build are separate gates."
    exit 0
fi

if [[ ! -x "$player" ]]; then
    echo "Unity reported success, but the Linux player was not created: $player" >&2
    echo "See $log_file" >&2
    exit 1
fi

echo "Build succeeded: $player"
