#requires -Version 7.0
<#
  Claude Code hook: PostToolUse for Edit | Write | MultiEdit.
  Refreshes the graph for the file just changed, records the edit in the active task and the journal, and tells Claude at once if
  the edit removed a symbol that other code still uses. Never fails the tool call.
#>
foreach ($m in 'Core', 'Git', 'Template', 'Session') { Import-Module (Join-Path $PSScriptRoot "../lib/Atlas.$m.psm1") -DisableNameChecking }
try {
    $hook = Read-HookInput
    $path = if ($hook.tool_input -is [hashtable]) { $hook.tool_input['file_path'] } else { $null }
    if (-not $path) { exit 0 }
    $rel = ConvertTo-RepoPath $path
    if (-not $rel) { exit 0 }
    $policy = Get-AtlasPolicy
    foreach ($skip in $policy.editGate.skipPaths) { if ($rel.StartsWith($skip, [StringComparison]::OrdinalIgnoreCase)) { exit 0 } }

    $cfg = Get-AtlasConfig
    if ($cfg.graph.refreshOnEdit) { [void](Update-Graph) }
    $changes = Invoke-EngineJson @('changes')
    $prefix = "$rel::"
    $inFile = @(@($changes.modified) + @($changes.added) + @($changes.removed) | Where-Object { $_.StartsWith($prefix) })
    $orphans = @(@($changes.removed) | Where-Object {
            $_.StartsWith($prefix) -and $changes.dependants.ContainsKey($_) -and
            @($changes.dependants[$_] | Where-Object { -not (Test-TestPath $_.src) }).Count -gt 0
        } | ForEach-Object { $_.Substring($prefix.Length) })

    $s = Get-Session
    if ($s) {
        Register-FileEdit -Session $s -Path $rel
        Merge-SessionSymbols -Session $s -Modified @($changes.modified) -Added @($changes.added) -Removed @($changes.removed)
        Update-SessionGraph $s
        Save-Session $s
    }
    Add-Journal -Type edit -Data @{ file = $rel; symbolsChanged = $inFile.Count; removedStillReferenced = @($orphans) }

    if ($orphans.Count) {
        Write-HookContext -EventName PostToolUse -Text ("CodeAtlas: this edit removed " + ($orphans -join ', ') + " from $rel, but other code still refers to it. " +
            "Run atlas_impact on the removed name and update the callers, or restore it.")
    }
    elseif ($cfg.checks.taintOnEdit -and $rel.EndsWith('.py')) {
        $t = Invoke-EngineJson @('taint', $rel)
        if ($t.total -gt 0) {
            $first = $t.findings[0]
            Write-HookContext -EventName PostToolUse -Text "CodeAtlas security lead in ${rel}: $($first.title) ($($first.cwe)) at $($first.file):$($first.line) from $($first.source). Run atlas_taint for the path."
        }
    }
}
catch { Write-AtlasLog "on-edit failed: $_" 'error' }
exit 0
