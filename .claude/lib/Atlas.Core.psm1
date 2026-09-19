#requires -Version 7.0
<#
  Atlas.Core: paths, configuration, running the CodeAtlas engine, hook input/output, risk levels.
  Every script in .claude/scripts imports this (directly or through Atlas.Session).
#>
Set-StrictMode -Version Latest

$script:ClaudeDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:Root = if ($env:CODEATLAS_ROOT) { (Resolve-Path $env:CODEATLAS_ROOT).Path } else { (Resolve-Path (Join-Path $script:ClaudeDir '..')).Path }
$script:RiskOrder = @('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
$script:ConfigCache = $null

# ------------------------------------------------------------------------------------------ paths

function Get-AtlasRoot { $script:Root }
function Get-ClaudeDir { $script:ClaudeDir }

function Get-WorkDir {
    $cfg = Get-AtlasConfig
    $dir = Join-Path $script:Root ($cfg.work.dir -replace '/', [IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path $dir)) { [void](New-Item -ItemType Directory -Path $dir -Force) }
    $dir
}

function ConvertTo-RepoPath {
    <# Repo-relative path with forward slashes, or $null when the path is outside the repo. #>
    param([Parameter(Mandatory)][string]$Path)
    $full = if ([IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $script:Root $Path }
    $full = [IO.Path]::GetFullPath($full)
    $root = [IO.Path]::GetFullPath($script:Root).TrimEnd('/', '\')
    if ($full.Length -gt $root.Length -and $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -and $full[$root.Length] -in '/', '\') {
        return $full.Substring($root.Length + 1).Replace('\', '/')
    }
    $null
}

function Test-TestPath {
    param([Parameter(Mandatory)][string]$RepoPath)
    $RepoPath -match '(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$|Tests?\.java$'
}

$script:ExtLanguage = @{ '.py' = 'python'; '.js' = 'javascript'; '.jsx' = 'javascript'; '.mjs' = 'javascript'; '.cjs' = 'javascript'; '.ts' = 'typescript'; '.tsx' = 'tsx'; '.java' = 'java'; '.go' = 'go' }

function Get-FileLanguage {
    param([Parameter(Mandatory)][string]$Path)
    $script:ExtLanguage[[IO.Path]::GetExtension($Path).ToLowerInvariant()]
}

# ------------------------------------------------------------------------------------------ JSON, config, schemas

function Merge-Hashtable {
    param([hashtable]$Base, [hashtable]$Over)
    $out = @{}
    foreach ($k in $Base.Keys) { $out[$k] = $Base[$k] }
    foreach ($k in $Over.Keys) {
        if ($out.ContainsKey($k) -and $out[$k] -is [hashtable] -and $Over[$k] -is [hashtable]) { $out[$k] = Merge-Hashtable $out[$k] $Over[$k] }
        else { $out[$k] = $Over[$k] }
    }
    $out
}

function Read-JsonFile {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    Get-Content -Raw -Path $Path -Encoding utf8 | ConvertFrom-Json -AsHashtable
}

function Test-AtlasSchema {
    <# Returns @() when the JSON text satisfies the named schema in .claude/schemas, else the error messages. #>
    param([Parameter(Mandatory)][string]$Json, [Parameter(Mandatory)][string]$Schema)
    $file = Join-Path $script:ClaudeDir "schemas/$Schema.schema.json"
    $errors = $null
    $ok = Test-Json -Json $Json -SchemaFile $file -ErrorAction SilentlyContinue -ErrorVariable errors
    if ($ok) { return @() }
    @($errors | ForEach-Object { $_.ToString() })
}

function Write-JsonFile {
    <# Validates against a schema (when given) and writes atomically, so a crash never leaves half a file. #>
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Data, [string]$Schema)
    $text = $Data | ConvertTo-Json -Depth 20
    if ($Schema) {
        $problems = @(Test-AtlasSchema -Json $text -Schema $Schema)
        if ($problems.Count) { throw "Refusing to write $Path; it does not match schema '$Schema': $($problems -join '; ')" }
    }
    $tmp = "$Path.$PID.tmp"
    Set-Content -Path $tmp -Value $text -Encoding utf8
    Move-Item -Path $tmp -Destination $Path -Force
}

# Built-in defaults, so a config file written by an older version (missing newer keys) still works.
$script:ConfigDefaults = @{
    engine = @{ path = ''; python = '' }
    work = @{ dir = '.claude/atlas/work'; journalMaxLines = 5000 }
    graph = @{ refreshOnSessionStart = $true; refreshOnEdit = $true; refreshOnCommit = $true; refreshOnPush = $true; timeoutSeconds = 120 }
    context = @{ journalTail = 8; maxFiles = 12 }
    checks = @{ taintOnEdit = $false; taintOnPrecommit = $true }
    report = @{ onPush = $true; onFinish = $true; onCommit = $false }
}

function Get-AtlasConfig {
    <# config/atlas.json overlaid with config/atlas.local.json (machine-specific, not committed). #>
    if ($script:ConfigCache) { return $script:ConfigCache }
    $dir = Join-Path $script:ClaudeDir 'config'
    $shared = Read-JsonFile (Join-Path $dir 'atlas.json')
    if (-not $shared) { throw "Missing $dir/atlas.json. Run .claude/scripts/install.ps1 again." }
    $local = Read-JsonFile (Join-Path $dir 'atlas.local.json')
    $merged = Merge-Hashtable (Merge-Hashtable $script:ConfigDefaults $shared) $(if ($local) { $local } else { @{} })
    $problems = @(Test-AtlasSchema -Json ($merged | ConvertTo-Json -Depth 10) -Schema 'atlas-config')
    if ($problems.Count) { throw "config/atlas*.json is invalid: $($problems -join '; ')" }
    $script:ConfigCache = $merged
    $merged
}

function Get-AtlasPolicy {
    $file = Join-Path $script:ClaudeDir 'config/policy.json'
    $text = Get-Content -Raw -Path $file -Encoding utf8
    $problems = @(Test-AtlasSchema -Json $text -Schema 'policy')
    if ($problems.Count) { throw "config/policy.json is invalid: $($problems -join '; ')" }
    $text | ConvertFrom-Json -AsHashtable
}

function Reset-AtlasCache { $script:ConfigCache = $null }

# ------------------------------------------------------------------------------------------ processes and the engine

function Invoke-Process {
    <# Runs a program with separate stdout/stderr capture and a timeout. Works the same on Windows, macOS and Linux. #>
    param(
        [Parameter(Mandatory)][string]$File,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $script:Root,
        [hashtable]$Environment = @{},
        [string]$StdIn,
        [int]$TimeoutSeconds = 120
    )
    $psi = [System.Diagnostics.ProcessStartInfo]::new($File)
    foreach ($a in $Arguments) { [void]$psi.ArgumentList.Add($a) }
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $PSBoundParameters.ContainsKey('StdIn')
    $psi.UseShellExecute = $false
    foreach ($k in $Environment.Keys) { $psi.Environment[$k] = [string]$Environment[$k] }
    $proc = [System.Diagnostics.Process]::Start($psi)
    if ($psi.RedirectStandardInput) { $proc.StandardInput.Write($StdIn); $proc.StandardInput.Close() }
    $out = $proc.StandardOutput.ReadToEndAsync()
    $err = $proc.StandardError.ReadToEndAsync()
    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
        try { $proc.Kill($true) } catch { }
        return [pscustomobject]@{ ExitCode = 124; StdOut = ''; StdErr = "timed out after $TimeoutSeconds s: $File" }
    }
    $proc.WaitForExit()
    [pscustomobject]@{ ExitCode = $proc.ExitCode; StdOut = $out.Result; StdErr = $err.Result }
}

function Get-EnginePath {
    <# The folder that holds atlas_engine/. It ships inside .claude (so a repo carries its own engine); config or env can point elsewhere. #>
    $cfg = Get-AtlasConfig
    $candidates = @($cfg.engine.path, $env:CODEATLAS_ENGINE, (Join-Path $script:ClaudeDir 'engine'), (Join-Path $script:Root 'atlas_engine/..')) | Where-Object { $_ }
    foreach ($c in $candidates) {
        if (Test-Path (Join-Path $c 'atlas_engine/__init__.py')) { return (Resolve-Path $c).Path }
    }
    throw "The CodeAtlas engine was not found. It should be in $(Join-Path $script:ClaudeDir 'engine'). Run: pwsh -File <source repo>/.claude/scripts/install.ps1 -Target <this repo> -Update"
}

function Get-EnginePython {
    $cfg = Get-AtlasConfig
    if ($env:CODEATLAS_PYTHON) { return $env:CODEATLAS_PYTHON }
    if ($cfg.engine.python) { return $cfg.engine.python }
    foreach ($base in @((Get-EnginePath), $script:Root)) {
        foreach ($rel in '.venv/bin/python', '.venv/Scripts/python.exe') {
            $candidate = Join-Path $base $rel
            if (Test-Path $candidate) { return $candidate }
        }
    }
    foreach ($name in 'python3', 'python') {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw 'No Python found. Install Python 3.10+, or run .claude/scripts/setup-engine.ps1 to create the engine virtual environment.'
}

function Invoke-Engine {
    <# Runs `python -m atlas_engine --root <repo> ...`. Returns ExitCode/StdOut/StdErr. #>
    param([Parameter(Mandatory)][string[]]$Arguments, [int]$TimeoutSeconds = 0, [string]$Root = $script:Root)
    $cfg = Get-AtlasConfig
    if ($TimeoutSeconds -le 0) { $TimeoutSeconds = [int]$cfg.graph.timeoutSeconds }
    $engine = Get-EnginePath
    $sep = [IO.Path]::PathSeparator
    $pythonPath = if ($env:PYTHONPATH) { "$engine$sep$($env:PYTHONPATH)" } else { $engine }
    Invoke-Process -File (Get-EnginePython) -Arguments (@('-m', 'atlas_engine', '--root', $Root) + $Arguments) -WorkingDirectory $Root `
        -Environment @{ PYTHONPATH = $pythonPath; PYTHONUTF8 = '1'; PYTHONIOENCODING = 'utf-8' } -TimeoutSeconds $TimeoutSeconds
}

function Invoke-EngineJson {
    <# Runs an engine command with --json and returns the parsed result. Throws when the engine fails or prints something else. #>
    param([Parameter(Mandatory)][string[]]$Arguments, [int]$TimeoutSeconds = 0)
    $r = Invoke-Engine -Arguments ($Arguments + '--json') -TimeoutSeconds $TimeoutSeconds
    $text = $r.StdOut.Trim()
    if (-not $text.StartsWith('{')) { throw "codeatlas $($Arguments -join ' ') failed (exit $($r.ExitCode)): $($r.StdErr.Trim()) $text" }
    $text | ConvertFrom-Json -AsHashtable
}

function Update-Graph {
    <# Brings the graph up to date. Only changed files are parsed. Returns the engine's one-line summary, or $null on failure. #>
    $r = Invoke-Engine -Arguments @('index', '--quiet')
    if ($r.ExitCode -ne 0) { Write-AtlasLog "index failed (exit $($r.ExitCode)): $($r.StdErr.Trim())" 'error'; return $false }
    $true
}

function Update-Report {
    <# Rebuilds the one-file HTML report from the current graph. Returns the report path, or $null on failure. #>
    $r = Invoke-Engine -Arguments @('--no-refresh', 'report') -TimeoutSeconds 300
    if ($r.ExitCode -ne 0) { Write-AtlasLog "report failed (exit $($r.ExitCode)): $($r.StdErr.Trim())" 'error'; return $null }
    '.claude/atlas/report/index.html'
}

function Get-GraphStats {
    try {
        $m = (Invoke-EngineJson @('status')).meta
        @{ symbols = [int]($m.symbolCount ?? 0); edges = [int]($m.edgeCount ?? 0); unresolved = [int]($m.unresolvedCount ?? 0) }
    }
    catch { @{ symbols = 0; edges = 0; unresolved = 0 } }
}

# ------------------------------------------------------------------------------------------ hooks: input, output, log

function Read-HookInput {
    <# Claude Code passes hook data as JSON on stdin. Returns an empty hashtable when there is none. #>
    try {
        if ([Console]::IsInputRedirected) {
            $text = [Console]::In.ReadToEnd()
            if ($text.Trim()) { return ($text | ConvertFrom-Json -AsHashtable) }
        }
    }
    catch { Write-AtlasLog "could not read hook input: $_" 'error' }
    @{}
}

function Get-HookFilePath {
    <# The file an edit hook is about, whatever the host calls the fields (Claude Code: tool_input.file_path; VS Code hooks use camelCase). #>
    param($Hook)
    foreach ($outer in 'tool_input', 'toolInput', 'input') {
        $inner = if ($Hook -is [System.Collections.IDictionary] -and $Hook.Contains($outer)) { $Hook[$outer] } else { $null }
        if ($inner -is [System.Collections.IDictionary]) {
            foreach ($key in 'file_path', 'filePath', 'path', 'file') { if ($inner.Contains($key) -and $inner[$key]) { return [string]$inner[$key] } }
        }
    }
    $null
}

function Write-HookContext {
    <# Gives Claude extra context from a hook (PreToolUse / PostToolUse / SessionStart-style JSON). #>
    param([Parameter(Mandatory)][string]$EventName, [Parameter(Mandatory)][string]$Text)
    @{ hookSpecificOutput = @{ hookEventName = $EventName; additionalContext = $Text } } | ConvertTo-Json -Compress -Depth 5
}

function Write-AtlasLog {
    param([Parameter(Mandatory)][string]$Message, [string]$Level = 'info')
    try {
        $file = Join-Path (Get-WorkDir) 'hook.log'
        if ((Test-Path $file) -and (Get-Item $file).Length -gt 200KB) { Move-Item $file "$file.old" -Force }
        Add-Content -Path $file -Value ("{0:o} [{1}] {2}" -f (Get-Date), $Level, $Message) -Encoding utf8
    }
    catch { }
}

# ------------------------------------------------------------------------------------------ risk

function Get-RiskLevel {
    <# The same table as the engine's risk rubric, with thresholds from config/policy.json. #>
    param([Parameter(Mandatory)][int]$Affected, [Parameter(Mandatory)][int]$Flows)
    $t = (Get-AtlasPolicy).riskThresholds
    if ($Affected -ge $t.critical.affected -or $Flows -ge $t.critical.flows) { return 'CRITICAL' }
    if ($Affected -ge $t.high.affected -or $Flows -ge $t.high.flows) { return 'HIGH' }
    if ($Affected -ge $t.medium.affected -or $Flows -ge $t.medium.flows) { return 'MEDIUM' }
    'LOW'
}

function Test-RiskAtLeast {
    param([Parameter(Mandatory)][string]$Level, [Parameter(Mandatory)][string]$Minimum)
    $script:RiskOrder.IndexOf($Level) -ge $script:RiskOrder.IndexOf($Minimum)
}

Export-ModuleMember -Function Get-FileLanguage, Get-AtlasRoot, Get-ClaudeDir, Get-WorkDir, ConvertTo-RepoPath, Test-TestPath, Merge-Hashtable, Read-JsonFile,
    Test-AtlasSchema, Write-JsonFile, Get-AtlasConfig, Get-AtlasPolicy, Reset-AtlasCache, Invoke-Process, Get-EnginePath, Get-EnginePython,
    Invoke-Engine, Invoke-EngineJson, Update-Graph, Update-Report, Get-GraphStats, Read-HookInput, Get-HookFilePath, Write-HookContext, Write-AtlasLog, Get-RiskLevel, Test-RiskAtLeast
