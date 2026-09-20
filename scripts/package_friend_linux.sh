#!/usr/bin/env bash
set -euo pipefail

# Selection only: no discovery/copy of live settings, characters, caches or models.
# Native runtime/player/model inputs must be staged and explicitly reviewed first.
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
: "${AIFREN_PACKAGE_STAGING_ROOT:?Set a clean reviewed staging root}"
: "${AIFREN_APPROVED_PACKAGE_INPUTS:?Set the reviewed path/SHA256 input manifest}"
: "${AIFREN_PACKAGE_OUTPUT:?Set a fresh output directory}"
exec "$repository_root/.venv-aifren/bin/python" "$repository_root/scripts/package_linux.py" \
    --source-root "$repository_root" --staging-root "$AIFREN_PACKAGE_STAGING_ROOT" \
    --inputs "$AIFREN_APPROVED_PACKAGE_INPUTS" --output "$AIFREN_PACKAGE_OUTPUT" \
    --default-model "${AIFREN_PACKAGE_DEFAULT_MODEL:-}"
