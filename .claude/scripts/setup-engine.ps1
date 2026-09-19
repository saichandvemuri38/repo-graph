#requires -Version 7.0
<#
  Creates the engine's own Python virtual environment at .claude/engine/.venv and installs what the engine can use:
    mcp (the MCP server for Claude Code), networkx (clusters), tree-sitter-language-pack (JavaScript, TypeScript, Java, Go).
    setup-engine.ps1 [-Extras all|mcp|graph|langs] [-Python <path to python 3.10+>]
  Needs network access for pip. The engine works without it for Python code (standard library only).
#>
[CmdletBinding()]
param([ValidateSet('all', 'mcp', 'graph', 'langs')][string]$Extras = 'all', [string]$Python)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot '../lib/Atlas.Core.psm1') -DisableNameChecking

$engine = Get-EnginePath
$venv = Join-Path $engine '.venv'
if (-not $Python) {
    foreach ($name in 'python3', 'python') { $cmd = Get-Command $name -ErrorAction SilentlyContinue; if ($cmd) { $Python = $cmd.Source; break } }
}
if (-not $Python) { throw 'No Python found. Install Python 3.10 or newer, then run this again.' }
$ver = (Invoke-Process -File $Python -Arguments @('-c', 'import sys; print("%d.%d" % sys.version_info[:2])')).StdOut.Trim()
if ([version]$ver -lt [version]'3.10') { throw "Python $ver is too old; the engine needs 3.10 or newer ($Python)." }

if (-not (Test-Path $venv)) {
    Write-Output "Creating $venv with Python $ver ..."
    $r = Invoke-Process -File $Python -Arguments @('-m', 'venv', $venv) -TimeoutSeconds 300
    if ($r.ExitCode -ne 0) { throw "venv failed: $($r.StdErr)" }
}
$py = @('bin/python', 'Scripts/python.exe') | ForEach-Object { Join-Path $venv $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
Write-Output "Installing packages ($Extras) ..."
$r = Invoke-Process -File $py -Arguments @('-m', 'pip', 'install', '--quiet', "$engine[$Extras]") -TimeoutSeconds 900
if ($r.ExitCode -ne 0) { throw "pip failed: $($r.StdErr)" }
Write-Output "Done. The engine will use $py"
