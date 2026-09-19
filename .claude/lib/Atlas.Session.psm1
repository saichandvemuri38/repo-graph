#requires -Version 7.0
<#
  Atlas.Session: what the daily agent is working on, remembered on disk so a new chat, a compaction or a restart loses nothing.

  .claude/atlas/work/
    current.json        the active task (schema: schemas/session.schema.json)
    journal.jsonl       every event, one JSON object per line (schema: schemas/journal-event.schema.json)
    gates.json          which files were impact-checked, and which the user confirmed
    sessions/<id>.json  finished tasks
    graph-snapshots.jsonl   graph size at each commit and push
#>
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'Atlas.Core.psm1') -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'Atlas.Git.psm1') -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'Atlas.Template.psm1') -DisableNameChecking

$script:ConfirmHours = 8

# Timestamps are 'yyyy-MM-dd HH:mm:ssZ' (UTC). The space instead of 'T' stops ConvertFrom-Json from turning them into local DateTime objects.
function Get-Stamp { [DateTime]::UtcNow.ToString('yyyy-MM-dd HH:mm:ss') + 'Z' }

function ConvertFrom-Stamp {
    param([Parameter(Mandatory)][string]$Stamp)
    [DateTime]::ParseExact($Stamp, 'yyyy-MM-dd HH:mm:ssZ', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal)
}

