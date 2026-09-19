#requires -Version 7.0
# Pester tests for the daily work agent's PowerShell (.claude/lib and .claude/scripts).
# Run:  pwsh -NoProfile -Command "Invoke-Pester tests/pester -Output Detailed"
# Needs: pwsh 7, git, and Python with the engine (a .venv in the repo, or python3 on the PATH).

BeforeDiscovery {
    $script:HaveGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
}

BeforeAll {
    $script:Owner = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path        # the repo that owns this .claude folder
    $script:Pwsh = (Get-Process -Id $PID).Path
    $script:Work = Join-Path ([IO.Path]::GetTempPath()) ("atlas-pester-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    [void](New-Item -ItemType Directory -Path $script:Work)
    $env:CODEATLAS_HOME = Join-Path $script:Work 'home'
    $py = Join-Path $script:Owner '.venv/bin/python'
    if (-not (Test-Path $py)) { $py = Join-Path $script:Owner '.venv/Scripts/python.exe' }
    if (Test-Path $py) { $env:CODEATLAS_PYTHON = $py }          # the engine's packages (MCP, clusters, parsers) when this repo has a venv

    function Invoke-Pwsh {
        param([string]$Script, [string[]]$Arguments = @(), [string]$StdIn, [string]$Cwd, [hashtable]$Env = @{})
        $core = Import-Module (Join-Path $script:Owner '.claude/lib/Atlas.Core.psm1') -DisableNameChecking -PassThru
        $splat = @{ File = $script:Pwsh; Arguments = (@('-NoProfile', '-File', $Script) + $Arguments); WorkingDirectory = $Cwd; Environment = $Env; TimeoutSeconds = 240 }
        if ($PSBoundParameters.ContainsKey('StdIn')) { $splat.StdIn = $StdIn }
        & $core { param($p) Invoke-Process @p } $splat
    }
    function Invoke-InRepo {
        param([string]$Name, [string[]]$Arguments = @(), [string]$StdIn)
        $p = @{ Script = (Join-Path $script:Repo ".claude/scripts/$Name"); Arguments = $Arguments; Cwd = $script:Repo }
        if ($PSBoundParameters.ContainsKey('StdIn')) { $p.StdIn = $StdIn }
        Invoke-Pwsh @p
    }
    function Get-HookJson([string]$Tool, [string]$RelPath) {
        @{ tool_name = $Tool; tool_input = @{ file_path = (Join-Path $script:Repo $RelPath) } } | ConvertTo-Json -Compress
    }
    function Invoke-Git {
        param([string[]]$Arguments)
        $out = & git -C $script:Repo -c user.email=t@t -c user.name=tester @Arguments 2>&1
        [pscustomobject]@{ Code = $LASTEXITCODE; Out = ($out -join "`n") }
    }
    function Read-Json([string]$Path) { Get-Content -Raw $Path | ConvertFrom-Json -AsHashtable }
    function Write-File([string]$Rel, [string]$Text) {
        $full = Join-Path $script:Repo $Rel
        [void](New-Item -ItemType Directory -Path (Split-Path $full -Parent) -Force)
        Set-Content -Path $full -Value $Text -Encoding utf8
    }
}

AfterAll {
    $env:CODEATLAS_HOME = $null; $env:CODEATLAS_PYTHON = $null; $env:CODEATLAS_ROOT = $null
    if ($script:Work -and (Test-Path $script:Work)) { Remove-Item -Recurse -Force $script:Work -ErrorAction SilentlyContinue }
}

Describe 'Template engine' {
    BeforeAll { Import-Module (Join-Path $script:Owner '.claude/lib/Atlas.Template.psm1') -Force -DisableNameChecking }

    It 'fills values, dotted paths and leaves unknown names empty' {
        Expand-TemplateText -Template 'a={{x}} b={{o.y}} c={{nope}}.' -Data @{ x = 1; o = @{ y = 'two' } } | Should -Be 'a=1 b=two c=.'
    }
    It 'repeats lists and shows blocks only when there is something to show' {
        $t = "Files:`n{{#each files}}- {{.}}`n{{/each}}{{#if note}}Note: {{note}}`n{{/if}}End"
        Expand-TemplateText -Template $t -Data @{ files = @('a', 'b'); note = '' } | Should -Be "Files:`n- a`n- b`nEnd"
        Expand-TemplateText -Template $t -Data @{ files = @(); note = 'hi' } | Should -Be "Files:`nNote: hi`nEnd"
    }
    It 'reads properties of items inside a list' {
        Expand-TemplateText -Template '{{#each r}}{{n}}={{v}};{{/each}}' -Data @{ r = @(@{ n = 'a'; v = 1 }, @{ n = 'b'; v = 2 }) } | Should -Be 'a=1;b=2;'
    }
    It 'treats 0 and false as empty for if' {
        Expand-TemplateText -Template '{{#if n}}x{{/if}}{{#if f}}y{{/if}}z' -Data @{ n = 0; f = $false } | Should -Be 'z'
    }
}

Describe 'Core: config, schemas, risk, paths' {
    BeforeAll {
        $env:CODEATLAS_ROOT = $script:Work
        Import-Module (Join-Path $script:Owner '.claude/lib/Atlas.Core.psm1') -Force -DisableNameChecking
    }
    AfterAll { $env:CODEATLAS_ROOT = $null }

    It 'ships valid config and policy' {
        { Get-AtlasConfig } | Should -Not -Throw
        (Get-AtlasPolicy).editGate.mode | Should -Be 'block'
    }
    It 'rejects config that breaks the schema' {
        @(Test-AtlasSchema -Json '{"schemaVersion":1,"graph":{"refreshOnEdit":"yes"}}' -Schema 'atlas-config').Count | Should -BeGreaterThan 0
        @(Test-AtlasSchema -Json '{"schemaVersion":1,"unknown":true}' -Schema 'atlas-config').Count | Should -BeGreaterThan 0
        @(Test-AtlasSchema -Json '{"schemaVersion":1,"work":{"dir":".claude/atlas/work"}}' -Schema 'atlas-config').Count | Should -Be 0
    }
    It 'rejects a policy with a bad level' {
        $bad = (Get-Content -Raw (Join-Path $script:Owner '.claude/config/policy.json') | ConvertFrom-Json -AsHashtable)
        $bad.editGate.blockAt = 'SEVERE'
        @(Test-AtlasSchema -Json ($bad | ConvertTo-Json -Depth 10) -Schema 'policy').Count | Should -BeGreaterThan 0
    }
    It 'turns dependants and flows into a risk level using the policy thresholds' {
        Get-RiskLevel -Affected 0 -Flows 0 | Should -Be 'LOW'
        Get-RiskLevel -Affected 4 -Flows 0 | Should -Be 'MEDIUM'
        Get-RiskLevel -Affected 11 -Flows 0 | Should -Be 'HIGH'
        Get-RiskLevel -Affected 0 -Flows 4 | Should -Be 'CRITICAL'
        Get-RiskLevel -Affected 31 -Flows 0 | Should -Be 'CRITICAL'
        Test-RiskAtLeast 'HIGH' 'MEDIUM' | Should -BeTrue
        Test-RiskAtLeast 'LOW' 'HIGH' | Should -BeFalse
    }
    It 'makes repo-relative paths and refuses paths outside the repo' {
        ConvertTo-RepoPath (Join-Path $script:Work 'src/a.py') | Should -Be 'src/a.py'
        ConvertTo-RepoPath 'src\b.py' | Should -Be 'src/b.py'
        ConvertTo-RepoPath (Join-Path ([IO.Path]::GetTempPath()) 'elsewhere/x.py') | Should -BeNullOrEmpty
    }
    It 'recognises test files' {
        Test-TestPath 'tests/test_a.py' | Should -BeTrue
        Test-TestPath 'src/App.test.tsx' | Should -BeTrue
        Test-TestPath 'src/app.py' | Should -BeFalse
    }
    It 'merges nested settings, local values win' {
        $m = Merge-Hashtable @{ a = @{ x = 1; y = 2 }; b = 1 } @{ a = @{ y = 9 } }
        $m.a.x | Should -Be 1; $m.a.y | Should -Be 9; $m.b | Should -Be 1
    }
}

Describe 'Session ledger' {
    BeforeAll {
        $script:LedgerRoot = Join-Path $script:Work 'ledger'
        [void](New-Item -ItemType Directory -Path $script:LedgerRoot)
        $env:CODEATLAS_ROOT = $script:LedgerRoot
        foreach ($m in 'Core', 'Git', 'Template', 'Session') { Import-Module (Join-Path $script:Owner ".claude/lib/Atlas.$m.psm1") -Force -DisableNameChecking }
        Set-Content -Path (Join-Path $script:LedgerRoot 'a.py') -Value 'def f():\n    return 1\n'
        Reset-AtlasCache
    }
    AfterAll { $env:CODEATLAS_ROOT = $null }

    It 'has no task before one is started' {
        Get-Session | Should -BeNullOrEmpty
    }
    It 'saves a valid session and reads it back' {
        $s = New-Session -Title 'Fix the login bug' -Description 'why' -Acceptance @('tests pass')
        $s.status | Should -Be 'active'
        $s.id | Should -Match '^\d{8}-\d{6}-fix-the-login-bug$'
        $back = Get-Session
        $back.task.title | Should -Be 'Fix the login bug'
        $back.startedAt | Should -Match '^\d{4}-\d\d-\d\d \d\d:\d\d:\d\dZ$'          # stays a string, never turns into a local DateTime
        @($back.task.acceptance) | Should -Contain 'tests pass'
    }
    It 'refuses to save a session that breaks the schema' {
        $s = Get-Session
        $s.status = 'paused'
        { Save-Session $s } | Should -Throw '*does not match schema*'
    }
    It 'counts edits per file and keeps symbol lists consistent' {
        $s = Get-Session
        Register-FileEdit -Session $s -Path 'a.py'
        Register-FileEdit -Session $s -Path 'a.py'
        $s.files['a.py'].edits | Should -Be 2
        Merge-SessionSymbols -Session $s -Modified @('a.py::f') -Removed @('a.py::g')
        Merge-SessionSymbols -Session $s -Added @('a.py::g')                     # g came back: it is no longer removed
        @($s.symbols.removed).Count | Should -Be 0
        @($s.symbols.added) | Should -Contain 'a.py::g'
        Merge-SessionSymbols -Session $s -Removed @('a.py::f')                   # f is now gone: no longer modified
        @($s.symbols.modified).Count | Should -Be 0
        @($s.symbols.removed) | Should -Contain 'a.py::f'
    }
    It 'writes journal events and rejects unknown types' {
        Add-Journal -Type note -Data @{ text = 'chose a token bucket' }
        $tail = @(Get-JournalTail -Count 5)
        $tail[-1].type | Should -Be 'note'
        Format-JournalEvent $tail[-1] | Should -Match 'chose a token bucket'
        { Add-Journal -Type 'made-up' } | Should -Throw
    }
    It 'renders the work context for a task, and a start prompt when there is none' {
        Get-SessionContext | Should -Match 'Active task: \*\*Fix the login bug\*\*'
        Close-Session (Get-Session) -Status abandoned
        Get-Session | Should -BeNullOrEmpty
        Get-SessionContext | Should -Match 'No active task'
        Test-Path (Join-Path $script:LedgerRoot '.claude/atlas/work/sessions') | Should -BeTrue
    }
    It 'remembers confirmations and lets them expire' {
        $rel = Confirm-Gate -Target 'src/core.py::run'
        $rel | Should -Be 'src/core.py'
        Test-GateConfirmed (Get-Gates)['src/core.py'] | Should -BeTrue
        Test-GateConfirmed @{ confirmedAt = '2000-01-01 00:00:00Z' } | Should -BeFalse
        Test-GateConfirmed @{ confirmedAt = '' } | Should -BeFalse
    }
}

Describe 'Install and the whole daily loop' -Skip:(-not $script:HaveGit) {
    BeforeAll {
        $script:Repo = Join-Path $script:Work 'repo'
        Write-File 'app/util.py' "def clamp(x, lo, hi):`n    return max(lo, min(x, hi))`n`ndef unused_helper():`n    return 1`n"
        Write-File 'app/shapes.py' "from app.util import clamp`n`ndef scaled(x):`n    return clamp(x, 0, 10)`n"
        Write-File 'tests/test_util.py' "from app.util import clamp`n`ndef test_clamp():`n    assert clamp(5, 0, 3) == 3`n"
        & git -C $script:Repo init -q 2>&1 | Out-Null
        & git -C $script:Repo symbolic-ref HEAD refs/heads/main | Out-Null      # `init -b` needs git 2.28; this works on older git too
        Invoke-Git @('add', '-A') | Out-Null
        Invoke-Git @('commit', '-q', '-m', 'init') | Out-Null
        # things the installer must not disturb
        Write-File '.claude/settings.json' '{"hooks":{"PostToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"echo mine"}]}]},"permissions":{"allow":["Bash(make:*)"]}}'
        $hooks = Join-Path $script:Repo '.git/hooks'
        Set-Content -Path (Join-Path $hooks 'post-merge') -Value "#!/bin/sh`necho my own hook"
        $script:Install = Invoke-Pwsh -Script (Join-Path $script:Owner '.claude/scripts/install.ps1') -Arguments @('-Target', $script:Repo) -Cwd $script:Owner
    }

    Context 'install' {
        It 'succeeds and reports a healthy setup' {
            $script:Install.ExitCode | Should -Be 0
            $script:Install.StdOut | Should -Not -Match '\[FAIL\]'
            $script:Install.StdOut | Should -Match 'graph built'
        }
        It 'copies every folder, engine included, so the repo carries its own tool' {
            foreach ($f in 'agents/atlas-dev.md', 'engine/atlas_engine/cli.py', 'engine/atlas_engine/web/index.html', 'engine/atlas_engine/web/vendor/sigma.min.js', 'instructions/before-editing.md', 'lib/Atlas.Core.psm1', 'scripts/on-push.ps1', 'schemas/session.schema.json', 'templates/commit-message.md', 'config/policy.json', 'skills/atlas-work/SKILL.md') {
                Join-Path $script:Repo ".claude/$f" | Should -Exist
            }
            Join-Path $script:Repo '.claude/engine/.venv' | Should -Not -Exist
            Join-Path $script:Repo '.claude/tests' | Should -Not -Exist
        }
        It 'merges settings: our hooks are added and yours are kept' {
            $s = Read-Json (Join-Path $script:Repo '.claude/settings.json')
            @($s.hooks.SessionStart).Count | Should -Be 1
            @($s.hooks.PreToolUse).Count | Should -Be 1
            @($s.hooks.PostToolUse).Count | Should -Be 2
            ($s.hooks.PostToolUse | ConvertTo-Json -Depth 10) | Should -Match 'echo mine'
            @($s.permissions.allow) | Should -Contain 'Bash(make:*)'
            @($s.permissions.allow) | Should -Contain 'mcp__codeatlas'
            $s.ContainsKey('agent') | Should -BeFalse
        }
        It 'registers the MCP server for pwsh' {
            $m = Read-Json (Join-Path $script:Repo '.mcp.json')
            $m.mcpServers.codeatlas.command | Should -Be 'pwsh'
            @($m.mcpServers.codeatlas.args) | Should -Contain 'serve'
        }
        It 'installs git hooks but keeps a hook you already had' {
            $hooks = Join-Path $script:Repo '.git/hooks'
            foreach ($n in 'post-commit', 'post-checkout', 'post-rewrite', 'pre-push') { (Get-Content -Raw (Join-Path $hooks $n)) | Should -Match '# codeatlas-hook' }
            (Get-Content -Raw (Join-Path $hooks 'post-merge')) | Should -Match 'my own hook'
            $script:Install.StdOut | Should -Match 'skipped git hook post-merge'
        }
        It 'keeps generated files out of git status' {
            $status = (Invoke-Git @('status', '--porcelain')).Out
            $status | Should -Not -Match 'atlas'                      # .claude/atlas/ (graph, journal, report) and atlas.local.json
        }
        It 'builds the graph and one HTML report inside .claude/atlas, and nothing in the repo root' {
            Join-Path $script:Repo '.claude/atlas/graph/atlas.db' | Should -Exist
            $report = Join-Path $script:Repo '.claude/atlas/report/index.html'
            $report | Should -Exist
            ((Get-Content $report -TotalCount 6) -join "`n") | Should -Match 'atlas-report link='
            @(Get-ChildItem (Join-Path $script:Repo '.claude/atlas') -Recurse -Filter '*.html').Count | Should -Be 1
            @(Get-ChildItem $script:Repo -Force | Where-Object { $_.Name -in 'CodeAtlas', 'scripts', 'atlas_engine' }).Count | Should -Be 0
        }
        It 'is idempotent: a second run adds no duplicate hooks' {
            $r = Invoke-Pwsh -Script (Join-Path $script:Owner '.claude/scripts/install.ps1') -Arguments @('-Target', $script:Repo, '-SkipIndex') -Cwd $script:Owner
            $r.ExitCode | Should -Be 0
            $s = Read-Json (Join-Path $script:Repo '.claude/settings.json')
            @($s.hooks.SessionStart).Count | Should -Be 1
            @($s.hooks.PostToolUse).Count | Should -Be 2
            @((Get-Content (Join-Path $script:Repo '.git/info/exclude')) | Where-Object { $_ -eq '.claude/atlas/' }).Count | Should -Be 1
        }
    }

    Context 'session start' {
        It 'tells Claude there is no task yet' {
            (Invoke-InRepo 'session-start.ps1' -StdIn '{"hook_event_name":"SessionStart"}').StdOut | Should -Match 'No active task'
        }
    }

    Context 'a task from start to finish' {
        It 'starts a task and refuses a second one' {
            $r = Invoke-InRepo 'work.ps1' @('start', '-Title', 'Validate clamp bounds', '-Description', 'raise when lo > hi', '-Accept', 'raises ValueError; has a test')
            $r.ExitCode | Should -Be 0
            $r.StdOut | Should -Match 'Started task'
            (Invoke-InRepo 'work.ps1' @('start', '-Title', 'Other')).ExitCode | Should -Be 1
            $s = Read-Json (Join-Path $script:Repo '.claude/atlas/work/current.json')
            $s.task.title | Should -Be 'Validate clamp bounds'
            @($s.task.acceptance).Count | Should -Be 2
        }
        It 'puts the task into the next session''s context' {
            $out = (Invoke-InRepo 'session-start.ps1' -StdIn '{"hook_event_name":"SessionStart","how_session_started":"compact"}').StdOut
            $out | Should -Match 'Validate clamp bounds'
            $out | Should -Match 'raises ValueError'
        }
        It 'lets a low-risk edit through silently' {
            $r = Invoke-InRepo 'pre-edit.ps1' -StdIn (Get-HookJson 'Edit' 'app/util.py')
            $r.ExitCode | Should -Be 0
            $r.StdErr | Should -BeNullOrEmpty
            (Read-Json (Join-Path $script:Repo '.claude/atlas/work/gates.json'))['app/util.py'].risk | Should -Be 'LOW'
        }
        It 'shows edits and checks in the work context (regression: a gate event without "blocked")' {
            $out = (Invoke-InRepo 'work.ps1' @('status')).StdOut
            $out | Should -Match 'checked app/util.py: LOW'
            $out | Should -Match 'Recent activity'
        }
        It 'ignores tests, new files, other folders and paths outside the repo' {
            foreach ($p in @((Get-HookJson 'Edit' 'tests/test_util.py'), (Get-HookJson 'Write' 'app/brand_new.py'), (Get-HookJson 'Edit' 'docs/readme.md'),
                    (@{ tool_input = @{ file_path = (Join-Path ([IO.Path]::GetTempPath()) 'elsewhere.py') } } | ConvertTo-Json -Compress), '{}', 'not json')) {
                (Invoke-InRepo 'pre-edit.ps1' -StdIn $p).ExitCode | Should -Be 0
            }
        }
        It 'blocks an edit to a critical file until the user confirms it' {
            $policyPath = Join-Path $script:Repo '.claude/config/policy.json'
            $original = Get-Content -Raw $policyPath
            try {
                $p = $original | ConvertFrom-Json -AsHashtable
                $p.riskThresholds = @{ medium = @{ affected = 1; flows = 9 }; high = @{ affected = 1; flows = 9 }; critical = @{ affected = 1; flows = 9 } }
                $p | ConvertTo-Json -Depth 10 | Set-Content $policyPath
                Remove-Item (Join-Path $script:Repo '.claude/atlas/work/gates.json') -ErrorAction SilentlyContinue
                $blocked = Invoke-InRepo 'pre-edit.ps1' -StdIn (Get-HookJson 'Edit' 'app/util.py')
                $blocked.ExitCode | Should -Be 2
                $blocked.StdErr | Should -Match 'edit gate'
                $blocked.StdErr | Should -Match 'work.ps1 confirm -Target app/util.py'
                (Invoke-InRepo 'work.ps1' @('confirm', '-Target', 'app/util.py')).StdOut | Should -Match 'Confirmed'
                $allowed = Invoke-InRepo 'pre-edit.ps1' -StdIn (Get-HookJson 'Edit' 'app/util.py')
                $allowed.ExitCode | Should -Be 0
                $allowed.StdOut | Should -Match 'additionalContext'
            }
            finally { Set-Content $policyPath $original }
        }
        It 'gates the symbol being edited, not the whole file' {
            $policyPath = Join-Path $script:Repo '.claude/config/policy.json'
            $original = Get-Content -Raw $policyPath
            try {
                $p = $original | ConvertFrom-Json -AsHashtable
                $p.riskThresholds = @{ medium = @{ affected = 1; flows = 9 }; high = @{ affected = 1; flows = 9 }; critical = @{ affected = 1; flows = 9 } }
                $p | ConvertTo-Json -Depth 10 | Set-Content $policyPath
                Remove-Item (Join-Path $script:Repo '.claude/atlas/work/gates.json') -ErrorAction SilentlyContinue
                function Edit-Json($old) { @{ tool_name = 'Edit'; tool_input = @{ file_path = (Join-Path $script:Repo 'app/util.py'); old_string = $old; new_string = 'x' } } | ConvertTo-Json -Compress }
                # unused_helper has nothing depending on it: allowed, although clamp in the same file is critical
                $quiet = Invoke-InRepo 'pre-edit.ps1' -StdIn (Edit-Json 'return 1')
                $quiet.ExitCode | Should -Be 0
                $blocked = Invoke-InRepo 'pre-edit.ps1' -StdIn (Edit-Json 'return max(lo, min(x, hi))')
                $blocked.ExitCode | Should -Be 2
                $blocked.StdErr | Should -Match 'clamp'
                $gates = Read-Json (Join-Path $script:Repo '.claude/atlas/work/gates.json')
                $gates['app/util.py::unused_helper'].risk | Should -Be 'LOW'
                $gates['app/util.py::clamp'].risk | Should -Be 'CRITICAL'
                # text that is not in the file falls back to checking the whole file
                (Invoke-InRepo 'pre-edit.ps1' -StdIn (Edit-Json 'no such text')).ExitCode | Should -Be 2
            }
            finally { Set-Content $policyPath $original }
        }
        It 'does not block when the gate is off' {
            $policyPath = Join-Path $script:Repo '.claude/config/policy.json'
            $original = Get-Content -Raw $policyPath
            try {
                $p = $original | ConvertFrom-Json -AsHashtable
                $p.editGate.mode = 'off'
                $p.riskThresholds = @{ medium = @{ affected = 1; flows = 9 }; high = @{ affected = 1; flows = 9 }; critical = @{ affected = 1; flows = 9 } }
                $p | ConvertTo-Json -Depth 10 | Set-Content $policyPath
                Remove-Item (Join-Path $script:Repo '.claude/atlas/work/gates.json') -ErrorAction SilentlyContinue
                (Invoke-InRepo 'pre-edit.ps1' -StdIn (Get-HookJson 'Edit' 'app/util.py')).ExitCode | Should -Be 0
            }
            finally { Set-Content $policyPath $original }
        }
        It 'warns at once when an edit removes a symbol that is still used' {
            Write-File 'app/util.py' "def unused_helper():`n    return 1`n"
            $r = Invoke-InRepo 'on-edit.ps1' -StdIn (Get-HookJson 'Edit' 'app/util.py')
            $r.ExitCode | Should -Be 0
            $ctx = ($r.StdOut | ConvertFrom-Json).hookSpecificOutput
            $ctx.hookEventName | Should -Be 'PostToolUse'
            $ctx.additionalContext | Should -Match 'removed clamp'
            $s = Read-Json (Join-Path $script:Repo '.claude/atlas/work/current.json')
            $s.files['app/util.py'].edits | Should -Be 1
            @($s.symbols.removed) | Should -Contain 'app/util.py::clamp'
            $journal = Get-Content (Join-Path $script:Repo '.claude/atlas/work/journal.jsonl') | ForEach-Object { $_ | ConvertFrom-Json -AsHashtable }
            ($journal | Where-Object { $_.type -eq 'edit' } | Select-Object -Last 1).data.removedStillReferenced | Should -Contain 'clamp'
        }
        It 'blocks the commit check while that break is unresolved' {
            $r = Invoke-InRepo 'precommit.ps1' @('-Subject', 'Validate clamp bounds')
            $r.ExitCode | Should -Be 0
            $r.StdOut | Should -Match 'Pre-commit check: BLOCK'
            $r.StdOut | Should -Match "Removed 'clamp' is still used by scaled"
            (Invoke-InRepo 'precommit.ps1' @('-Strict')).ExitCode | Should -Be 1
        }
        It 'passes once the change is fixed, lists tests, and drafts the commit message' {
            Write-File 'app/util.py' "def clamp(x, lo, hi):`n    if lo > hi:`n        raise ValueError('lo must not exceed hi')`n    return max(lo, min(x, hi))`n`ndef unused_helper():`n    return 1`n"
            (Invoke-InRepo 'on-edit.ps1' -StdIn (Get-HookJson 'Edit' 'app/util.py')).StdOut | Should -Not -Match 'removed'
            $noTests = (Invoke-InRepo 'precommit.ps1').StdOut
            $noTests | Should -Match 'No passing test run is recorded'
            (Invoke-InRepo 'work.ps1' @('test', '-Command', 'python -m pytest -q', '-Result', 'pass')).StdOut | Should -Match 'Recorded'
            $r = (Invoke-InRepo 'precommit.ps1' @('-Subject', 'Validate clamp bounds')).StdOut
            $r | Should -Match 'Pre-commit check: OK'
            $r | Should -Match 'python -m pytest -q'
            $draft = Get-Content -Raw (Join-Path $script:Repo '.claude/atlas/work/commit-draft.txt')
            $draft | Should -Match '^Validate clamp bounds'
            $draft | Should -Match 'app/util.py: modified clamp'
        }
        It 'runs the configured tests, records the result, and fails when a runner fails' {
            $cfgPath = Join-Path $script:Repo '.claude/config/tests.json'
            $original = Get-Content -Raw $cfgPath
            try {
                @{ schemaVersion = 1; runners = @(@{ name = 'echo'; languages = @('python'); detect = @('app'); command = 'Write-Output tests-ran' }) } | ConvertTo-Json -Depth 5 | Set-Content $cfgPath
                $ok = Invoke-InRepo 'run-tests.ps1'
                $ok.ExitCode | Should -Be 0
                $ok.StdOut | Should -Match '== echo: Write-Output tests-ran => pass'
                $ok.StdOut | Should -Match 'tests-ran'
                $s = Read-Json (Join-Path $script:Repo '.claude/atlas/work/current.json')
                ($s.tests | Select-Object -Last 1).result | Should -Be 'pass'
                @{ schemaVersion = 1; runners = @(@{ name = 'broken'; languages = @('python'); detect = @('app'); command = 'exit 3' }) } | ConvertTo-Json -Depth 5 | Set-Content $cfgPath
                $bad = Invoke-InRepo 'run-tests.ps1'
                $bad.ExitCode | Should -Be 1
                $bad.StdOut | Should -Match '=> fail'
                (Read-Json (Join-Path $script:Repo '.claude/atlas/work/current.json')).tests | Select-Object -Last 1 | ForEach-Object { $_.result | Should -Be 'fail' }
                (Invoke-InRepo 'run-tests.ps1' @('-Runner', 'nothing')).StdOut | Should -Match 'No test runner applies'
            }
            finally { Set-Content $cfgPath $original }
            (Invoke-InRepo 'work.ps1' @('test', '-Command', 'python -m pytest -q', '-Result', 'pass')).ExitCode | Should -Be 0     # leave a passing run for the checks below
        }
        It 'finds test files in the repo root and prefers the Maven wrapper over plain Maven' {
            $cfgPath = Join-Path $script:Repo '.claude/config/tests.json'
            $original = Get-Content -Raw $cfgPath
            try {
                Write-File 'test_rootlevel.py' "def test_x():`n    assert True`n"
                Write-File 'mvnw' "#!/bin/sh`n"
                Write-File 'pom.xml' "<project/>`n"
                @{ schemaVersion = 1; runners = @(
                        @{ name = 'py'; languages = @('python'); detect = @('test_*.py'); command = 'Write-Output py-ran' },
                        @{ name = 'wrapper'; languages = @('java'); detect = @('mvnw'); command = 'Write-Output wrapper-ran' },
                        @{ name = 'plain'; languages = @('java'); detect = @('pom.xml'); unless = @('mvnw'); command = 'Write-Output plain-ran' }) } | ConvertTo-Json -Depth 6 | Set-Content $cfgPath
                $out = (Invoke-InRepo 'run-tests.ps1' @('-All')).StdOut
                $out | Should -Match 'py-ran'
                $out | Should -Match 'wrapper-ran'
                $out | Should -Not -Match 'plain-ran'
                @{ schemaVersion = 1; runners = @(@{ name = 'py'; languages = @('python'); detect = @('nothing_like_this_*.py'); command = 'x' }) } | ConvertTo-Json -Depth 6 | Set-Content $cfgPath
                (Invoke-InRepo 'run-tests.ps1' @('-All')).StdOut | Should -Match 'No test runner applies\..*Configured: py'
            }
            finally { Set-Content $cfgPath $original; Remove-Item (Join-Path $script:Repo 'test_rootlevel.py'), (Join-Path $script:Repo 'mvnw'), (Join-Path $script:Repo 'pom.xml') -ErrorAction SilentlyContinue }
        }
        It 'copes with git hooks that pass their own arguments, and with a config from an older version' {
            $r = Invoke-InRepo 'on-commit.ps1' @('post-checkout', '1111111111111111111111111111111111111111', '2222222222222222222222222222222222222222', '1')
            $r.ExitCode | Should -Be 0
            $r.StdErr | Should -BeNullOrEmpty
            $cfgPath = Join-Path $script:Repo '.claude/config/atlas.json'
            $original = Get-Content -Raw $cfgPath
            try {
                '{"schemaVersion":1}' | Set-Content $cfgPath
                $d = (Invoke-InRepo 'doctor.ps1' @('-Json')).StdOut | ConvertFrom-Json
                ($d | Where-Object check -eq 'Config and policy').status | Should -Be 'PASS'
                (Invoke-InRepo 'work.ps1' @('status')).ExitCode | Should -Be 0
            }
            finally { Set-Content $cfgPath $original }
        }
        It 'records a commit made through git (post-commit hook)' {
            (Invoke-Git @('add', 'app/util.py')).Code | Should -Be 0
            $c = Invoke-Git @('commit', '-q', '-F', (Join-Path $script:Repo '.claude/atlas/work/commit-draft.txt'))
            $c.Code | Should -Be 0
            $s = Read-Json (Join-Path $script:Repo '.claude/atlas/work/current.json')
            @($s.commits).Count | Should -Be 1
            $s.commits[0].subject | Should -Be 'Validate clamp bounds'
            (Get-Content (Join-Path $script:Repo '.claude/atlas/work/graph-snapshots.jsonl') | Select-Object -Last 1) | Should -Match 'post-commit'
        }
        It 'updates the graph on push and records it (pre-push hook)' {
            $remote = Join-Path $script:Work 'remote.git'
            & git init -q --bare $remote 2>&1 | Out-Null
            Invoke-Git @('remote', 'add', 'origin', $remote) | Out-Null
            $p = Invoke-Git @('push', '-u', 'origin', 'main')
            $p.Code | Should -Be 0
            $p.Out | Should -Match '\[codeatlas\] push to origin'
            $p.Out | Should -Match 'Graph updated: \d+ symbols'
            (Get-Content (Join-Path $script:Repo '.claude/atlas/work/graph-snapshots.jsonl') | Select-Object -Last 1) | Should -Match 'pre-push'
            (Get-Item (Join-Path $script:Repo '.claude/atlas/report/index.html')).LastWriteTimeUtc | Should -BeGreaterThan ([DateTime]::UtcNow.AddMinutes(-2))    # the report was rebuilt by the push
            $s = Read-Json (Join-Path $script:Repo '.claude/atlas/work/current.json')
            @($s.pushes).Count | Should -Be 1
            $s.pushes[0].remote | Should -Be 'origin'
        }
        It 'blocks a push only when the policy says so' {
            Write-File 'app/more.py' "def extra():`n    return 2`n"
            Invoke-Git @('add', '-A') | Out-Null
            Invoke-Git @('commit', '-q', '-m', 'more') | Out-Null
            $policyPath = Join-Path $script:Repo '.claude/config/policy.json'
            $original = Get-Content -Raw $policyPath
            try {
                $p = $original | ConvertFrom-Json -AsHashtable
                $p.push.mode = 'block'; $p.push.blockAt = 'LOW'
                $p | ConvertTo-Json -Depth 10 | Set-Content $policyPath
                $blocked = Invoke-Git @('push', 'origin', 'main')
                $blocked.Code | Should -Not -Be 0
                $blocked.Out | Should -Match 'push blocked'
            }
            finally { Set-Content $policyPath $original }
            (Invoke-Git @('push', 'origin', 'main')).Code | Should -Be 0
        }
        It 'shows the doctor a healthy setup with an active task' {
            $r = Invoke-InRepo 'doctor.ps1' @('-Json')
            $checks = $r.StdOut | ConvertFrom-Json
            ($checks | Where-Object status -eq 'FAIL') | Should -BeNullOrEmpty
            ($checks | Where-Object check -eq 'Active task').detail | Should -Match 'Validate clamp bounds'
            ($checks | Where-Object check -eq 'Git hooks').status | Should -BeIn 'PASS', 'WARN'
        }
        It 'finishes the task with a summary and a pull request description' {
            $r = Invoke-InRepo 'work.ps1' @('finish')
            $r.ExitCode | Should -Be 0
            $r.StdOut | Should -Match "finished"
            $r.StdOut | Should -Match 'HTML report:\s+\.claude/atlas/report/index.html'
            Join-Path $script:Repo '.claude/atlas/work/current.json' | Should -Not -Exist
            $summary = Get-ChildItem (Join-Path $script:Repo '.claude/atlas/work') -Filter 'summary-*.md'
            $pr = Get-ChildItem (Join-Path $script:Repo '.claude/atlas/work') -Filter 'pr-*.md'
            @($summary).Count | Should -Be 1
            (Get-Content -Raw $pr[0].FullName) | Should -Match 'Validate clamp bounds'
            (Get-Content -Raw $summary[0].FullName) | Should -Match 'Commits'
            Get-ChildItem (Join-Path $script:Repo '.claude/atlas/work/sessions') -Filter '*.json' | Should -Not -BeNullOrEmpty
            (Invoke-InRepo 'work.ps1' @('list')).StdOut | Should -Match 'finished'
        }
    }

    Context 'security lead in the pre-commit check' {
        It 'reports untrusted input reaching a shell call in a changed Python file' {
            Write-File 'app/run.py' "import os`n`ndef go():`n    os.system('echo ' + input())`n"
            $r = (Invoke-InRepo 'precommit.ps1').StdOut
            $r | Should -Match 'Security leads'
            $r | Should -Match 'Command injection \(CWE-78\) at app/run.py:4'
        }
    }

    Context 'the HTML report and localhost' {
        It 'writes one self-contained file and prints where it is' {
            $r = Invoke-InRepo 'atlas.ps1' @('report')
            $r.ExitCode | Should -Be 0
            ($r.StdOut -split "`n")[0].Trim() | Should -Match 'report[\\/]index.html$'
            $html = Get-Content -Raw (Join-Path $script:Repo '.claude/atlas/report/index.html')
            $html | Should -Match '<script id="atlas-data"'
            $html | Should -Not -Match '(src|href)="https?:'
        }
        It 'serves that file on localhost and nothing else' {
            $port = 47000 + (Get-Random -Maximum 900)
            $proc = Start-Process -FilePath $script:Pwsh -ArgumentList @('-NoProfile', '-File', (Join-Path $script:Repo '.claude/scripts/atlas.ps1'), '--no-refresh', 'web', '--port', $port) -WorkingDirectory $script:Repo -PassThru -RedirectStandardOutput (Join-Path $script:Work 'web.out') -RedirectStandardError (Join-Path $script:Work 'web.err')
            try {
                $ok = $false
                foreach ($i in 1..40) { Start-Sleep -Milliseconds 300; try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -TimeoutSec 3; $ok = $true; break } catch { } }
                $ok | Should -BeTrue
                $r.Headers['Content-Type'] | Should -Match 'text/html'
                $r.Content | Should -Match '<script id="atlas-data"'
                { Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/projects" -TimeoutSec 3 -SkipHttpErrorCheck:$false } | Should -Throw
            }
            finally {
                # atlas.ps1 starts Python as a child; killing only pwsh would leave the server running
                if ($IsWindows) { Get-CimInstance Win32_Process -Filter "ParentProcessId=$($proc.Id)" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
                else { & pkill -f "web --port $port" 2>$null }
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }

    Context 'uninstall' {
        It 'removes what install added and leaves your own settings' {
            $r = Invoke-Pwsh -Script (Join-Path $script:Owner '.claude/scripts/install.ps1') -Arguments @('-Target', $script:Repo, '-Uninstall') -Cwd $script:Owner
            $r.ExitCode | Should -Be 0
            $s = Read-Json (Join-Path $script:Repo '.claude/settings.json')
            $s.hooks.ContainsKey('SessionStart') | Should -BeFalse
            ($s.hooks.PostToolUse | ConvertTo-Json -Depth 10) | Should -Match 'echo mine'
            Join-Path $script:Repo '.git/hooks/pre-push' | Should -Not -Exist
            (Get-Content -Raw (Join-Path $script:Repo '.git/hooks/post-merge')) | Should -Match 'my own hook'
            (Read-Json (Join-Path $script:Repo '.mcp.json')).mcpServers.ContainsKey('codeatlas') | Should -BeFalse
        }
    }
}
