#requires -Version 7.0
<#
  Claude Code hook: SessionStart. Whatever this prints is added to Claude's context, so a new chat, a resume or a compaction
  starts by knowing the active task, what was touched, and what is still to do. Never fails the session.
#>
foreach ($m in 'Core', 'Git', 'Template', 'Session') { Import-Module (Join-Path $PSScriptRoot "../lib/Atlas.$m.psm1") -DisableNameChecking }
try {
    $hook = Read-HookInput
    if ((Get-AtlasConfig).graph.refreshOnSessionStart) {
        $ok = Update-Graph
        $s = Get-Session
        if ($s) {
            Update-SessionGraph $s
            Save-Session $s
            Add-Journal -Type refresh -Data @{ reason = 'session-start'; ok = [bool]$ok }
        }
    }
    Get-SessionContext
}
catch {
    Write-AtlasLog "session-start failed: $_" 'error'
    'CodeAtlas: the start-up check failed. See .claude/atlas/work/hook.log, or run .claude/scripts/doctor.ps1.'
}
exit 0
