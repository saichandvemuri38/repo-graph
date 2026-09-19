#requires -Version 7.0
<#
  Sets CodeAtlas and the daily work agent up in a repository. Run it from a repo that already has this .claude folder:
    pwsh -NoProfile -File .claude/scripts/install.ps1 -Target C:\work\my-repo
  Everything lives in the target's own .claude folder, engine included (Python, no server): it copies
  .claude/{agents,engine,instructions,lib,prompts,schemas,scripts,skills,templates} and config; merges .claude/settings.json
  (permissions and hooks), .mcp.json and CLAUDE.md; excludes generated files from git; installs the git hooks; builds the first
  graph and the one-file HTML report (.claude/atlas/report/index.html).
  Nothing you already have is overwritten unless you pass -Update (managed folders) or -Force (config too). Re-running is safe.
    -Update        refresh the managed folders (including the engine) from this repo
    -Force         also reset config/*.json to the defaults
    -NoHooks       skip git hooks
    -NoMcp         skip .mcp.json
    -DefaultAgent  make atlas-dev the default agent for this repo (settings.json "agent"), so plain `claude` starts it
    -SetupPython   create the engine's virtual environment (.claude/engine/.venv) and install its packages (needs network)
    -SkipIndex     do not build the graph and report now
    -Uninstall     remove the hooks, settings entries and MCP entry this script added (folders are left in place)
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Target,
    [switch]$Update, [switch]$Force, [switch]$NoHooks, [switch]$NoMcp, [switch]$DefaultAgent, [switch]$SetupPython, [switch]$SkipIndex, [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
foreach ($m in 'Core') { Import-Module (Join-Path $PSScriptRoot "../lib/Atlas.$m.psm1") -DisableNameChecking }

$Source = Get-AtlasRoot                                    # the repo this script belongs to (it has the .claude folder to copy)
$Engine = Get-EnginePath                                   # <source>/.claude/engine
if (-not (Test-Path $Target -PathType Container)) { throw "Not a folder: $Target" }
$TargetRoot = (Resolve-Path $Target).Path
$SameFolder = ([IO.Path]::GetFullPath($TargetRoot).TrimEnd('/', '\') -eq [IO.Path]::GetFullPath($Source).TrimEnd('/', '\'))
$TargetClaude = Join-Path $TargetRoot '.claude'
$Marker = '# codeatlas-hook'
$HookNames = 'post-commit', 'post-merge', 'post-checkout', 'post-rewrite', 'pre-push'
$Report = [System.Collections.Generic.List[string]]::new()
function Say([string]$Message) { $Report.Add($Message); Write-Host $Message }

function Test-OurHookEntry($Entry) { ($Entry | ConvertTo-Json -Depth 10 -Compress) -match '\.claude/scripts/(session-start|pre-edit|on-edit)\.ps1|scripts/reindex\.sh' }

function Get-GitPath([string]$What) {
    $r = Invoke-Process -File 'git' -Arguments @('-C', $TargetRoot, 'rev-parse', '--git-path', $What)
    if ($r.ExitCode -ne 0) { return $null }
    $p = $r.StdOut.Trim()
    if ([IO.Path]::IsPathRooted($p)) { $p } else { Join-Path $TargetRoot $p }
}
$IsGit = (Invoke-Process -File 'git' -Arguments @('-C', $TargetRoot, 'rev-parse', '--is-inside-work-tree')).StdOut.Trim() -eq 'true'

# ---------------------------------------------------------------------------------------------- uninstall
if ($Uninstall) {
    if ($IsGit) {
        $hooks = Get-GitPath 'hooks'
        foreach ($n in $HookNames) {
            $f = Join-Path $hooks $n
            if ((Test-Path $f) -and (Select-String -Path $f -SimpleMatch $Marker -Quiet)) { Remove-Item $f -Force; Say "removed git hook $n" }
        }
    }
    $sf = Join-Path $TargetClaude 'settings.json'
    $settings = Read-JsonFile $sf
    if ($settings) {
        if ($settings.ContainsKey('hooks')) {
            foreach ($event in @($settings.hooks.Keys)) {
                $kept = @($settings.hooks[$event] | Where-Object { -not (Test-OurHookEntry $_) })
                if ($kept.Count) { $settings.hooks[$event] = $kept } else { $settings.hooks.Remove($event) }
            }
            if ($settings.hooks.Count -eq 0) { $settings.Remove('hooks') }
        }
        if ($settings.agent -eq 'atlas-dev') { $settings.Remove('agent') }
        $settings | ConvertTo-Json -Depth 20 | Set-Content -Path $sf -Encoding utf8
        Say 'removed CodeAtlas hooks from .claude/settings.json'
    }
    $mf = Join-Path $TargetRoot '.mcp.json'
    $mcp = Read-JsonFile $mf
    if ($mcp -and $mcp.mcpServers -and $mcp.mcpServers.ContainsKey('codeatlas')) {
        $mcp.mcpServers.Remove('codeatlas')
        $mcp | ConvertTo-Json -Depth 10 | Set-Content -Path $mf -Encoding utf8
        Say 'removed the codeatlas MCP server from .mcp.json'
    }
    Say 'Done. The .claude folders and .claude/atlas/ were left in place; delete them by hand if you want them gone.'
    return
}

# ---------------------------------------------------------------------------------------------- 1. copy the managed folders
$copied = 0; $updated = 0; $kept = 0
function Copy-Managed([string]$Folder, [bool]$Overwrite, [string[]]$Skip = @()) {
    $src = Join-Path $Source ".claude/$Folder"
    foreach ($file in Get-ChildItem -Path $src -Recurse -File -Force) {
        $rel = [IO.Path]::GetRelativePath($src, $file.FullName)
        if ($Skip -contains $rel -or $rel -match '(^|[\\/])(__pycache__|\.venv|\.pytest_cache)([\\/]|$)' -or $rel -like '*.egg-info*') { continue }
        $dest = Join-Path $TargetClaude (Join-Path $Folder $rel)
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) { [void](New-Item -ItemType Directory -Path $destDir -Force) }
        if (-not (Test-Path $dest)) { Copy-Item $file.FullName $dest; $script:copied++ }
        elseif ($Overwrite -and (Get-FileHash $file.FullName).Hash -ne (Get-FileHash $dest).Hash) { Copy-Item $file.FullName $dest -Force; $script:updated++ }
        else { $script:kept++ }
    }
}
if (-not $SameFolder) {
    foreach ($f in 'agents', 'engine', 'instructions', 'lib', 'prompts', 'schemas', 'scripts', 'skills', 'templates') { Copy-Managed $f ([bool]($Update -or $Force)) }
    Copy-Managed 'config' ([bool]$Force) @('atlas.local.json')
    Say "copied $copied file(s), updated $updated, kept $kept that already existed"
}
else { Say 'the target is the engine folder itself: nothing to copy' }

# ---------------------------------------------------------------------------------------------- 2. Python for the engine (this machine only)
# The engine runs on plain Python, but the MCP server, clusters and JS/TS/Java/Go parsers need packages. Until you run
# setup-engine.ps1 (or pass -SetupPython), reuse this repo's virtual environment when it has one.
$localPath = Join-Path $TargetClaude 'config/atlas.local.json'
if (-not $SameFolder -and -not $SetupPython -and -not (Test-Path $localPath)) {
    foreach ($rel in '.venv/bin/python', '.venv/Scripts/python.exe', '.claude/engine/.venv/bin/python', '.claude/engine/.venv/Scripts/python.exe') {
        $candidate = Join-Path $Source $rel
        if (Test-Path $candidate) {
            @{ engine = @{ python = $candidate } } | ConvertTo-Json -Depth 4 | Set-Content -Path $localPath -Encoding utf8
            Say "python for the engine: $candidate (saved in .claude/config/atlas.local.json; run setup-engine.ps1 for a self-contained one)"
            break
        }
    }
}

# ---------------------------------------------------------------------------------------------- 3. CLAUDE.md
$claudeMd = Join-Path $TargetClaude 'CLAUDE.md'
if (-not $SameFolder) {
    $block = Get-Content -Raw -Path (Join-Path $Source '.claude/templates/target-claude.md') -Encoding utf8
    if (-not (Test-Path $claudeMd)) { Set-Content -Path $claudeMd -Value $block -Encoding utf8; Say 'wrote .claude/CLAUDE.md' }
    elseif (-not (Select-String -Path $claudeMd -SimpleMatch '<!-- codeatlas -->' -Quiet)) { Add-Content -Path $claudeMd -Value "`n$block" -Encoding utf8; Say 'appended the CodeAtlas rules to your .claude/CLAUDE.md' }
}

# ---------------------------------------------------------------------------------------------- 4. settings.json (permissions + hooks)
$fragment = Get-Content -Raw -Path (Join-Path $Source '.claude/templates/settings.fragment.json') -Encoding utf8 | ConvertFrom-Json -AsHashtable
$sf = Join-Path $TargetClaude 'settings.json'
$settings = Read-JsonFile $sf
if (-not $settings) { $settings = @{} }
if (-not $settings.ContainsKey('permissions')) { $settings.permissions = @{} }
if (-not $settings.permissions.ContainsKey('allow')) { $settings.permissions.allow = @() }
$settings.permissions.allow = @($settings.permissions.allow) + @($fragment.permissions.allow | Where-Object { @($settings.permissions.allow) -notcontains $_ })
if (-not $settings.ContainsKey('hooks')) { $settings.hooks = @{} }
foreach ($event in $fragment.hooks.Keys) {
    $existing = @(if ($settings.hooks.ContainsKey($event)) { $settings.hooks[$event] | Where-Object { -not (Test-OurHookEntry $_) } })
    $settings.hooks[$event] = @($existing) + @($fragment.hooks[$event])       # our entries are replaced, yours are kept
}
if ($DefaultAgent) { $settings.agent = 'atlas-dev' }
$settings | ConvertTo-Json -Depth 20 | Set-Content -Path $sf -Encoding utf8
Say ".claude/settings.json: permissions and hooks (session start, edit gate, edit tracking)$(if ($DefaultAgent) { ', default agent atlas-dev' })"

# ---------------------------------------------------------------------------------------------- 5. .mcp.json
if (-not $NoMcp) {
    $mf = Join-Path $TargetRoot '.mcp.json'
    $mcp = Read-JsonFile $mf
    if (-not $mcp) { $mcp = @{} }
    if (-not $mcp.ContainsKey('mcpServers')) { $mcp.mcpServers = @{} }
    $mcp.mcpServers.codeatlas = @{ command = 'pwsh'; args = @('-NoProfile', '-File', '.claude/scripts/atlas.ps1', 'serve') }
    $mcp | ConvertTo-Json -Depth 10 | Set-Content -Path $mf -Encoding utf8
    Say '.mcp.json: codeatlas MCP server (Claude Code asks once to approve it)'
}

# ---------------------------------------------------------------------------------------------- 6. git: excludes and hooks
if ($IsGit) {
    $exclude = Get-GitPath 'info/exclude'
    if (-not (Test-Path (Split-Path $exclude -Parent))) { [void](New-Item -ItemType Directory -Path (Split-Path $exclude -Parent) -Force) }
    if (-not (Test-Path $exclude)) { New-Item -ItemType File -Path $exclude -Force | Out-Null }
    $have = @(Get-Content $exclude)
    foreach ($pattern in '.claude/atlas/', '.claude/config/atlas.local.json', '.claude/engine/.venv/', '.claude/engine/**/__pycache__/') { if ($have -notcontains $pattern) { Add-Content -Path $exclude -Value $pattern } }
    Say 'git: generated files (.claude/atlas/, the engine venv and caches) are excluded from git status'
    if (-not $NoHooks) {
        $hooksDir = Get-GitPath 'hooks'
        if (-not (Test-Path $hooksDir)) { [void](New-Item -ItemType Directory -Path $hooksDir -Force) }
        foreach ($n in $HookNames) {
            $file = Join-Path $hooksDir $n
            if ((Test-Path $file) -and -not (Select-String -Path $file -SimpleMatch $Marker -Quiet)) {
                Say "skipped git hook ${n}: you already have one. Add a line that runs .claude/scripts/$(if ($n -eq 'pre-push') { 'on-push.ps1' } else { 'on-commit.ps1' }) to it."
                continue
            }
            $script = if ($n -eq 'pre-push') { 'on-push.ps1' } else { 'on-commit.ps1' }
            $callArgs = if ($n -eq 'pre-push') { '"$@"' } else { "$n `"`$@`"" }
            $tail = if ($n -eq 'pre-push') { 'exit $?' } else { 'exit 0' }
            $body = @(
                '#!/bin/sh', $Marker,
                'command -v pwsh >/dev/null 2>&1 || exit 0',
                'ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0',
                "[ -f `"`$ROOT/.claude/scripts/$script`" ] || exit 0",
                "pwsh -NoProfile -File `"`$ROOT/.claude/scripts/$script`" $callArgs",
                $tail, ''
            ) -join "`n"
            [IO.File]::WriteAllText($file, $body)
            if (-not $IsWindows) { & chmod +x $file }
        }
        Say "git hooks: $($HookNames -join ', ') (the graph updates on every commit, merge, checkout, rebase and push)"
    }
}
else { Say 'not a git repository: no git hooks installed (run git init, then install again)' }

