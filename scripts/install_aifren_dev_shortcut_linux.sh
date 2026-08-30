#!/usr/bin/env bash
set -euo pipefail

# Compatibility spelling for the AIFren Dev desktop-entry installer.
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec "$repository_root/scripts/install_aifren_dev_launcher_linux.sh" "$@"