function Get-Slug {
    param([string]$Text)
    $s = ($Text.ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
    if ($s.Length -gt 30) { $s = $s.Substring(0, 30).Trim('-') }
    $s
}

# ------------------------------------------------------------------------------------------ the active task

function Get-SessionFile { Join-Path (Get-WorkDir) 'current.json' }

function Get-Session {
    $file = Get-SessionFile
    if (-not (Test-Path $file)) { return $null }
    try { Read-JsonFile $file } catch { Write-AtlasLog "current.json is unreadable: $_" 'error'; $null }
}

function Save-Session {
    param([Parameter(Mandatory)]$Session)
    Write-JsonFile -Path (Get-SessionFile) -Data $Session -Schema 'session'
}

function New-Session {
    param([Parameter(Mandatory)][string]$Title, [string]$Description = '', [string[]]$Acceptance = @())
    $stats = Get-GraphStats
    $now = Get-Date
    $slug = Get-Slug $Title
    $id = $now.ToString('yyyyMMdd-HHmmss') + $(if ($slug) { "-$slug" } else { '' })
    $session = [ordered]@{
        schemaVersion = 1
        id            = $id
        status        = 'active'
        task          = [ordered]@{ title = $Title; description = $Description; acceptance = @($Acceptance) }
        branch        = (Get-GitBranch)
        baseCommit    = (Get-GitHead)
        startedAt     = Get-Stamp
        graph         = [ordered]@{ baseline = $stats; latest = $stats }
        files         = [ordered]@{}
        symbols       = [ordered]@{ modified = @(); added = @(); removed = @() }
        commits       = @()
        pushes        = @()
        tests         = @()
        notes         = @()
    }
    Save-Session $session
    $session
}

function Close-Session {
    param([Parameter(Mandatory)]$Session, [ValidateSet('finished', 'abandoned')][string]$Status = 'finished')
    $Session.status = $Status
    $Session.finishedAt = Get-Stamp
    $dir = Join-Path (Get-WorkDir) 'sessions'
    if (-not (Test-Path $dir)) { [void](New-Item -ItemType Directory -Path $dir -Force) }
    Write-JsonFile -Path (Join-Path $dir "$($Session.id).json") -Data $Session -Schema 'session'
    Remove-Item -Path (Get-SessionFile) -Force -ErrorAction SilentlyContinue
    Clear-Gates
}

function Update-SessionGraph {
    param([Parameter(Mandatory)]$Session)
    $Session.graph.latest = Get-GraphStats
}

# ------------------------------------------------------------------------------------------ the journal

function Add-Journal {
    param(
        [Parameter(Mandatory)][ValidateSet('task-start', 'edit', 'gate', 'confirm', 'note', 'precommit', 'commit', 'push', 'test', 'refresh', 'task-finish', 'error')][string]$Type,
        [hashtable]$Data = @{}
    )
    $s = Get-Session
    $event = [ordered]@{ ts = Get-Stamp; session = $(if ($s) { $s.id } else { '' }); type = $Type; data = $Data }
    $text = $event | ConvertTo-Json -Depth 10 -Compress
    $problems = @(Test-AtlasSchema -Json $text -Schema 'journal-event')
    if ($problems.Count) { throw "Bad journal event: $($problems -join '; ')" }
    $file = Join-Path (Get-WorkDir) 'journal.jsonl'
    Add-Content -Path $file -Value $text -Encoding utf8
    if ((Get-Item $file).Length -gt 1MB) {
        $keep = [int](Get-AtlasConfig).work.journalMaxLines
        Set-Content -Path $file -Value (Get-Content -Path $file -Tail $keep -Encoding utf8) -Encoding utf8
    }
}

function Get-JournalTail {
    param([int]$Count = 8)
    $file = Join-Path (Get-WorkDir) 'journal.jsonl'
    if ($Count -le 0 -or -not (Test-Path $file)) { return @() }
    @(Get-Content -Path $file -Tail $Count -Encoding utf8 | ForEach-Object { try { $_ | ConvertFrom-Json -AsHashtable } catch { } } | Where-Object { $_ })
}

function Format-JournalEvent {
    param([Parameter(Mandatory)]$Event)
    $d = $Event.data
    $detail = switch ($Event.type) {
        'task-start' { "started: $($d.title)" }
        'edit' { "edited $($d.file)" + $(if ($d.removedStillReferenced) { " (removed symbols still referenced: $($d.removedStillReferenced -join ', '))" } else { '' }) }
        'gate' { "checked $($d.target): $($d.risk) ($($d.affected) affected)" + $(if ($d.blocked) { ', waiting for confirmation' } else { '' }) }
        'confirm' { "user confirmed edits to $($d.target)" }
        'note' { $d.text }
        'precommit' { "pre-commit check: $($d.verdict)" }
        'commit' { "committed $(([string]$d.sha).Substring(0, [Math]::Min(7, ([string]$d.sha).Length))): $($d.subject)" }
        'push' { "push to $($d.remote): $($d.risk) risk, graph now $($d.symbols) symbols" }
        'test' { "ran $($d.command): $($d.result)" }
        'refresh' { "graph refreshed ($($d.reason))" }
        'task-finish' { "finished: $($d.status)" }
        'error' { "error: $($d.message)" }
        default { $Event.type }
    }
    $time = if ($Event.ts.Length -ge 16) { $Event.ts.Substring(11, 5) } else { '' }
    "$time $detail"
}

function Add-GraphSnapshot {
    param([string]$Reason, [string]$Sha = '')
    $stats = Get-GraphStats
    $line = [ordered]@{ ts = Get-Stamp; reason = $Reason; sha = $Sha; symbols = $stats.symbols; edges = $stats.edges; unresolved = $stats.unresolved } | ConvertTo-Json -Compress
    Add-Content -Path (Join-Path (Get-WorkDir) 'graph-snapshots.jsonl') -Value $line -Encoding utf8
    $stats
}

# ------------------------------------------------------------------------------------------ recording work

function Register-FileEdit {
    param([Parameter(Mandatory)]$Session, [Parameter(Mandatory)][string]$Path)
    $now = Get-Stamp
    if ($Session.files.Contains($Path)) {
        $Session.files[$Path].edits = [int]$Session.files[$Path].edits + 1
        $Session.files[$Path].lastAt = $now
    }
    else { $Session.files[$Path] = [ordered]@{ edits = 1; firstAt = $now; lastAt = $now } }
}

function Merge-SessionSymbols {
    <# Adds symbol ids to the session's lists. A symbol that comes back leaves "removed"; one that is removed leaves "modified" and "added". #>
    param([Parameter(Mandatory)]$Session, [string[]]$Modified = @(), [string[]]$Added = @(), [string[]]$Removed = @())
    $s = $Session.symbols
    $back = @($Modified) + @($Added)
    $s.removed = @((@($s.removed) + $Removed | Where-Object { $back -notcontains $_ }) | Sort-Object -Unique)
    $s.modified = @((@($s.modified) + $Modified | Where-Object { $Removed -notcontains $_ }) | Sort-Object -Unique)
    $s.added = @((@($s.added) + $Added | Where-Object { $Removed -notcontains $_ }) | Sort-Object -Unique)
}

function Add-SessionNote {
    param([Parameter(Mandatory)]$Session, [Parameter(Mandatory)][string]$Text)
    $Session.notes = @($Session.notes) + [ordered]@{ at = Get-Stamp; text = $Text }
}

function Add-SessionTest {
    param([Parameter(Mandatory)]$Session, [Parameter(Mandatory)][string]$Command, [Parameter(Mandatory)][ValidateSet('pass', 'fail', 'skipped')][string]$Result, [string]$Note = '')
    $entry = [ordered]@{ command = $Command; result = $Result; at = Get-Stamp }
    if ($Note) { $entry.note = $Note }
    $Session.tests = @($Session.tests) + $entry
}

# ------------------------------------------------------------------------------------------ impact gates

function Get-GatesFile { Join-Path (Get-WorkDir) 'gates.json' }

function Get-Gates {
    $g = Read-JsonFile (Get-GatesFile)
    if ($g) { $g } else { @{} }
}

function Save-Gate {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Risk, [int]$Affected = 0, [int]$Flows = 0, [bool]$IsEntry = $false)
    $gates = Get-Gates
    $previous = if ($gates.ContainsKey($Path)) { $gates[$Path] } else { $null }
    $gates[$Path] = @{
        risk = $Risk; affected = $Affected; flows = $Flows; isEntry = $IsEntry; checkedAt = Get-Stamp
        confirmedAt = $(if ($previous -and $previous.confirmedAt) { $previous.confirmedAt } else { '' })
    }
    Write-JsonFile -Path (Get-GatesFile) -Data $gates
}

