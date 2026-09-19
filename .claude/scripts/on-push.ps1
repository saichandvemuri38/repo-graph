#requires -Version 7.0
<#
  Git hook body for pre-push (installed by install.ps1). git runs it as: on-push.ps1 <remote name> <remote url>, with one line per ref on stdin.
  Every push updates the graph: it refreshes the index, rebuilds the HTML report, records a graph snapshot, works out the risk of what is being pushed, notes the push
  in the active task, and prints a short summary. It only blocks when config/policy.json sets push.mode to "block" and the risk reaches push.blockAt.
#>
param([Parameter(Position = 0)][string]$Remote = 'origin', [Parameter(Position = 1)][string]$Url = '')
foreach ($m in 'Core', 'Git', 'Template', 'Session') { Import-Module (Join-Path $PSScriptRoot "../lib/Atlas.$m.psm1") -DisableNameChecking }

function Write-Note([string]$Message) { [Console]::Error.WriteLine("[codeatlas] $Message") }

try {
    $policy = Get-AtlasPolicy
    $refs = @()
    $remoteSha = ''
    if ([Console]::IsInputRedirected) {
        foreach ($line in ([Console]::In.ReadToEnd() -split "`r?`n" | Where-Object { $_.Trim() })) {
            $p = $line -split '\s+'
            if ($p.Count -ge 4) {
                $refs += $p[2]
                if (-not $remoteSha -and $p[3] -notmatch '^0+$') { $remoteSha = $p[3] }
            }
        }
    }
    if ($policy.push.mode -eq 'off' -and -not (Get-AtlasConfig).graph.refreshOnPush) { exit 0 }

    $refreshed = if ((Get-AtlasConfig).graph.refreshOnPush) { Update-Graph } else { $true }
    $head = Get-GitHead
    $stats = Add-GraphSnapshot -Reason 'pre-push' -Sha $head

    $base = if ($remoteSha) { $remoteSha } else { Get-GitMergeBase }
    $risk = 'LOW'; $affected = 0; $flows = 0; $modified = 0; $removed = 0; $compared = $false
    if ($base) {
        try {
            $c = Invoke-EngineJson @('changes', '--base', $base)
            $affected = [int]$c.affected; $flows = @($c.processes).Count; $modified = @($c.modified).Count; $removed = @($c.removed).Count
            $risk = Get-RiskLevel -Affected $affected -Flows $flows
            $compared = $true
        }
        catch { Write-AtlasLog "push risk check skipped: $_" 'error' }
    }

    if ((Get-AtlasConfig).report.onPush) { [void](Update-Report) }
    $s = Get-Session
    if ($s) {
        $s.pushes = @($s.pushes) + [ordered]@{ at = Get-Stamp; remote = $Remote; risk = $risk; refs = @($refs) }
        Update-SessionGraph $s
        Save-Session $s
    }
    Add-Journal -Type push -Data @{ remote = $Remote; risk = $risk; refs = @($refs); symbols = $stats.symbols; edges = $stats.edges; affected = $affected }

    $what = if ($compared) { "$risk risk, $modified symbol(s) modified, $removed removed, $affected dependant(s), $flows flow(s)" } else { 'risk not computed (nothing on the remote to compare with)' }
    Write-Note "push to ${Remote}: $what. Graph $(if ($refreshed) { 'updated' } else { 'refresh failed' }): $($stats.symbols) symbols, $($stats.edges) edges."
    if ($policy.push.mode -eq 'block' -and (Test-RiskAtLeast $risk $policy.push.blockAt)) {
        Write-Note "push blocked: $risk risk is at or above policy.push.blockAt ($($policy.push.blockAt)). Review with atlas_changes, or change .claude/config/policy.json."
        exit 1
    }
}
catch { Write-AtlasLog "on-push failed: $_" 'error' }
exit 0
