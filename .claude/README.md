# CodeAtlas: a code knowledge graph and a daily work agent, all inside `.claude/`

CodeAtlas reads a codebase into a **graph** (functions, classes, methods; the calls, imports, inheritance and references between them; clusters and execution flows). **atlas-dev** is a Claude Code agent that uses the graph for your daily work: it tracks the task, checks what depends on the code before editing, and keeps the graph current on every edit, commit and push. You look at the graph as **one HTML file**.

Everything is in the `.claude/` folder. Nothing is added to the rest of the repository except `.mcp.json` (Claude Code reads it from the repo root) and, if you want CI, `.github/workflows/codeatlas.yml`.

## Quick start

```powershell
# in a repo that has this .claude folder (or install into another one, see below)
pwsh -NoProfile -File .claude/scripts/doctor.ps1          # is everything in place?
pwsh -NoProfile -File .claude/scripts/atlas.ps1 web --open # the graph, in your browser
claude --agent atlas-dev                                   # start working with the daily agent
```

Requirements: PowerShell 7 (`pwsh`), Python 3.10+, git, Claude Code. Optional Python packages (MCP server, clusters, JS/TS/Java/Go parsing): `pwsh -NoProfile -File .claude/scripts/setup-engine.ps1`.

## What is in `.claude/`

