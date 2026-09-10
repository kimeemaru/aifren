<#
Creates or updates the current user's Desktop\AIFren Dev shortcut.
The shortcut path is resolved at install time, so this repository can be moved
without committing a machine-specific absolute path.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $repositoryRoot 'scripts\launch_aifren_dev.vbs'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "AIFren hidden developer launcher was not found: $launcher"
}

$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
$shortcutPath = Join-Path $desktop 'AIFren Dev.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
$shortcut.Arguments = ('"{0}"' -f $launcher)
$shortcut.WorkingDirectory = $repositoryRoot
$shortcut.Description = 'Open the local AIFren developer launcher.'
# No icon is assigned until a licensed AIFren visual asset exists.
$shortcut.Save()

Write-Host "Created or updated: $shortcutPath"
