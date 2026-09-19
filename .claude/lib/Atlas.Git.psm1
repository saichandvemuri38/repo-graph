#requires -Version 7.0
<# Atlas.Git: the few git questions the scripts ask. Every function is safe to call outside a git repo. #>
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'Atlas.Core.psm1') -DisableNameChecking

function Invoke-Git {
    param([Parameter(Mandatory)][string[]]$Arguments, [int]$TimeoutSeconds = 30)
    Invoke-Process -File 'git' -Arguments (@('-C', (Get-AtlasRoot)) + $Arguments) -TimeoutSeconds $TimeoutSeconds
}

function Test-GitRepo {
    try { (Invoke-Git @('rev-parse', '--is-inside-work-tree')).StdOut.Trim() -eq 'true' } catch { $false }
}

function Get-GitValue {
    param([string[]]$Arguments)
    try {
        $r = Invoke-Git $Arguments
        if ($r.ExitCode -eq 0) { return $r.StdOut.Trim() }
    }
    catch { }
    ''
}

function Get-GitBranch { $b = Get-GitValue @('rev-parse', '--abbrev-ref', 'HEAD'); if ($b) { $b } else { '' } }
function Get-GitHead { Get-GitValue @('rev-parse', 'HEAD') }
function Get-GitUpstream { Get-GitValue @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}') }

function Get-LastCommit {
    $out = Get-GitValue @('log', '-1', '--format=%H%x1f%s')
    if (-not $out) { return $null }
    $sha, $subject = $out -split "`u{1f}", 2
    @{ sha = $sha; subject = $subject }
}

function Get-GitMergeBase {
    <# The commit a push or review should be compared against: the upstream, else origin's default branch, else nothing. #>
    param([string]$Preferred)
    foreach ($ref in @($Preferred, (Get-GitUpstream), 'origin/HEAD', 'origin/main', 'origin/master') | Where-Object { $_ }) {
        $base = Get-GitValue @('merge-base', 'HEAD', $ref)
        if ($base) { return $base }
    }
    ''
}

Export-ModuleMember -Function Invoke-Git, Test-GitRepo, Get-GitBranch, Get-GitHead, Get-GitUpstream, Get-LastCommit, Get-GitMergeBase
