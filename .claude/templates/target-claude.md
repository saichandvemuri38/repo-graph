<!-- codeatlas -->
## CodeAtlas (code knowledge graph)

This repo has a code graph, kept current by hooks, and a daily work agent that uses it.

- Start work with the agent: `claude --agent atlas-dev` (or run `/atlas-work start "<task>"`). It tracks the task, checks impact before editing, and keeps the graph fresh on every edit, commit and push.
- The graph is an index, not the truth. Confirm important conclusions in the source. `low` and `med` confidence edges are guesses and inferences.
- Before editing a symbol run `atlas_impact`; the edit gate (`.claude/config/policy.json`) stops edits to critical files until the user agrees.
- Before committing run `pwsh -NoProfile -File .claude/scripts/precommit.ps1`. Commit and push only when the user asks.
- Never edit `.claude/atlas/`. It is generated.
- See the graph: `pwsh -NoProfile -File .claude/scripts/atlas.ps1 web --open` (one HTML file, `.claude/atlas/report/index.html`; it opens without a server too).
- Health check: `pwsh -NoProfile -File .claude/scripts/doctor.ps1`.
<!-- /codeatlas -->
