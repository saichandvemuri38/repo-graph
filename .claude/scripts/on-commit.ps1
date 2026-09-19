#requires -Version 7.0
<#
  Git hook body for post-commit, post-merge, post-checkout and post-rewrite (installed by install.ps1).
    on-commit.ps1 <hook name> [git's own arguments]
  Refreshes the graph, records the commit in the active task, and adds a line to the graph snapshots. Never fails git.
#>
param([Parameter(Position = 0)][string]$Hook = 'post-commit')
foreach ($m in 'Core', 'Git', 'Template', 'Session') { Import-Module (Join-Path $PSScriptRoot "../lib/Atlas.$m.psm1") -DisableNameChecking }
try {
    if ((Get-AtlasConfig).graph.refreshOnCommit) { [void](Update-Graph) }
    if ((Get-AtlasConfig).report.onCommit) { [void](Update-Report) }
    $head = Get-GitHead
    [void](Add-GraphSnapshot -Reason $Hook -Sha $head)
    $s = Get-Session
    if ($s) {
        if ($Hook -eq 'post-commit') {
            $last = Get-LastCommit
            if ($last -and -not (@($s.commits) | Where-Object { $_.sha -eq $last.sha })) {
                $s.commits = @($s.commits) + [ordered]@{ sha = $last.sha; subject = $last.subject; at = Get-Stamp }
                Add-Journal -Type commit -Data @{ sha = $last.sha; subject = $last.subject }
            }
        }
        else { Add-Journal -Type refresh -Data @{ reason = $Hook } }
        Update-SessionGraph $s
        Save-Session $s
    }
}
catch { Write-AtlasLog "on-commit ($Hook) failed: $_" 'error' }
exit 0
