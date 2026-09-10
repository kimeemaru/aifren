param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][string]$OwnershipFile
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $OwnershipFile)) { exit 0 }
$raw = (Get-Content -LiteralPath $OwnershipFile -Raw).Trim()
Remove-Item -LiteralPath $OwnershipFile -Force -ErrorAction SilentlyContinue
if (-not ($raw -as [int])) { exit 0 }
$ownedProcessId = [int]$raw
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $ownedProcessId" -ErrorAction SilentlyContinue
if ($null -eq $process) { exit 0 }
$repo = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\')
$python = [IO.Path]::GetFullPath($PythonPath)
$normalizedCommand = ([string]$process.CommandLine -replace '/', '\').ToLowerInvariant()
$expectedBackend = ((Join-Path $repo 'backend_host.py') -replace '/', '\').ToLowerInvariant()
$expectedPythonDirectory = ((Split-Path -Parent $python) -replace '/', '\').ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace([string]$process.ExecutablePath)) { exit 0 }
$executablePath = [IO.Path]::GetFullPath([string]$process.ExecutablePath)
$executableDirectory = ((Split-Path -Parent $executablePath) -replace '/', '\').ToLowerInvariant()
$executableName = (Split-Path -Leaf $executablePath).ToLowerInvariant()
if (-not $normalizedCommand.Contains($expectedBackend) -or
    $executableDirectory -ne $expectedPythonDirectory -or
    $executableName -notin @('python.exe', 'pythonw.exe')) { exit 0 }
& $PythonPath (Join-Path $RepositoryRoot 'scripts\check_backend_protocol.py') --shutdown *> $null
for ($i = 0; $i -lt 20; $i++) {
    if (-not (Get-Process -Id $ownedProcessId -ErrorAction SilentlyContinue)) { exit 0 }
    Start-Sleep -Milliseconds 200
}
Stop-Process -Id $ownedProcessId -Force -ErrorAction SilentlyContinue
