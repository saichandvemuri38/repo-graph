# CodeAtlas — project rules

This project has **CodeAtlas**: a code knowledge graph built by the Python engine in `atlas_engine/` and exposed through the `codeatlas` MCP server (`atlas_*` tools), the CLI (`sh scripts/codeatlas.sh ...`) and the app in `CodeAtlas/`. The graph updates itself when files change. See `.claude/README.md`.

## Always do

- **Before editing a function, class or method**, call `atlas_impact` (or `/atlas-impact <symbol>`) and tell the user the risk level. If it is HIGH or CRITICAL, warn and wait for confirmation before editing.
- **Before committing or finishing a task that changed code**, call `atlas_changes` (or `/atlas-changes`). Pay attention to *removed symbols that are still referenced*.
- **For debugging**, start with `atlas_locate_error` if you have an error or stack trace, or `atlas_search` if you have only a symptom, then read the source. Use `/atlas-debug` for a full report.
- **To explore unfamiliar code**, use `atlas_search`, `atlas_context`, `atlas_flows` and `atlas_clusters` before grepping.
- If an MCP tool is unavailable, use the CLI: `sh scripts/codeatlas.sh <command>`.
- To let the user browse visually, run `/atlas-explore` (the interactive explorer at http://localhost:4848).

## Trust rules

- The graph is an **index, not the truth**. Confirm important conclusions in the source.
- Edges with confidence `med` were inferred from types or a unique name. `low` ones are guesses. Both are *possible*, not proven.
- Calls listed under "could not resolve" (`CodeAtlas/graph/unresolved.md`) may hide real dependants.
- Never claim "nothing calls this" from the graph alone. Check for dynamic use (`getattr`, decorators that register handlers, string dispatch, tests).
- A file listed with a parse error has recovered symbols but no call edges until its syntax is fixed.

## Never do

- Never edit files under `CodeAtlas/graph/`. They are generated on every index.
- Never edit source from an analysis skill (`atlas-impact`, `atlas-trace`, `atlas-debug`, `atlas-changes`). Those propose only.
- Never rename a symbol with find-and-replace. Run `atlas_impact`, update every dependant it lists including `REFERENCES`, then re-run `atlas_changes`.
- Never commit generated files (`atlas.db`, shards, generated HTML). `.gitignore` already excludes them.

## Where things are

| Path | What |
|---|---|
| `atlas_engine/` | The engine (Python). Change it only when asked, and run `python -m pytest` after |
| `.claude/agents/` | Judgment agents (`bug-tracer`, `impact-analyst`, `symbol-analyst`, `change-detector`) and fallbacks |
| `.claude/skills/` | The `/atlas-*` commands |
| `.claude/prompts/` | Shared rules and formats |
| `atlas_engine/web/` | The explorer front-end (plain JS, no build step; sigma + graphology vendored under `web/vendor/`) |
| `scripts/` | Launcher, reindex, git-hook installer |
| `CodeAtlas/` | The app pages, saved reports and generated graph data |
