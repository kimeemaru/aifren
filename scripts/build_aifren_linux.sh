#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
project="$repository_root/unity/AIFrenUnityPoc"
player="$project/Builds/Linux/AIFrenPoc.x86_64"
log_file="${AIFREN_UNITY_BUILD_LOG:-/tmp/aifren-unity-linux-build.log}"
unity_version="2022.3.62f3"

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

echo "Building AIFren Linux presentation player with $unity_editor..."
"$unity_editor" \
    -batchmode \
    -quit \
    -projectPath "$project" \
    -executeMethod AIFren.UnityPoc.Editor.BuildAIFrenPoc.BuildLinux \
    -logFile "$log_file"

if [[ ! -x "$player" ]]; then
    echo "Unity reported success, but the Linux player was not created: $player" >&2
    echo "See $log_file" >&2
    exit 1
fi

echo "Build succeeded: $player"