| Folder | What it holds |
|---|---|
| `agents/` | `atlas-dev`, the daily work agent |
| `engine/` | the code-graph engine (Python, standard library at its core): parsers, resolver, SQLite store, clusters and flows, queries, MCP server, taint analysis, and the HTML report builder with its front end (`atlas_engine/web/`) |
| `instructions/` | how to do each step: daily loop, memory, before editing, before committing, pushing, debugging, exploring, safety |
| `prompts/` | shared formats and rules: graph schema, extraction and resolution rules, risk rubric, debug playbook, clustering |
| `skills/` | the `/atlas-*` commands: work, commit, doctor, index, report, context, impact, trace, debug, changes, unused, security, guide |
| `scripts/` | PowerShell 7: `atlas`, `work`, `precommit`, `session-start`, `pre-edit`, `on-edit`, `on-commit`, `on-push`, `install`, `setup-engine`, `doctor` |
| `lib/` | PowerShell modules: `Atlas.Core` (paths, config, engine, hooks, risk), `Atlas.Git`, `Atlas.Session` (task ledger, journal, gates), `Atlas.Template` |
| `config/` | `atlas.json` (settings), `policy.json` (risk thresholds, edit gate, push policy), `tests.json` (how to run tests), `atlas.local.json` (this machine's Python, not committed) |
| `schemas/` | JSON Schemas for the config, the policy, the session file and journal events; scripts validate before writing |
| `templates/` | text the scripts fill in (session context, commit message, reports, pull request) and the fragments the installer merges |
| `tests/` | pytest suite for the engine and a Pester suite for the PowerShell (development only; not copied to other repos) |
| `atlas/` | **generated**, git-ignored: `graph/` (database, Markdown shards), `work/` (task ledger, journal), `report/index.html` (the HTML report) |

## The daily work agent

`claude --agent atlas-dev` runs the agent as the main session; its prompt replaces Claude Code's default one. You describe a change; it follows one loop: **start a task, understand with the graph, plan, check impact, edit in small steps, verify, run the pre-commit check, commit and push when you ask, finish.**

| Moment | The agent | The scripts (automatic) |
|---|---|---|
| Session starts | reads the work context, says what is in progress | `session-start.ps1` refreshes the graph and injects the active task, files touched, pending steps and the recent journal |
| Before an edit | runs `atlas_impact`, tells you the risk | `pre-edit.ps1` (edit gate) blocks edits to critical files until you agree |
| After an edit | reads hook feedback | `on-edit.ps1` refreshes the graph, records the edit, warns at once if a removed symbol is still used |
| Before a commit | runs `precommit.ps1`, fixes BLOCKs | reports removed-but-used symbols, risk, tests to run, Python security leads, and drafts the commit message |
| Commit, merge, checkout, rebase | (you ask it to commit) | git hooks run `on-commit.ps1`: refresh, snapshot, record the commit in the task |
| Push | (you ask it to push) | the `pre-push` hook runs `on-push.ps1`: refresh the graph, rebuild the report, snapshot, risk of what is pushed; blocks only if the policy says so |
| Task done | runs `work.ps1 finish` | writes `summary-<id>.md` and `pr-<id>.md`, rebuilds the report, archives the task |

Only the agent uses agent prompts; the bookkeeping is done by scripts, so it cannot be forgotten. The task ledger in `atlas/work/` lets a new chat, a compaction or a restart resume where you stopped.

Policy (`config/policy.json`): `editGate.mode` `off`/`warn`/`block` (default block at CRITICAL, warn at HIGH); `commit.blockOnRemovedStillReferenced`; `push.mode` `warn`/`block` at `push.blockAt`; `riskThresholds` decide LOW/MEDIUM/HIGH/CRITICAL from dependants and flows.

## The HTML report (the graph view)

`pwsh -NoProfile -File .claude/scripts/atlas.ps1 report --open` writes **one self-contained file**, `.claude/atlas/report/index.html`: the graph, every node's details, the source, flows, clusters, checks and current changes are inside it (gzip-compressed), with the interactive front end. It opens by double-click: no server, no network, nothing to install.

`atlas.ps1 web --open` does the same and also serves that one file on `http://localhost:4848/` (127.0.0.1 only, the file and nothing else). If localhost gives you trouble, use the file directly (`web --no-server`, or just open it).

In the report: force / tree / radial layouts, filters and depth focus, search with `type:` and `path:` filters and a **⇢ Related** toggle (graph search), the explorer tree, code inspector, node details with impact, "depends on" and trace, flows with diagrams, clusters, **Checks** (Python security data flow, code nothing calls), **Changes**, dark mode. Search, impact and trace run in the browser over the embedded edges.

It is a snapshot. Pushes and `work.ps1 finish` rebuild it; `web` rebuilds it when the graph moved on. Size: about 400 KB for a 24-file repo, about 15 MB for 3,500 files and 20,000 symbols (built in 5 s; opens in 1.5 s). Needs a current browser (Chrome/Edge 80+, Firefox 113+, Safari 16.4+).

## Using it from VS Code (no Claude CLI needed)

**Claude Code extension.** It uses the same settings as the CLI. Install with `-DefaultAgent` (or add `"agent": "atlas-dev"` to `.claude/settings.json`), open the folder in VS Code and start a Claude Code session: it starts as atlas-dev, with hooks, the edit gate and task tracking. Accept the trust prompt once so the project's permission list applies.

**GitHub Copilot Chat.** Install with `-Copilot`:

```powershell
pwsh -NoProfile -File .claude/scripts/install.ps1 -Target C:\work\my-repo -Update -Copilot
```

The Copilot agent is written to `.github/agents/atlas-dev.agent.md` (pick **atlas-dev** in the Chat agent picker; if you keep it in `.claude/agents/copilot-agent.md` instead, the install uses that, since VS Code reads custom agents from there too). The install also writes `.github/copilot-instructions.md` (a short CodeAtlas block appended to yours) and `.vscode/mcp.json` (the `codeatlas` MCP server; VS Code asks you to trust it once, and you may need to enable its tools in the tools picker). In Copilot the agent does the bookkeeping itself (`work.ps1`, `precommit.ps1`, `run-tests.ps1`) by running terminal commands; the git hooks still refresh the graph and rebuild the report on every commit and push. To avoid a prompt for each script, allow terminal commands that start with `pwsh -NoProfile -File .claude/scripts/` in Copilot's terminal auto-approve setting (`chat.tools.terminal.autoApprove`). VS Code's hooks support is preview and ignores matchers, so the automatic edit gate and edit tracking are only guaranteed in Claude Code; the scripts accept both field spellings, but that path is untested.

## Commands and tools

| You want to… | Claude Code | Terminal (`pwsh -NoProfile -File .claude/scripts/…`) |
|---|---|---|
| Start, check or finish a tracked task | `/atlas-work` | `work.ps1 start\|status\|note\|confirm\|test\|finish` |
| Check before committing | `/atlas-commit` | `precommit.ps1` |
| Check the setup | `/atlas-doctor` | `doctor.ps1` |
| Build or refresh the graph | `/atlas-index` | `atlas.ps1 index [--full]` |
| See the graph | `/atlas-report` | `atlas.ps1 web --open` or `atlas.ps1 report --open` |
| Everything about a symbol | `/atlas-context X` | `atlas.ps1 context X` |
| What breaks if I change X | `/atlas-impact X` | `atlas.ps1 impact X` |
| How does A reach B | `/atlas-trace A B` | `atlas.ps1 trace A B` |
| Debug an error | `/atlas-debug <text>` | `atlas.ps1 locate '<text>'` |
| What did I change | `/atlas-changes` | `atlas.ps1 changes [--base origin/main]` |
| Is this code really unused | `/atlas-unused` | `atlas.ps1 unused` |
| Untrusted input reaching a dangerous call (Python) | `/atlas-security [file]` | `atlas.ps1 taint [file]` |
| Where does this value come from (Python) | `atlas_defs` | `atlas.ps1 defs <function> <variable>` |
| Find code by concept | `atlas_search` | `atlas.ps1 search --graph "how are files detected"` |

Add `--json` to `status`, `search`, `context`, `impact`, `trace`, `changes`, `check`, `locate`, `taint`, `defs` or `unused` for machine-readable output. MCP tools (server `codeatlas`, started from `.mcp.json`): `atlas_status`, `atlas_index`, `atlas_search`, `atlas_context`, `atlas_impact`, `atlas_trace`, `atlas_locate_error`, `atlas_changes`, `atlas_flows`, `atlas_clusters`, `atlas_unused`, `atlas_taint`, `atlas_defs`.

## What the engine understands

- **Python** (exact `ast`): definitions with line ranges, calls, imports (absolute, relative, aliased, star), inheritance and method resolution, decorators, callbacks passed as values, `self.method()`, `super()`, receiver types from constructors, annotations and return types. A file with a syntax error still yields its symbols (no call edges).
- **JavaScript, TypeScript, TSX, Java, Go** (tree-sitter): the same with each language's import rules, monorepo workspaces and tsconfig aliases, `forwardRef`/`memo` components.
- **Confidence:** `high` proven by scope, import or hierarchy; `med` inferred from types or a unique name; `low` guessed by proximity. Unresolved calls are listed, never dropped.
- **Graph search:** keyword matches first, then a personalised PageRank over calls, references and inheritance, so it finds `hash_password` for "authentication" because `login` calls it. No embeddings.
- **Hidden use (Python):** decorators, overrides, properties, framework naming conventions, `getattr` with a literal or a fixed prefix, names used as strings and run-time lookups are recorded, so "nothing calls this" is checked, not assumed.
- **Taint (Python):** follows values from sources (`input()`, `sys.argv`, request data, HTTP handler parameters, environment) through assignments, branches, loops and function calls to sinks (`os.system`, `eval`, `execute`, `open`, `pickle.loads`, ...), with each step and a fix.

## Staying up to date

| When | What updates | Mechanism |
|---|---|---|
| Claude edits a file | graph; the edit is recorded | `PostToolUse` hook, `on-edit.ps1` |
| Before Claude edits a file | the edit gate checks its impact | `PreToolUse` hook, `pre-edit.ps1` |
| A session starts, resumes or is compacted | graph; the work context is injected | `SessionStart` hook |
| A question through MCP | graph, before answering | the MCP server re-indexes changed files first |
| `git commit`, `merge`, `checkout`, `rebase` | graph, snapshot | git hooks running `on-commit.ps1` |
| `git push` | graph, report, snapshot, risk line | `pre-push` hook running `on-push.ps1` |
| Every push and pull request on GitHub | full rebuild, tests, impact summary, the report as an artifact | `.github/workflows/codeatlas.yml` |

Only changed files are re-parsed (size and time, then SHA-256).

## Installing into another repo

From a repo that has this `.claude` folder:

```powershell
pwsh -NoProfile -File .claude/scripts/install.ps1 -Target C:\work\my-repo
```

It copies the folders (engine included), merges `.claude/settings.json`, `.mcp.json` and `CLAUDE.md`, excludes generated files from git, installs the git hooks, and builds the first graph and report. It never overwrites a file you have unless you pass `-Update` (refresh the managed folders and engine) or `-Force` (also reset `config/`), merges your settings, and never replaces a git hook you already had. Other options: `-NoHooks`, `-NoMcp`, `-DefaultAgent` (plain `claude`, and the VS Code Claude extension, start atlas-dev), `-Copilot` (VS Code Copilot Chat files), `-SetupPython` (create `.claude/engine/.venv`), `-SkipIndex`, `-Uninstall`. The repo then carries its own copy of the tool; teammates get it with a normal `git pull`, and each runs `setup-engine.ps1` once if they want the optional packages.

## Limits

- **Static analysis is approximate.** Dynamic code is only partly visible (see hidden use above); JS/TS/Java/Go record none of it, so their uncalled symbols are reported as "not judged". Read important results in the source.
- **Taint is Python only,** not path-sensitive (`if x.isdigit():` does not clean `x`), ignores globals and closures, and treats unknown library calls as passing values through (marked medium confidence). Findings are leads.
- **The report is a snapshot** and holds the source of your files; treat the file like the code. Graphs over 20,000 nodes are cut to the most connected; embedded source is capped at 30 MB before compression.
- **Clusters and flows are heuristics.** Use them to orient, not to prove.
- **Windows:** the PowerShell is written to be cross-platform but has been tested on macOS only.