# ---------------------------------------------------------------------------------------------- 7. python packages, first graph, first report
if ($SetupPython -and -not $SameFolder) {
    $setup = Join-Path $TargetClaude 'scripts/setup-engine.ps1'
    $r = Invoke-Process -File (Get-Process -Id $PID).Path -Arguments @('-NoProfile', '-File', $setup) -WorkingDirectory $TargetRoot -TimeoutSeconds 900
    Say $(if ($r.ExitCode -eq 0) { 'engine virtual environment created (.claude/engine/.venv)' } else { "engine setup failed: $($r.StdErr.Trim()) $($r.StdOut.Trim())" })
}
if (-not $SkipIndex) {
    $targetEngine = Join-Path $TargetClaude 'engine'
    $env:CODEATLAS_ENGINE = if ($SameFolder) { $Engine } else { $targetEngine }
    $r = Invoke-Engine -Root $TargetRoot -Arguments @('index') -TimeoutSeconds 600
    Say $(if ($r.ExitCode -eq 0) { "graph built: $(($r.StdOut -split "`r?`n")[0].Trim())" } else { "graph build failed: $($r.StdErr.Trim())" })
    $r = Invoke-Engine -Root $TargetRoot -Arguments @('--no-refresh', 'report') -TimeoutSeconds 600
    Say $(if ($r.ExitCode -eq 0) { "HTML report: $(($r.StdOut -split "`r?`n")[0].Trim())" } else { "report failed: $($r.StdErr.Trim())" })
    $env:CODEATLAS_ENGINE = $null
}

# ---------------------------------------------------------------------------------------------- 8. health check and next steps
$doctor = Join-Path $TargetClaude 'scripts/doctor.ps1'
if (Test-Path $doctor) {
    Write-Host ''
    $env:CODEATLAS_ROOT = $null
    $pwsh = (Get-Process -Id $PID).Path
    $d = Invoke-Process -File $pwsh -Arguments @('-NoProfile', '-File', $doctor) -WorkingDirectory $TargetRoot -TimeoutSeconds 180
    Write-Host $d.StdOut
}
Write-Host @"

Installed in $TargetRoot
  Work with the daily agent:   cd $TargetRoot ; claude --agent atlas-dev
  See the graph (HTML report): pwsh -NoProfile -File .claude/scripts/atlas.ps1 web --open        (or open .claude/atlas/report/index.html)
  Health check any time:       pwsh -NoProfile -File .claude/scripts/doctor.ps1
"@
