param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [string]$OwnershipFile = ''
)

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\')
$python = [IO.Path]::GetFullPath($PythonPath)
$checker = Join-Path $repo 'scripts\check_backend_protocol.py'
$expectedBackend = ((Join-Path $repo 'backend_host.py') -replace '/', '\').ToLowerInvariant()
$expectedPythonDirectory = ((Split-Path -Parent $python) -replace '/', '\').ToLowerInvariant()

function Get-PortOwner {
    @(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Get-ProcessIdentity([int]$ProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    $commandLine = [string]$process.CommandLine
    $normalizedCommand = ($commandLine -replace '/', '\').ToLowerInvariant()
    $isExpected = $false
    if (-not [string]::IsNullOrWhiteSpace([string]$process.ExecutablePath)) {
        $executablePath = [IO.Path]::GetFullPath([string]$process.ExecutablePath)
        $executableDirectory = ((Split-Path -Parent $executablePath) -replace '/', '\').ToLowerInvariant()
        $executableName = (Split-Path -Leaf $executablePath).ToLowerInvariant()
        $isExpected = $normalizedCommand.Contains($expectedBackend) -and
            $executableDirectory -eq $expectedPythonDirectory -and
            $executableName -in @('python.exe', 'pythonw.exe')
    }
    [pscustomobject]@{
        Pid = $ProcessId
        Name = $process.Name
        ExecutablePath = $process.ExecutablePath
        CommandLine = $commandLine
        IsExpectedAIFrenBackend = $isExpected
    }
}

function Test-BackendProtocol {
    & $python $checker *> $null
    return $LASTEXITCODE -eq 0
}

function Stop-ExpectedBackend([object]$Identity) {
    Write-Host "Stopping stale AIFren backend PID $($Identity.Pid) ($($Identity.Name))."
    # Windows has no portable SIGINT for a detached pythonw process. Ask the
    # process to stop first, then escalate only if it remains alive.
    # First ask the local host to close its WebSocket, PTT, TTS, and state.
    & $python $checker --shutdown *> $null
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-Process -Id $Identity.Pid -ErrorAction SilentlyContinue)) { return $true }
        Start-Sleep -Milliseconds 200
    }
    Write-Host "Backend PID $($Identity.Pid) did not exit promptly; forcing termination."
    Stop-Process -Id $Identity.Pid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
    return -not [bool](Get-Process -Id $Identity.Pid -ErrorAction SilentlyContinue)
}

if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "AIFren runtime was not found: $python"
    exit 1
}

$owners = Get-PortOwner
foreach ($owner in $owners) {
    $identity = Get-ProcessIdentity $owner
    # Protocol compatibility is readiness, never ownership. Only this exact
    # repository/runtime command may be replaced by the developer launcher.
    if ($null -eq $identity -or -not $identity.IsExpectedAIFrenBackend) {
        Write-Host "Port 8765 is owned by an unrelated process and will not be stopped."
        if ($null -ne $identity) {
            Write-Host "PID: $($identity.Pid)"
            Write-Host "Process: $($identity.Name)"
            Write-Host "Executable: $($identity.ExecutablePath)"
            Write-Host "Command line: $($identity.CommandLine)"
        } else {
            Write-Host "PID: $owner"
        }
        exit 2
    }
    if (-not (Stop-ExpectedBackend $identity)) {
        Write-Error "Could not release port 8765 from AIFren backend PID $owner."
        exit 3
    }
}

for ($i = 0; $i -lt 30; $i++) {
    if (-not (Get-PortOwner)) { break }
    Start-Sleep -Milliseconds 200
}
if (Get-PortOwner) {
    Write-Error 'Port 8765 did not become free after stopping the expected backend.'
    exit 4
}

$logPath = Join-Path $env:TEMP 'aifren-backend.log'
$errorPath = Join-Path $env:TEMP 'aifren-backend-error.log'
Write-Host "Starting current AIFren backend from $repo"
$backendScript = Join-Path $repo 'backend_host.py'
# This session-owned backend is not a developer-facing console.  pythonw
# prevents the Desktop shortcut from creating a visible Python window while
# preserving the existing local stdout/stderr log files.
$pythonWindowless = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
$backendExecutable = if (Test-Path -LiteralPath $pythonWindowless) { $pythonWindowless } else { $python }
function Start-AIFrenBackend {
    # Start the process directly rather than through Start-Process. The latter
    # reconstructs a PowerShell environment dictionary and fails in shells
    # that expose both Path and PATH. Direct ProcessStartInfo preserves the
    # Windows inherited environment unchanged.
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $backendExecutable
    $startInfo.Arguments = '"' + $backendScript + '"'
    $startInfo.WorkingDirectory = $repo
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw 'The AIFren backend process could not be started.'
    }
    return $process
}

$started = Start-AIFrenBackend

for ($i = 0; $i -lt 300; $i++) {
    if (Test-BackendProtocol) {
        if ($OwnershipFile) { Set-Content -LiteralPath $OwnershipFile -Value $started.Id -NoNewline }
        Write-Host "AIFren backend transport v2 is ready (PID $($started.Id))."
        exit 0
    }
    if ($started.HasExited) {
        Write-Host "The new AIFren backend exited before transport validation."
        if (Test-Path $logPath) { Get-Content -LiteralPath $logPath -Tail 20 }
        if (Test-Path $errorPath) { Get-Content -LiteralPath $errorPath -Tail 20 }
        exit 5
    }
    Start-Sleep -Seconds 1
}

Write-Host 'The new AIFren backend did not reach transport v2 readiness; stopping it.'
Stop-Process -Id $started.Id -Force -ErrorAction SilentlyContinue
if (Test-Path $logPath) { Get-Content -LiteralPath $logPath -Tail 20 }
if (Test-Path $errorPath) { Get-Content -LiteralPath $errorPath -Tail 20 }
exit 6
