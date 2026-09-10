[CmdletBinding()]
param(
    [string]$PythonRuntime = '',
    [switch]$SkipUnityBuild,
    [switch]$Replace,
    [switch]$Archive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = if ($PythonRuntime) {
    [IO.Path]::GetFullPath($PythonRuntime)
} else {
    Join-Path $repositoryRoot '.venv-aifren'
}
$python = Join-Path $runtimeRoot 'python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = Join-Path $runtimeRoot 'Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "The Windows AIFren build runtime is unavailable: $python"
}

if (-not $SkipUnityBuild) {
    & (Join-Path $PSScriptRoot 'build_aifren_test.bat')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$arguments = @(
    (Join-Path $PSScriptRoot 'package_unity.py'),
    '--platform', 'windows',
    '--repository-root', $repositoryRoot,
    '--python-runtime', $runtimeRoot
)
if ($Replace) { $arguments += '--replace' }
if ($Archive) { $arguments += '--archive' }
& $python @arguments
exit $LASTEXITCODE
