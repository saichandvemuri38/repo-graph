# CodeAtlas: project rules

This repo has **CodeAtlas**: a code knowledge graph built by the Python engine in `.claude/engine/`, exposed through the `codeatlas` MCP server (`atlas_*` tools), PowerShell scripts in `.claude/scripts/`, and an HTML report. Everything lives in `.claude/`; see `.claude/README.md`. Work with the daily agent: `claude --agent atlas-dev`.

## Always do

- **Track your work.** Start a task with `pwsh -NoProfile -File .claude/scripts/work.ps1 start -Title "..."`, run `precommit.ps1` before committing, finish with `work.ps1 finish`. Commit and push only when the user asks. Hooks keep the graph current on every edit, commit and push.
- **Before editing a function, class or method**, call `atlas_impact` and tell the user the risk. If it is HIGH or CRITICAL, warn and wait for confirmation. The edit gate blocks critical files until `work.ps1 confirm`.
- **Before committing or finishing a task that changed code**, run `precommit.ps1` (or `atlas_changes`). Pay attention to *removed symbols that are still referenced*.
- **To find code by concept**, `atlas_search` uses the graph. **Before deleting code**, run `atlas_unused`. **When touching input handling, subprocess, SQL, file paths or deserialization in Python**, run `atlas_taint <file>`.
- **For debugging**, start with `atlas_locate_error` if you have an error or stack trace, or `atlas_search` for a symptom, then read the source (`.claude/instructions/debugging.md`).
- If an MCP tool is unavailable, use the CLI: `pwsh -NoProfile -File .claude/scripts/atlas.ps1 <command>`.
- To let the user see the graph, run `/atlas-report` (one HTML file, `.claude/atlas/report/index.html`; it can also be served on localhost).

## Trust rules

- The graph is an **index, not the truth**. Confirm important conclusions in the source.
- Edges with confidence `med` were inferred from types or a unique name. `low` ones are guesses.
- Calls listed as unresolved may hide real dependants.
- Never claim "nothing calls this" from the graph alone. Run `atlas_unused`, then check the leads it lists (`getattr`, decorators that register handlers, string dispatch, overrides, tests).
- `atlas_taint` results are leads: read the path in the source before calling something a vulnerability. Confidence `med` means a library call was assumed to pass the value through.
- A file listed with a parse error has recovered symbols but no call edges until its syntax is fixed.

## Never do

- Never edit files under `.claude/atlas/`. They are generated.
- Never rename a symbol with find-and-replace. Run `atlas_impact`, update every dependant it lists including `REFERENCES`, then re-run `atlas_changes`.
- Never commit generated files (`.claude/atlas/`, `.claude/config/atlas.local.json`, `.claude/engine/.venv`). `.gitignore` already excludes them.
- Do not change the engine (`.claude/engine/`) unless asked; run `python -m pytest ../tests` from `.claude/engine` after (the suite also runs the PowerShell tests when `pwsh` and Pester are installed).

## Where things are

| Path | What |
|---|---|
| `.claude/agents/atlas-dev.md` | the daily work agent |
| `.claude/engine/` | the engine (Python package `atlas_engine`, its HTML front end in `atlas_engine/web/`) |
| `.claude/scripts/`, `lib/` | PowerShell automation and its modules |
| `.claude/instructions/`, `prompts/`, `skills/`, `templates/` | how each step is done; shared formats; the `/atlas-*` commands; text the scripts fill in |
| `.claude/config/`, `schemas/` | policy and settings; the shape of the state files |
| `.claude/atlas/` | generated: graph, task ledger, HTML report |
