#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
gui_launcher="$repository_root/scripts/aifren_dev_launcher_linux.py"
applications_directory="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
desktop_file="$applications_directory/aifren-dev.desktop"

mkdir -p "$applications_directory"
cat > "$desktop_file" <<EOF
[Desktop Entry]
Type=Application
Name=AIFren Dev
Comment=Open AIFren's Unity development controls and diagnostics
Exec="$gui_launcher"
Path=$repository_root
Terminal=false
Categories=Development;
Actions=StartCurrent;RebuildStart;StartDevelopment;RebuildDevelopment;

[Desktop Action StartCurrent]
Name=Start Current Build
Exec="$gui_launcher" --launch current

[Desktop Action RebuildStart]
Name=Rebuild + Start
Exec="$gui_launcher" --launch rebuild

[Desktop Action StartDevelopment]
Name=Start Development Build
Exec="$gui_launcher" --launch current --development

[Desktop Action RebuildDevelopment]
Name=Rebuild Development + Start
Exec="$gui_launcher" --launch rebuild --development
EOF
chmod 755 "$desktop_file"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$applications_directory" || true
fi

echo "Created or updated: $desktop_file"
