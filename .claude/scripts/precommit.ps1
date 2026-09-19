#requires -Version 7.0
<#
  The check to run before every commit.
    precommit.ps1 [-Subject "short commit subject"] [-Json] [-Strict]
  It refreshes the graph, compares the working tree with HEAD, and reports: symbols removed while still used, the risk of the change,
  the tests to run, Python security leads in the changed files, and a commit message draft. Advisory: the exit code is 0 unless -Strict
  is given and the verdict is BLOCK. Output: .claude/atlas/work/precommit-report.md and .claude/atlas/work/commit-draft.txt.
#>
[CmdletBinding()]
param([string]$Subject, [switch]$Json, [switch]$Strict)
foreach ($m in 'Core', 'Git', 'Template', 'Session') { Import-Module (Join-Path $PSScriptRoot "../lib/Atlas.$m.psm1") -DisableNameChecking }

function Get-ShortName([string]$Id) { ($Id -split '::', 2)[-1] }
function Get-StatusWord([string]$Code) { switch ($Code) { 'M' { 'modified' } 'A' { 'added' } 'D' { 'removed' } default { $Code } } }

try {
    $policy = Get-AtlasPolicy
    [void](Update-Graph)
    $c = Invoke-EngineJson @('changes')
    $s = Get-Session
    $task = if ($s) { $s.task.title } else { '' }
    $files = @($c.files.Keys | Sort-Object)
    $affected = [int]$c.affected
    $flows = @($c.processes).Count
    $risk = Get-RiskLevel -Affected $affected -Flows $flows

    $blockers = [System.Collections.Generic.List[string]]::new()
    $warnings = [System.Collections.Generic.List[string]]::new()

    if ($files.Count -eq 0) {
        $report = "# Pre-commit check: OK`n`nNothing has changed since $($c.baseline).`n"
        $verdict = 'OK'
    }
    else {
        foreach ($id in @($c.removed)) {
            $users = @($c.dependants[$id] | Where-Object { -not (Test-TestPath $_.src) } | ForEach-Object { Get-ShortName $_.src } | Sort-Object -Unique)
            if ($users.Count) {
                $line = "Removed '$(Get-ShortName $id)' is still used by $(($users | Select-Object -First 4) -join ', ')."
                if ($policy.commit.blockOnRemovedStillReferenced) { $blockers.Add($line) } else { $warnings.Add($line) }
            }
        }
        if ($policy.commit.requireTask -and -not $s) { $blockers.Add('No active task. Start one with work.ps1 start -Title "...".') }
        if (Test-RiskAtLeast $risk 'HIGH') { $warnings.Add("The change reaches $affected dependant symbol(s) across $flows flow(s). Review the dependants before committing.") }

        # Python security leads, limited to the changed files
        $security = @()
        if ((Get-AtlasConfig).checks.taintOnPrecommit -and @($files | Where-Object { $_.EndsWith('.py') }).Count) {
            try {
                $t = Invoke-EngineJson @('taint')
                $security = @($t.findings | Where-Object { $files -contains $_.file -or $files -contains (($_.function -split '::')[0]) -or $files -contains (($_.source_function -split '::')[0]) } |
                        ForEach-Object { "$($_.severity) $($_.title) ($($_.cwe)) at $($_.file):$($_.line), from $($_.source); confidence $($_.confidence)" })
            }
            catch { Write-AtlasLog "taint check skipped: $_" 'error' }
            if ($security.Count) { $warnings.Add("$($security.Count) security lead(s) in the changed Python code. Read each path before committing.") }
        }

        # tests
        $languages = @($files | ForEach-Object { Get-FileLanguage $_ } | Where-Object { $_ } | Sort-Object -Unique)
        $runners = (Read-JsonFile (Join-Path (Get-ClaudeDir) 'config/tests.json')).runners
        $tests = @()
        foreach ($r in $runners) {
            $present = @($r.detect | Where-Object { Test-Path (Join-Path (Get-AtlasRoot) $_) }).Count -gt 0
            if ($present -and @($r.languages | Where-Object { $languages -contains $_ }).Count) { $tests += "``$($r.command)`` ($($r.name))" }
        }
        if (@($c.tests).Count) { $tests += "The graph says these tests exercise the changes: $((@($c.tests) | Select-Object -First 6 | ForEach-Object { Get-ShortName $_ }) -join ', ')" }
        if ($s) {
            $lastEdit = ($s.files.Values | ForEach-Object { $_.lastAt } | Sort-Object | Select-Object -Last 1)
            $lastPass = (@($s.tests) | Where-Object { $_.result -eq 'pass' } | ForEach-Object { $_.at } | Sort-Object | Select-Object -Last 1)
            if ($lastEdit -and (-not $lastPass -or $lastPass -lt $lastEdit)) {
                $warnings.Add('No passing test run is recorded since the last edit. Run: pwsh -NoProfile -File .claude/scripts/run-tests.ps1')
            }
        }

        # commit message draft
        $byFile = foreach ($f in $files) {
            $prefix = "$f::"
            $parts = @()
            foreach ($kind in 'modified', 'added', 'removed') {
                $names = @(@($c[$kind]) | Where-Object { $_.StartsWith($prefix) } | ForEach-Object { $_.Substring($prefix.Length) })
                if ($names.Count) { $parts += "$kind $((($names | Select-Object -First 5) -join ', '))$(if ($names.Count -gt 5) { " (+$($names.Count - 5) more)" })" }
            }
            "$f" + $(if ($parts.Count) { ": $($parts -join '; ')" } else { " ($(Get-StatusWord $c.files[$f]))" })
        }
        $subjectText = if ($Subject) { $Subject } elseif ($task) { $task } else { 'Update ' + (($files | Select-Object -First 2) -join ', ') }
        $max = [int]$policy.commit.maxSubjectLength
        if ($subjectText.Length -gt $max) { $subjectText = $subjectText.Substring(0, $max - 3).TrimEnd() + '...' }
        $draft = Expand-Template -Name 'commit-message.md' -Data @{ subject = $subjectText; task = $task; changes = @($byFile | Select-Object -First 12); warnings = @($warnings) }
        $draftPath = Join-Path (Get-WorkDir) 'commit-draft.txt'
        Set-Content -Path $draftPath -Value $draft -Encoding utf8

        $verdict = if ($blockers.Count) { 'BLOCK' } elseif ($warnings.Count -or (Test-RiskAtLeast $risk 'HIGH')) { 'REVIEW' } else { 'OK' }
        $report = Expand-Template -Name 'precommit-report.md' -Data @{
            verdict = $verdict; task = $(if ($task) { $task } else { '(no active task)' }); risk = $risk; affected = $affected; flows = $flows; baseline = $c.baseline
            blockers = @($blockers); warnings = @($warnings); files = @($files | ForEach-Object { "``$_`` ($(Get-StatusWord $c.files[$_]))" })
            symbols = @(@(@($c.modified) | ForEach-Object { "$(Get-ShortName $_) (modified)" }) + @(@($c.added) | ForEach-Object { "$(Get-ShortName $_) (added)" }) + @(@($c.removed) | ForEach-Object { "$(Get-ShortName $_) (removed)" }) | Select-Object -First 20); tests = $tests; security = $security
            draftPath = (ConvertTo-RepoPath $draftPath)
        }
    }
    Set-Content -Path (Join-Path (Get-WorkDir) 'precommit-report.md') -Value $report -Encoding utf8
    Add-Journal -Type precommit -Data @{ verdict = $verdict; risk = $risk; blockers = $blockers.Count; warnings = $warnings.Count }

    if ($Json) {
        @{ verdict = $verdict; risk = $risk; affected = $affected; flows = $flows; blockers = @($blockers); warnings = @($warnings); files = $files } | ConvertTo-Json -Depth 5 -Compress
    }
    else { Write-Output $report }
    if ($Strict -and $verdict -eq 'BLOCK') { exit 1 }
}
catch {
    Write-AtlasLog "precommit failed: $_" 'error'
    [Console]::Error.WriteLine("precommit.ps1 failed: $_")
    exit 1
}
exit 0
