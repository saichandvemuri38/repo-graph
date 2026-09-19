<!-- codeatlas -->
## CodeAtlas (code knowledge graph)

This repo has a code graph and a daily work agent. In Copilot Chat choose the **atlas-dev-copilot** agent; in Claude Code use **atlas-dev**. The graph is served by the `codeatlas` MCP server (`.vscode/mcp.json`) and PowerShell scripts in `.claude/scripts/`.

- Check what depends on code before changing it (`atlas_impact`); warn on HIGH or CRITICAL and wait for the user.
- Run `pwsh -NoProfile -File .claude/scripts/precommit.ps1` before committing. Commit and push only when asked.
- The graph is an index, not the truth: confirm important conclusions in the source. Never edit `.claude/atlas/` (generated).
- See the graph: `pwsh -NoProfile -File .claude/scripts/atlas.ps1 web --open`.
<!-- /codeatlas -->
