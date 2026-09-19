#requires -Version 7.0
<#
  Claude Code hook: PreToolUse for Edit | Write | MultiEdit. The edit gate.
  Works out what is about to change and how much depends on it. For Edit and MultiEdit it finds the functions, methods or classes
  the edit falls inside and checks those; for Write, or an edit outside any symbol, it checks the whole file. Above the warn level it
  tells Claude; at or above the block level it stops the edit (exit 2) until the user has agreed and `work.ps1 confirm` has been run
  for the file. Config: .claude/config/policy.json.
#>
foreach ($m in 'Core', 'Git', 'Template', 'Session') { Import-Module (Join-Path $PSScriptRoot "../lib/Atlas.$m.psm1") -DisableNameChecking }

function Get-EditedSymbols {
    <# Ids of the symbols an Edit or MultiEdit touches, or an empty list when that cannot be told (Write, text not found, module-level code). #>
    param($Hook, [string]$Rel, [string]$Full)
    $ti = if ($Hook.tool_input -is [hashtable]) { $Hook.tool_input } elseif ($Hook.toolInput -is [hashtable]) { $Hook.toolInput } else { @{} }
    $olds = switch ($Hook.tool_name) {
        'Edit' { @($ti['old_string']) }
        'MultiEdit' { @($ti['edits'] | ForEach-Object { $_['old_string'] }) }
        default { @() }
    }
    if (-not $olds.Count -or @($olds | Where-Object { -not $_ }).Count) { return @() }
    $text = [IO.File]::ReadAllText($Full)
    $ids = [System.Collections.Generic.List[string]]::new()
    foreach ($old in $olds) {
        $at = $text.IndexOf($old, [StringComparison]::Ordinal)
        if ($at -lt 0) { return @() }
        $start = 1 + ($text.Substring(0, $at).Split("`n").Count - 1)
        $end = $start + ($old.TrimEnd("`r", "`n").Split("`n").Count - 1)
        $found = (Invoke-EngineJson @('at', $Rel, "$start", "$end")).symbols
        if (-not @($found).Count) { return @() }
        foreach ($f in $found) { if (-not $ids.Contains($f.id)) { $ids.Add($f.id) } }
    }
    @($ids | Select-Object -First 6)
}

function Get-GateEntry {
    <# The recorded impact of one file or symbol, worked out again when it is older than editGate.cacheMinutes. #>
    param([string]$Key, [int]$CacheMinutes)
    $gates = Get-Gates
    $entry = if ($gates.ContainsKey($Key)) { $gates[$Key] } else { $null }
    $fresh = $entry -and $entry.checkedAt -and $entry.risk -ne 'UNKNOWN' -and (([DateTime]::UtcNow - (ConvertFrom-Stamp $entry.checkedAt)).TotalMinutes -lt $CacheMinutes)
    if ($fresh) { return $entry }
    $impact = Invoke-EngineJson @('impact', $Key)
    if ($impact.ContainsKey('error')) { return $null }                      # a language or file the graph does not cover
    $affected = [int]$impact.affected
    $flows = @($impact.processes).Count
    $risk = Get-RiskLevel -Affected $affected -Flows $flows
    Save-Gate -Path $Key -Risk $risk -Affected $affected -Flows $flows -IsEntry ([bool]$impact.is_entry)
    Add-Journal -Type gate -Data @{ target = $Key; risk = $risk; affected = $affected; flows = $flows }
    (Get-Gates)[$Key]
}

try {
    $hook = Read-HookInput
    $policy = Get-AtlasPolicy
    $gate = $policy.editGate
    if ($gate.mode -eq 'off') { exit 0 }

    $path = Get-HookFilePath $hook
    if (-not $path) { exit 0 }
    $rel = ConvertTo-RepoPath $path
    if (-not $rel) { exit 0 }
    foreach ($skip in $gate.skipPaths) { if ($rel.StartsWith($skip, [StringComparison]::OrdinalIgnoreCase)) { exit 0 } }
    if ($gate.skipTests -and (Test-TestPath $rel)) { exit 0 }
    $full = Join-Path (Get-AtlasRoot) $rel
    if (-not (Test-Path $full)) { exit 0 }                                    # a new file has no dependants yet

    $symbols = @(Get-EditedSymbols -Hook $hook -Rel $rel -Full $full)
    $keys = if ($symbols.Count) { $symbols } else { @($rel) }
    $worst = $null; $worstKey = ''
    foreach ($k in $keys) {
        $e = Get-GateEntry -Key $k -CacheMinutes ([int]$gate.cacheMinutes)
        if (-not $e) { continue }
        $bigger = -not $worst -or ($e.risk -ne $worst.risk -and (Test-RiskAtLeast $e.risk $worst.risk)) -or ($e.risk -eq $worst.risk -and [int]$e.affected -gt [int]$worst.affected)
        if ($bigger) { $worst = $e; $worstKey = $k }
    }
    if (-not $worst) { exit 0 }

    $fileEntry = (Get-Gates)[$rel]
    $confirmed = $fileEntry -and (Test-GateConfirmed $fileEntry)
    $what = if ($symbols.Count) { "the code you are changing ($((($worstKey -split '::', 2)[-1])))" } else { "'$rel'" }
    $summary = "$($worst.affected) dependant symbol(s) and $($worst.flows) flow(s) depend on it"

    if ($gate.mode -eq 'block' -and (Test-RiskAtLeast $worst.risk $gate.blockAt) -and -not $confirmed) {
        Add-Journal -Type gate -Data @{ target = $worstKey; risk = $worst.risk; affected = [int]$worst.affected; flows = [int]$worst.flows; blocked = $true }
        [Console]::Error.WriteLine("CodeAtlas edit gate: $what in '$rel' is $($worst.risk) risk to change ($summary). Do not edit it yet. " +
            "Run atlas_impact on it, tell the user what depends on it, and ask whether to go ahead. " +
            "Once they agree, run: pwsh -NoProfile -File .claude/scripts/work.ps1 confirm -Target $rel   then retry the edit.")
        exit 2
    }
    if (Test-RiskAtLeast $worst.risk $gate.warnAt) {
        Write-HookContext -EventName PreToolUse -Text "CodeAtlas: $what in '$rel' is $($worst.risk) risk to change ($summary). Check atlas_impact for it and keep the change small."
    }
}
catch { Write-AtlasLog "pre-edit failed: $_" 'error' }
exit 0
