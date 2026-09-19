#requires -Version 7.0
<#
  Checks that the graph, hooks, agent and settings are in place for this repo, and says how to fix anything that is not.
    doctor.ps1 [-Json]
  Exit code 1 when a check FAILs; WARN does not fail.
#>
[CmdletBinding()]
param([switch]$Json)
foreach ($m in 'Core', 'Git', 'Template', 'Session') { Import-Module (Join-Path $PSScriptRoot "../lib/Atlas.$m.psm1") -DisableNameChecking }

$results = [System.Collections.Generic.List[object]]::new()
function Add-Check([string]$Name, [string]$Status, [string]$Detail, [string]$Fix = '') { $results.Add([pscustomobject]@{ check = $Name; status = $Status; detail = $Detail; fix = $Fix }) }
$root = Get-AtlasRoot
$claude = Get-ClaudeDir

Add-Check 'PowerShell' $(if ($PSVersionTable.PSVersion.Major -ge 7) { 'PASS' } else { 'FAIL' }) "version $($PSVersionTable.PSVersion)" 'Install PowerShell 7 or newer (https://aka.ms/powershell).'

try {
    [void](Get-AtlasConfig); [void](Get-AtlasPolicy)
    Add-Check 'Config and policy' 'PASS' 'atlas.json, atlas.local.json and policy.json are valid'
}
catch { Add-Check 'Config and policy' 'FAIL' "$_" 'Fix the JSON named in the message, or run install.ps1 -Force to reset it.' }

$engineOk = $false
try {
    $engine = Get-EnginePath
    $py = Get-EnginePython
    $r = Invoke-Process -File $py -Arguments @('-c', 'import sys, atlas_engine; print(sys.version.split()[0])') -Environment @{ PYTHONPATH = $engine }
    if ($r.ExitCode -eq 0) { $engineOk = $true; Add-Check 'Engine' 'PASS' "python $($r.StdOut.Trim()) at $py; engine at $engine" }
    else { Add-Check 'Engine' 'FAIL' $r.StdErr.Trim() 'Run: pwsh -NoProfile -File .claude/scripts/setup-engine.ps1' }
}
catch { Add-Check 'Engine' 'FAIL' "$_" 'Run install.ps1 from the engine folder again.' }

if ($engineOk) {
    try {
        $st = Invoke-EngineJson @('status')
        $n = [int]($st.meta.symbolCount ?? 0)
        if ($n -gt 0) { Add-Check 'Graph' 'PASS' "$n symbols, $($st.meta.edgeCount) edges, last link $($st.meta.lastLink)" }
        else { Add-Check 'Graph' 'WARN' 'the graph is empty' 'Run: pwsh -NoProfile -File .claude/scripts/atlas.ps1 index' }
        $mcpOk = $false
        $mp = Join-Path $root '.mcp.json'
        $mcp = Read-JsonFile $mp
        if ($mcp -and $mcp.mcpServers -and $mcp.mcpServers.ContainsKey('codeatlas')) { $mcpOk = $true }
        Add-Check 'MCP server' $(if ($mcpOk) { 'PASS' } else { 'WARN' }) $(if ($mcpOk) { '.mcp.json registers codeatlas (approve it once in Claude Code)' } else { 'not in .mcp.json' }) 'Run install.ps1 (without -NoMcp).'
    }
    catch { Add-Check 'Graph' 'FAIL' "$_" 'Run: pwsh -NoProfile -File .claude/scripts/atlas.ps1 index' }
}

$reportFile = Join-Path $root '.claude/atlas/report/index.html'
Add-Check 'HTML report' $(if (Test-Path $reportFile) { 'PASS' } else { 'WARN' }) $(if (Test-Path $reportFile) { ".claude/atlas/report/index.html (open it in a browser, no server needed)" } else { 'not generated yet' }) 'Run: pwsh -NoProfile -File .claude/scripts/atlas.ps1 report --open'

$agent = Join-Path $claude 'agents/atlas-dev.md'
Add-Check 'Daily agent' $(if (Test-Path $agent) { 'PASS' } else { 'FAIL' }) $(if (Test-Path $agent) { 'agents/atlas-dev.md (start it with: claude --agent atlas-dev)' } else { 'agents/atlas-dev.md is missing' }) 'Run install.ps1 -Update.'

$sf = Read-JsonFile (Join-Path $claude 'settings.json')
$events = @('SessionStart', 'PreToolUse', 'PostToolUse')
$have = if ($sf -and $sf.hooks) { @($events | Where-Object { $sf.hooks.ContainsKey($_) -and (($sf.hooks[$_] | ConvertTo-Json -Depth 10 -Compress) -match '\.claude/scripts/') }) } else { @() }
Add-Check 'Claude Code hooks' $(if ($have.Count -eq $events.Count) { 'PASS' } else { 'FAIL' }) "$($have.Count) of $($events.Count) in .claude/settings.json ($($have -join ', '))" 'Run install.ps1 -Update.'

if (Test-GitRepo) {
    $hp = (Invoke-Git @('rev-parse', '--git-path', 'hooks')).StdOut.Trim()
    if (-not [IO.Path]::IsPathRooted($hp)) { $hp = Join-Path $root $hp }
    $names = 'post-commit', 'post-merge', 'post-checkout', 'post-rewrite', 'pre-push'
    $installed = @($names | Where-Object { $f = Join-Path $hp $_; (Test-Path $f) -and (Select-String -Path $f -SimpleMatch '# codeatlas-hook' -Quiet) })
    Add-Check 'Git hooks' $(if ($installed.Count -eq $names.Count) { 'PASS' } else { 'WARN' }) "$($installed.Count) of $($names.Count) installed" 'Run install.ps1 (a hook you already had is never overwritten).'
    $hasPwsh = [bool](Get-Command pwsh -ErrorAction SilentlyContinue)
    Add-Check 'pwsh on PATH' $(if ($hasPwsh) { 'PASS' } else { 'WARN' }) $(if ($hasPwsh) { 'git hooks and Claude Code hooks can start it' } else { 'the hooks call "pwsh" and cannot find it' }) 'Add PowerShell 7 to PATH.'
}
else { Add-Check 'Git' 'WARN' 'not a git repository, so no commit or push hooks' 'Run git init, then install.ps1.' }

$s = Get-Session
Add-Check 'Active task' 'PASS' $(if ($s) { "'$($s.task.title)' since $($s.startedAt)" } else { 'none (start one with work.ps1 start -Title "...")' })

if ($Json) { $results | ConvertTo-Json -Depth 4 -Compress }
else {
    foreach ($r in $results) {
        $mark = switch ($r.status) { 'PASS' { '[ OK ]' } 'WARN' { '[WARN]' } default { '[FAIL]' } }
        Write-Output ("{0} {1,-18} {2}" -f $mark, $r.check, $r.detail)
        if ($r.status -ne 'PASS' -and $r.fix) { Write-Output ("       fix: {0}" -f $r.fix) }
    }
}
if (@($results | Where-Object status -eq 'FAIL').Count) { exit 1 }
exit 0
