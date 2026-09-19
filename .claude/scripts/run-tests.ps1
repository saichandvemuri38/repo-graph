#requires -Version 7.0
<#
  Runs the project's tests the way .claude/config/tests.json says, and records the result in the active task.
    run-tests.ps1                 the runners that fit the languages of the files you changed (all detected runners when git is not used)
    run-tests.ps1 -All            every detected runner
    run-tests.ps1 -Runner maven   one runner by name
  Prints each runner's verdict and the end of its output. Exit code 1 when a runner fails. Only commands from tests.json are run.
#>
[CmdletBinding()]
param([string]$Runner, [switch]$All, [int]$TimeoutSeconds = 900)
foreach ($m in 'Core', 'Git', 'Template', 'Session') { Import-Module (Join-Path $PSScriptRoot "../lib/Atlas.$m.psm1") -DisableNameChecking }

try {
    $root = Get-AtlasRoot
    $runners = @((Read-JsonFile (Join-Path (Get-ClaudeDir) 'config/tests.json')).runners)
    # A runner applies when one of its `detect` names exists in the repo root (wildcards such as test_*.py are allowed) and none of `unless` does.
    function Test-Present([string]$Name) {
        if ($Name -match '[*?]') { return [bool](Get-ChildItem -Path $root -Filter $Name -File -ErrorAction SilentlyContinue | Select-Object -First 1) }
        Test-Path (Join-Path $root $Name)
    }
    $detected = @($runners | Where-Object {
            $r = $_
            @($r.detect | Where-Object { Test-Present $_ }).Count -gt 0 -and -not ($r.ContainsKey('unless') -and @($r.unless | Where-Object { Test-Present $_ }).Count -gt 0)
        })
    $chosen = $detected
    if ($Runner) { $chosen = @($runners | Where-Object { $_.name -eq $Runner }) }
    elseif (-not $All -and (Test-GitRepo)) {
        $changed = @((Invoke-Git @('diff', '--name-only', 'HEAD')).StdOut -split "`r?`n") + @((Invoke-Git @('ls-files', '--others', '--exclude-standard')).StdOut -split "`r?`n")
        $languages = @($changed | Where-Object { $_ -and -not $_.StartsWith('.claude/') } | ForEach-Object { Get-FileLanguage $_ } | Where-Object { $_ } | Sort-Object -Unique)
        if ($languages.Count) { $chosen = @($chosen | Where-Object { @($_.languages | Where-Object { $languages -contains $_ }).Count -gt 0 }) }
        if (-not $chosen.Count) { $changedLanguages = $languages }
    }
    if (-not $chosen.Count) {
        $known = ($runners | ForEach-Object { "$($_.name) ($($_.languages -join '/'), looks for: $($_.detect -join ', '))" }) -join '; '
        $why = if ($changedLanguages) { " for the changed $($changedLanguages -join '/') files" } else { '' }
        Write-Output "No test runner applies$why. Detected here: $(if ($detected.Count) { ($detected.name -join ', ') } else { 'none' }). Configured: $known. Add one to .claude/config/tests.json, or pass -All or -Runner <name>."
        exit 0
    }

    $shell = (Get-Process -Id $PID).Path
    $failed = 0
    foreach ($r in $chosen) {
        $started = Get-Date
        $res = Invoke-Process -File $shell -Arguments @('-NoProfile', '-Command', $r.command) -WorkingDirectory $root -TimeoutSeconds $TimeoutSeconds
        $outcome = if ($res.ExitCode -eq 0) { 'pass' } else { 'fail'; $failed++ }
        $secs = [Math]::Round(((Get-Date) - $started).TotalSeconds, 1)
        Write-Output "== $($r.name): $($r.command) => $outcome ($secs s)"
        $tail = @(($res.StdOut + "`n" + $res.StdErr) -split "`r?`n" | Where-Object { $_.Trim() }) | Select-Object -Last 30
        $tail | ForEach-Object { Write-Output "   $_" }
        $s = Get-Session
        if ($s) { Add-SessionTest -Session $s -Command $r.command -Result $outcome; Save-Session $s }
        Add-Journal -Type test -Data @{ command = $r.command; result = $outcome }
    }
    exit $(if ($failed) { 1 } else { 0 })
}
catch {
    Write-AtlasLog "run-tests failed: $_" 'error'
    [Console]::Error.WriteLine("run-tests.ps1 failed: $_")
    exit 1
}
