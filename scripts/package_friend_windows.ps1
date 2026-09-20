[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$StagingRoot,
    [Parameter(Mandatory=$true)][string]$Inputs,
    [Parameter(Mandatory=$true)][string]$Output,
    [string]$Python = 'python',
    [switch]$Archive
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$arguments = @((Join-Path $PSScriptRoot 'package_unity.py'), '--staging-root', $StagingRoot,
               '--inputs', $Inputs, '--output', $Output)
if ($Archive) { $arguments += '--archive' }
& $Python @arguments
exit $LASTEXITCODE
