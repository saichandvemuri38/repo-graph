#requires -Version 7.0
<#
  Claude Code hook: PreToolUse for Edit | Write | MultiEdit. The edit gate.
  Looks up how much depends on the file about to change. Above the warn level it tells Claude; at or above the block level it
  stops the edit (exit 2) until the user has agreed and `work.ps1 confirm` has been run. Config: .claude/config/policy.json.
#>
foreach ($m in 'Core', 'Git', 'Template', 'Session') { Import-Module (Join-Path $PSScriptRoot "../lib/Atlas.$m.psm1") -DisableNameChecking }
try {
    $hook = Read-HookInput
    $policy = Get-AtlasPolicy
    $gate = $policy.editGate
    if ($gate.mode -eq 'off') { exit 0 }

    $path = if ($hook.tool_input -is [hashtable]) { $hook.tool_input['file_path'] } else { $null }
    if (-not $path) { exit 0 }
    $rel = ConvertTo-RepoPath $path
    if (-not $rel) { exit 0 }
    foreach ($skip in $gate.skipPaths) { if ($rel.StartsWith($skip, [StringComparison]::OrdinalIgnoreCase)) { exit 0 } }
    if ($gate.skipTests -and (Test-TestPath $rel)) { exit 0 }
    if (-not (Test-Path (Join-Path (Get-AtlasRoot) $rel))) { exit 0 }      # a new file has no dependants yet

    $gates = Get-Gates
    $entry = if ($gates.ContainsKey($rel)) { $gates[$rel] } else { $null }
    $fresh = $entry -and $entry.checkedAt -and (([DateTime]::UtcNow - (ConvertFrom-Stamp $entry.checkedAt)).TotalMinutes -lt [int]$gate.cacheMinutes)
    if (-not $fresh) {
        $impact = Invoke-EngineJson @('impact', $rel)
        if ($impact.ContainsKey('error')) { exit 0 }                        # a language or file the graph does not cover
        $affected = [int]$impact.affected
        $flows = @($impact.processes).Count
        $risk = Get-RiskLevel -Affected $affected -Flows $flows
        Save-Gate -Path $rel -Risk $risk -Affected $affected -Flows $flows -IsEntry ([bool]$impact.is_entry)
        $entry = (Get-Gates)[$rel]
        Add-Journal -Type gate -Data @{ target = $rel; risk = $risk; affected = $affected; flows = $flows }
    }
    $risk = $entry.risk
    $confirmed = Test-GateConfirmed $entry
    $summary = "$($entry.affected) dependant symbol(s) and $($entry.flows) flow(s) depend on it"

    if ($gate.mode -eq 'block' -and (Test-RiskAtLeast $risk $gate.blockAt) -and -not $confirmed) {
        Add-Journal -Type gate -Data @{ target = $rel; risk = $risk; affected = [int]$entry.affected; flows = [int]$entry.flows; blocked = $true }
        [Console]::Error.WriteLine("CodeAtlas edit gate: '$rel' is $risk risk to change ($summary). Do not edit it yet. " +
            "Run atlas_impact on the symbol you plan to change, tell the user what depends on it, and ask whether to go ahead. " +
            "Once they agree, run: pwsh -NoProfile -File .claude/scripts/work.ps1 confirm -Target $rel   then retry the edit.")
        exit 2
    }
    if (Test-RiskAtLeast $risk $gate.warnAt) {
        Write-HookContext -EventName PreToolUse -Text "CodeAtlas: '$rel' is $risk risk to change ($summary). Check atlas_impact for the symbol you are editing and keep the change small."
    }
}
catch { Write-AtlasLog "pre-edit failed: $_" 'error' }
exit 0