function Confirm-Gate {
    <# The user agreed to edit this file. Accepts a repo path or a symbol id (path::Name). #>
    param([Parameter(Mandatory)][string]$Target)
    $file = ($Target -split '::', 2)[0]
    $rel = ConvertTo-RepoPath $file
    if (-not $rel) { $rel = $file.Replace('\', '/') }
    $gates = Get-Gates
    $entry = if ($gates.ContainsKey($rel)) { $gates[$rel] } else { @{ risk = 'UNKNOWN'; affected = 0; flows = 0; isEntry = $false; checkedAt = '' } }
    $entry.confirmedAt = Get-Stamp
    $gates[$rel] = $entry
    Write-JsonFile -Path (Get-GatesFile) -Data $gates
    $rel
}

function Test-GateConfirmed {
    param([Parameter(Mandatory)]$Gate)
    if (-not $Gate.confirmedAt) { return $false }
    ([DateTime]::UtcNow - (ConvertFrom-Stamp $Gate.confirmedAt)).TotalHours -lt $script:ConfirmHours
}

function Clear-Gates { Remove-Item -Path (Get-GatesFile) -Force -ErrorAction SilentlyContinue }

# ------------------------------------------------------------------------------------------ context for the agent

function Get-SessionContext {
    <# The text injected when a session starts and shown by `work status`. #>
    $cfg = Get-AtlasConfig
    $s = Get-Session
    $stats = Get-GraphStats
    $data = @{
        hasTask    = $false
        graph      = "$($stats.symbols) symbols, $($stats.edges) edges, $($stats.unresolved) unresolved calls"
        branch     = (Get-GitBranch)
        root       = (Get-AtlasRoot)
    }
    if ($s) {
        $files = @($s.files.Keys | Sort-Object { $s.files[$_].lastAt } -Descending)
        $lastEdit = ($s.files.Values | ForEach-Object { $_.lastAt } | Sort-Object | Select-Object -Last 1)
        $lastTest = (@($s.tests) | ForEach-Object { $_.at } | Sort-Object | Select-Object -Last 1)
        $lastCommit = (@($s.commits) | ForEach-Object { $_.at } | Sort-Object | Select-Object -Last 1)
        $pending = @()
        if ($lastEdit -and (-not $lastCommit -or $lastEdit -gt $lastCommit)) { $pending += "Edits since the last commit ($(@($files).Count) file(s))." }
        if ($lastEdit -and (-not $lastTest -or $lastEdit -gt $lastTest)) { $pending += 'Tests have not been run since the last edit.' }
        $data.hasTask = $true
        $data.task = $s.task.title
        $data.description = $s.task.description
        $data.acceptance = @($s.task.acceptance)
        $data.id = $s.id
        $data.since = $s.startedAt
        $data.taskBranch = $s.branch
        $data.files = @($files | Select-Object -First ([int]$cfg.context.maxFiles) | ForEach-Object { "$_ ($($s.files[$_].edits) edit(s))" })
        $more = [Math]::Max(0, $files.Count - [int]$cfg.context.maxFiles)
        $data.moreFilesLine = if ($more) { "- ... and $more more`n" } else { '' }
        $data.symbolsModified = @($s.symbols.modified).Count
        $data.symbolsAdded = @($s.symbols.added).Count
        $data.symbolsRemoved = @($s.symbols.removed).Count
        $data.commits = @($s.commits | ForEach-Object { "$(([string]$_.sha).Substring(0, [Math]::Min(7, ([string]$_.sha).Length))) $($_.subject)" })
        $data.pending = $pending
        $data.notes = @($s.notes | Select-Object -Last 5 | ForEach-Object { $_.text })
        $data.graphDelta = "symbols $($s.graph.baseline.symbols) to $($s.graph.latest.symbols), edges $($s.graph.baseline.edges) to $($s.graph.latest.edges)"
        $data.journal = @(Get-JournalTail -Count ([int]$cfg.context.journalTail) | Where-Object { $_.session -eq $s.id } | ForEach-Object { Format-JournalEvent $_ })
    }
    Expand-Template -Name $(if ($s) { 'session-context.md' } else { 'session-none.md' }) -Data $data
}

Export-ModuleMember -Function Get-Stamp, ConvertFrom-Stamp, Get-Slug, Get-Session, Save-Session, New-Session, Close-Session, Update-SessionGraph, Add-Journal,
    Get-JournalTail, Format-JournalEvent, Add-GraphSnapshot, Register-FileEdit, Merge-SessionSymbols, Add-SessionNote, Add-SessionTest,
    Get-Gates, Save-Gate, Confirm-Gate, Test-GateConfirmed, Clear-Gates, Get-SessionContext
