<# Windows-only front end for the existing batch launch lifecycle. #>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$repo = Split-Path -Parent $PSScriptRoot
$player = Join-Path $repo 'unity\AIFrenUnityPoc\Builds\Windows\AIFrenPoc.exe'
$managed = Join-Path $repo 'unity\AIFrenUnityPoc\Builds\Windows\AIFrenPoc_Data\Managed\AIFren.UnityPoc.dll'
$run = Join-Path $PSScriptRoot 'run_aifren_test.bat'
$rebuild = Join-Path $repo 'rebuild_and_run_aifren_test.bat'
$logs = Join-Path $repo 'logs'
$launcherLog = Join-Path $logs 'dev_launcher.log'
$script:session = $null
$script:sessionStarted = $null
$script:ownedFile = $null
$script:playerPid = $null
$script:state = 'Ready'
$script:heartbeatUntil = $null
$script:lastHeartbeat = $null
$script:sessionOutputFile = $null
$script:sessionOutputText = ''

function Ensure-LogFile {
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    if ((Test-Path -LiteralPath $launcherLog) -and (Get-Item $launcherLog).Length -gt 1048576) {
        Move-Item -LiteralPath $launcherLog -Destination ($launcherLog + '.1') -Force
    }
}
function Get-BuildFingerprint {
    if (-not (Test-Path -LiteralPath $managed)) { return 'Missing' }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $managed).Hash.Substring(0,8)
}
function Add-Output([string]$line) {
    try {
        if ([string]::IsNullOrWhiteSpace($line)) { return }
        $entry = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $line.TrimEnd()
        Add-Content -LiteralPath $launcherLog -Value $entry
        if ($output -and -not $output.IsDisposed) {
            $append = [System.Action[string]]{
                param([string]$text)
                if ($output.IsDisposed) { return }
                $wasAtBottom = $output.SelectionStart -ge $output.TextLength - 2
                $output.AppendText($text + [Environment]::NewLine)
                if ($wasAtBottom) { $output.SelectionStart = $output.TextLength; $output.ScrollToCaret() }
            }
            if ($output.InvokeRequired) {
                [void]$output.BeginInvoke($append, [object[]]@($entry))
            } else {
                $append.Invoke($entry)
            }
        }
    } catch {
        # A process-output callback must never take down the WinForms UI.
        try { Add-Content -LiteralPath $launcherLog -Value ('[{0}] Dev Output failure: {1}' -f (Get-Date -Format 'HH:mm:ss'), $_.Exception.ToString()) } catch {}
    }
}
function Write-LauncherException([string]$context, [Exception]$exception) {
    $entry = '[{0}] {1} failed: {2}' -f (Get-Date -Format 'HH:mm:ss'), $context, $exception.ToString()
    try { Add-Content -LiteralPath $launcherLog -Value $entry } catch {}
    if ($status -and -not $status.IsDisposed) { $status.Text = "$context failed; see Dev Output/log." }
    try { Add-Output "$context failed: $($exception.Message)" } catch {}
}
function Invoke-LauncherSafe([string]$context, [scriptblock]$action) {
    try { & $action } catch { Write-LauncherException $context $_.Exception }
}
function Set-State([string]$value) { $script:state = $value; $status.Text = $value }
function Update-Status {
    $buildTime.Text = $(if (Test-Path $managed) { (Get-Item $managed).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') } else { 'Missing' })
    $buildHash.Text = Get-BuildFingerprint
    if ($script:session -and -not $script:session.HasExited) {
        $running = $false
        if ($script:playerPid) { $running = $null -ne (Get-Process -Id $script:playerPid -ErrorAction SilentlyContinue) }
        $stopButton.Enabled = $true
        if ($running -and $script:state -ne 'AIFren running') { Set-State 'AIFren running'; Add-Output "Player PID $script:playerPid is alive." }
    } else {
        $stopButton.Enabled = $false
        if ($script:state -notlike 'Ready*' -and $script:state -ne 'AIFren closed. Ready to launch again.') { Set-State 'Ready' }
    }
}
function Set-SessionControls([bool]$active) {
    $startButton.Enabled = -not $active; $rebuildButton.Enabled = -not $active
    $resetConsole.Enabled = -not $active; $resetUi.Enabled = -not $active
    $stopButton.Enabled = $active
}
function Get-ProcessTrace([int]$processId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if (-not $process) { return "pid=$processId unavailable" }
    $path = if ($process.ExecutablePath) { $process.ExecutablePath } else { '<unknown>' }
    return "pid=$processId path=$path parent=$($process.ParentProcessId)"
}
function Write-LaunchHeartbeat {
    if (-not $script:heartbeatUntil -or (Get-Date) -gt $script:heartbeatUntil) { return }
    if ($script:lastHeartbeat -and ((Get-Date) - $script:lastHeartbeat).TotalSeconds -lt 1) { return }
    $script:lastHeartbeat = Get-Date
    $child = if ($script:session) { $script:session.Id } else { '<none>' }
    $playerId = if ($script:playerPid) { $script:playerPid } else { '<none>' }
    Add-Output "LAUNCHER heartbeat pid=$PID child=$child player=$playerId"
}
function Read-SessionOutput {
    if (-not $script:sessionOutputFile -or -not (Test-Path -LiteralPath $script:sessionOutputFile)) { return }
    $current = Get-Content -LiteralPath $script:sessionOutputFile -Raw -ErrorAction SilentlyContinue
    if ($null -eq $current -or $current.Length -le $script:sessionOutputText.Length) { return }
    if (-not $current.StartsWith($script:sessionOutputText, [StringComparison]::Ordinal)) {
        # The shared command owns this short-lived file. If it was replaced, safely
        # resume from its current contents instead of evaluating output callbacks.
        $script:sessionOutputText = ''
    }
    $newText = $current.Substring($script:sessionOutputText.Length)
    $script:sessionOutputText = $current
    foreach ($line in ($newText -split "`r?`n")) { if ($line) { Add-Output $line } }
}
function Find-OwnedPlayer {
    if (-not $script:session) { return }
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($script:session.Id)" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ieq 'AIFrenPoc.exe' -and $_.ExecutablePath -ieq $player }
    if ($children) { $script:playerPid = [int]$children[0].ProcessId }
}
function Start-Session([bool]$forceRebuild) {
    if ($script:session -and -not $script:session.HasExited) { Set-State 'AIFren is already starting or running.'; return }
    if (-not $forceRebuild -and -not (Test-Path $player)) { Set-State 'Current player is missing. Use Rebuild + Start.'; return }
    Ensure-LogFile
    $script:ownedFile = Join-Path $env:TEMP ('aifren-dev-owned-' + [guid]::NewGuid().ToString() + '.pid')
    $script:playerPid = $null
    $arguments = @(); if ($resetConsole.Checked) { $arguments += 'reset-console' }; if ($resetUi.Checked) { $arguments += 'reset-ui' }
    $target = $(if ($forceRebuild) { $rebuild } else { $run })
    $tail = $(if ($arguments.Count) { ' ' + ($arguments -join ' ') } else { '' })
    $script:sessionOutputFile = Join-Path $env:TEMP ('aifren-dev-session-' + [guid]::NewGuid().ToString() + '.log')
    $script:sessionOutputText = ''
    $command = ('call "{0}"{1} 1> "{2}" 2>&1' -f $target, $tail, $script:sessionOutputFile)
    $info = [Diagnostics.ProcessStartInfo]::new($env:ComSpec, ('/d /c ' + $command))
    $info.WorkingDirectory = $repo; $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.EnvironmentVariables['AIFREN_LAUNCHER_NONINTERACTIVE'] = '1'
    $info.EnvironmentVariables['AIFREN_OWNERSHIP_FILE'] = $script:ownedFile
    Add-Output "LAUNCHER host $(Get-ProcessTrace $PID)"
    Add-Output "LAUNCHER child command: $($info.FileName) $($info.Arguments)"
    $script:session = [Diagnostics.Process]::new(); $script:session.StartInfo = $info
    if (-not $script:session.Start()) { throw 'The shared launcher batch did not start.' }
    Add-Output "LAUNCHER child started $(Get-ProcessTrace $script:session.Id); host pid=$PID remains alive."
    $script:sessionStarted = Get-Date; $resetConsole.Checked = $false; $resetUi.Checked = $false
    $script:heartbeatUntil = $script:sessionStarted.AddSeconds(10); $script:lastHeartbeat = $null
    Set-SessionControls $true
    Set-State $(if ($forceRebuild) { 'Building...' } else { 'Starting backend...' })
    Add-Output $(if ($forceRebuild) { 'Starting rebuild through the shared build launcher.' } else { 'Starting current player through the shared launcher.' })
}
function Stop-Session {
    if (-not $script:session) { return }
    Set-State 'Stopping owned AIFren session...'; Add-Output 'Stop requested for launcher-owned session.'
    if ($script:playerPid) { Stop-Process -Id $script:playerPid -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800 }
    if ($script:ownedFile -and (Test-Path $script:ownedFile)) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stop_aifren_backend.ps1') -RepositoryRoot $repo -PythonPath (Join-Path $repo '.venv-aifren\Scripts\python.exe') -OwnershipFile $script:ownedFile
    }
    if (-not $script:session.HasExited) { $script:session.Kill(); $script:session.WaitForExit(3000) | Out-Null }
    Read-SessionOutput
    if ($script:sessionOutputFile) { Remove-Item -LiteralPath $script:sessionOutputFile -Force -ErrorAction SilentlyContinue }
    Add-Output 'Owned player/backend cleanup finished.'; $script:session = $null; $script:playerPid = $null; $script:sessionOutputFile = $null; $script:sessionOutputText = ''; Set-SessionControls $false; Set-State 'Ready'
}

$form = [Windows.Forms.Form]@{ Text='AIFren Dev Launcher'; StartPosition='CenterScreen'; ClientSize=[Drawing.Size]::new(720,610); Font=[Drawing.Font]::new('Segoe UI',9) }
$title = [Windows.Forms.Label]@{Text='AIFren Dev Launcher'; Left=18; Top=14; Width=500; Height=28; Font=[Drawing.Font]::new('Segoe UI Semibold',14)}; $form.Controls.Add($title)
$resetConsole = [Windows.Forms.CheckBox]@{Text='Reset Console unlock'; Left=18; Top=52; Width=180}
$resetUi = [Windows.Forms.CheckBox]@{Text='Reset UI/display settings'; Left=205; Top=52; Width=210}; $form.Controls.AddRange(@($resetConsole,$resetUi))
$startButton = [Windows.Forms.Button]@{Text='Start Current Build'; Left=18; Top=82; Width=190; Height=32}; $startButton.Add_Click({Invoke-LauncherSafe 'Start Current Build' {Start-Session $false}})
$rebuildButton = [Windows.Forms.Button]@{Text='Rebuild + Start'; Left=218; Top=82; Width=160; Height=32}; $rebuildButton.Add_Click({Invoke-LauncherSafe 'Rebuild + Start' {Start-Session $true}})
$stopButton = [Windows.Forms.Button]@{Text='Stop AIFren'; Left=388; Top=82; Width=130; Height=32; Enabled=$false}; $stopButton.Add_Click({Invoke-LauncherSafe 'Stop AIFren' {Stop-Session}})
$form.Controls.AddRange(@($startButton,$rebuildButton,$stopButton))
$group=[Windows.Forms.GroupBox]@{Text='Build freshness';Left=18;Top=125;Width=684;Height=72}; $form.Controls.Add($group)
$group.Controls.Add([Windows.Forms.Label]@{Text='Managed DLL:';Left=14;Top=24;Width=90}); $buildTime=[Windows.Forms.Label]@{Left=108;Top=24;Width=220};$group.Controls.Add($buildTime)
$group.Controls.Add([Windows.Forms.Label]@{Text='Fingerprint:';Left=350;Top=24;Width=75}); $buildHash=[Windows.Forms.Label]@{Left=428;Top=24;Width=160};$group.Controls.Add($buildHash)
$status=[Windows.Forms.Label]@{Text='Ready';Left=18;Top=207;Width=680;Height=22;ForeColor=[Drawing.Color]::DimGray};$form.Controls.Add($status)
$output=[Windows.Forms.TextBox]@{Multiline=$true;ReadOnly=$true;ScrollBars='Vertical';Left=18;Top=235;Width=684;Height=290;Font=[Drawing.Font]::new('Consolas',9)};$form.Controls.Add($output)
$copy=[Windows.Forms.Button]@{Text='Copy All';Left=18;Top=538;Width=100};$copy.Add_Click({[Windows.Forms.Clipboard]::SetText($output.Text)});$form.Controls.Add($copy)
$clear=[Windows.Forms.Button]@{Text='Clear';Left=128;Top=538;Width=80};$clear.Add_Click({$output.Clear()});$form.Controls.Add($clear)
$open=[Windows.Forms.Button]@{Text='Open Full Log';Left=218;Top=538;Width=110};$open.Add_Click({if(Test-Path $launcherLog){Start-Process $launcherLog}});$form.Controls.Add($open)
$timer=[Windows.Forms.Timer]::new();$timer.Interval=500;$timer.Add_Tick({Invoke-LauncherSafe 'Status refresh' {
    if($script:session -and -not $script:session.HasExited){Read-SessionOutput;Find-OwnedPlayer;Write-LaunchHeartbeat}
    elseif($script:session -and $script:session.HasExited){Read-SessionOutput;$code=$script:session.ExitCode;Add-Output "Launcher session exited with code $code.";$script:session.Dispose();$script:session=$null;$script:playerPid=$null;$script:heartbeatUntil=$null;if($script:sessionOutputFile){Remove-Item -LiteralPath $script:sessionOutputFile -Force -ErrorAction SilentlyContinue};$script:sessionOutputFile=$null;$script:sessionOutputText='';Set-SessionControls $false;Set-State 'AIFren closed. Ready to launch again.'}
    Update-Status
}});$timer.Start()
$form.Add_Shown({Invoke-LauncherSafe 'Launcher initialization' {Ensure-LogFile;Update-Status;Add-Output 'Dev Launcher ready.'}})
$form.Add_FormClosing({Invoke-LauncherSafe 'Launcher shutdown' {Add-Output "Launcher FormClosing host pid=$PID.";if($script:session -and -not $script:session.HasExited){Stop-Session};$timer.Stop()}})
$form.Add_FormClosed({Invoke-LauncherSafe 'Launcher shutdown' {Add-Output "Launcher FormClosed host pid=$PID."}})
[void]$form.ShowDialog()
