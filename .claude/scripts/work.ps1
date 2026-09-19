#requires -Version 7.0
<#
  The task ledger for the daily work agent.
    work.ps1 start -Title "Add rate limiting" [-Description "..."] [-Accept "returns 429; has a test"] [-Force]
    work.ps1 status [-Json]
    work.ps1 note -Text "Chose a token bucket because ..."
    work.ps1 confirm -Target src/api/limits.py          the user agreed to edit a gated file
    work.ps1 test -Command "python -m pytest -q" -Result pass
    work.ps1 finish [-Abandon]                          writes the summary and a pull request description
    work.ps1 list
  State lives in .claude/atlas/work/ (see lib/Atlas.Session.psm1).
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][ValidateSet('start', 'status', 'note', 'confirm', 'test', 'finish', 'list')][string]$Action = 'status',
    [string]$Title,
    [string]$Description = '',
    [string[]]$Accept = @(),
    [string]$Text,
    [string]$Target,
    [string]$Command,
    [ValidateSet('pass', 'fail', 'skipped')][string]$Result,
    [switch]$Abandon,
    [switch]$Force,
    [switch]$Json
)
foreach ($m in 'Core', 'Git', 'Template', 'Session') { Import-Module (Join-Path $PSScriptRoot "../lib/Atlas.$m.psm1") -DisableNameChecking }
# -File mode cannot pass real arrays, so several items are separated with ';'  (-Accept "raises ValueError; has a test")
$Accept = @($Accept | ForEach-Object { $_ -split ';' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

function Stop-Work([string]$Message) { [Console]::Error.WriteLine($Message); exit 1 }

function Get-Short([string]$Sha) { if ($Sha.Length -gt 7) { $Sha.Substring(0, 7) } else { $Sha } }

try {
    switch ($Action) {
        'start' {
            if (-not $Title) { Stop-Work 'start needs -Title "what you are doing".' }
            $existing = Get-Session
            if ($existing -and -not $Force) {
                Stop-Work "A task is already active: '$($existing.task.title)'. Finish it with 'work.ps1 finish', or start a new one with -Force (the old one is marked abandoned)."
            }
            if ($existing) {
                Close-Session $existing -Status abandoned
                Add-Journal -Type task-finish -Data @{ status = 'abandoned'; id = $existing.id }
            }
            [void](Update-Graph)
            $s = New-Session -Title $Title -Description $Description -Acceptance $Accept
            Add-Journal -Type task-start -Data @{ title = $Title; branch = $s.branch }
            Write-Output "Started task '$Title' (session $($s.id))."
            Write-Output (Get-SessionContext)
        }
        'status' {
            if ($Json) { $s = Get-Session; if ($s) { $s | ConvertTo-Json -Depth 20 } else { '{}' } }
            else { Write-Output (Get-SessionContext) }
        }
        'note' {
            $s = Get-Session
            if (-not $s) { Stop-Work 'No active task. Start one with: work.ps1 start -Title "..."' }
            if (-not $Text) { Stop-Work 'note needs -Text.' }
            Add-SessionNote -Session $s -Text $Text
            Save-Session $s
            Add-Journal -Type note -Data @{ text = $Text }
            Write-Output 'Noted.'
        }
        'confirm' {
            if (-not $Target) { Stop-Work 'confirm needs -Target <file path>.' }
            $rel = Confirm-Gate -Target $Target
            Add-Journal -Type confirm -Data @{ target = $rel }
            Write-Output "Confirmed: edits to $rel are allowed."
        }
        'test' {
            $s = Get-Session
            if (-not $s) { Stop-Work 'No active task. Start one with: work.ps1 start -Title "..."' }
            if (-not $Command -or -not $Result) { Stop-Work 'test needs -Command and -Result (pass, fail or skipped).' }
            Add-SessionTest -Session $s -Command $Command -Result $Result -Note $Text
            Save-Session $s
            Add-Journal -Type test -Data @{ command = $Command; result = $Result }
            Write-Output "Recorded: $Command => $Result"
        }
        'list' {
            $dir = Join-Path (Get-WorkDir) 'sessions'
            $rows = if (Test-Path $dir) { @(Get-ChildItem $dir -Filter '*.json' | Sort-Object Name -Descending | Select-Object -First 15 | ForEach-Object { Read-JsonFile $_.FullName }) } else { @() }
            $active = Get-Session
            if ($active) { Write-Output "* $($active.id)  active    $($active.task.title)" }
            foreach ($r in $rows) { Write-Output "  $($r.id)  $(([string]$r.status).PadRight(9)) $($r.task.title)" }
            if (-not $active -and -not $rows) { Write-Output 'No tasks yet.' }
        }
        'finish' {
            $s = Get-Session
            if (-not $s) { Stop-Work 'No active task to finish.' }
            [void](Update-Graph)
            Update-SessionGraph $s
            $changes = try {
                if ($s.baseCommit) { Invoke-EngineJson @('changes', '--base', $s.baseCommit) } else { Invoke-EngineJson @('changes') }
            } catch { $null }
            $affected = if ($changes) { [int]$changes.affected } else { 0 }
            $flows = if ($changes) { @($changes.processes).Count } else { 0 }
            $risk = Get-RiskLevel -Affected $affected -Flows $flows
            $status = if ($Abandon) { 'abandoned' } else { 'finished' }
            $finishedAt = Get-Stamp
            $data = @{
                id = $s.id; task = $s.task.title; description = $s.task.description; acceptance = @($s.task.acceptance); status = $status
                startedAt = $s.startedAt; finishedAt = $finishedAt; branch = $s.branch
                graphDelta = "symbols $($s.graph.baseline.symbols) to $($s.graph.latest.symbols), edges $($s.graph.baseline.edges) to $($s.graph.latest.edges)"
                symbolsModified = @($s.symbols.modified).Count; symbolsAdded = @($s.symbols.added).Count; symbolsRemoved = @($s.symbols.removed).Count
                fileCount = $s.files.Count
                files = @($s.files.Keys | Sort-Object | ForEach-Object { "``$_`` ($($s.files[$_].edits) edit(s))" })
                commits = @($s.commits | ForEach-Object { "$(Get-Short ([string]$_.sha)) $($_.subject)" })
                pushes = @($s.pushes | ForEach-Object { "$($_.at) to $($_.remote), $($_.risk) risk" })
                tests = @($s.tests | ForEach-Object { "``$($_.command)``: $($_.result)" })
                notes = @($s.notes | ForEach-Object { $_.text })
                risk = $risk; affected = $affected; flows = $flows
            }
            $dir = Get-WorkDir
            $summaryPath = Join-Path $dir "summary-$($s.id).md"
            $prPath = Join-Path $dir "pr-$($s.id).md"
            Set-Content -Path $summaryPath -Value (Expand-Template -Name 'work-summary.md' -Data $data) -Encoding utf8
            Set-Content -Path $prPath -Value (Expand-Template -Name 'pr-description.md' -Data $data) -Encoding utf8
            Save-Session $s
            Add-Journal -Type task-finish -Data @{ status = $status; id = $s.id }
            Close-Session $s -Status $status
            Write-Output "Task '$($data.task)' $status. Graph: $($data.graphDelta). Risk of the whole task: $risk ($affected dependants, $flows flows)."
            $reportPath = if ((Get-AtlasConfig).report.onFinish) { Update-Report } else { $null }
            Write-Output "Summary:        $(ConvertTo-RepoPath $summaryPath)"
            Write-Output "PR description: $(ConvertTo-RepoPath $prPath)"
            if ($reportPath) { Write-Output "HTML report:    $reportPath" }
        }
    }
}
catch {
    Write-AtlasLog "work $Action failed: $_" 'error'
    Stop-Work "work.ps1 $Action failed: $_"
}
exit 0
