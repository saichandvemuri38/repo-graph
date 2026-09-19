---
name: atlas-guide
description: Explain how CodeAtlas works and which command to use. Use when the user asks "what can this do", "how do I use CodeAtlas", or seems unsure which atlas command to run.
---

# /atlas-guide — how CodeAtlas works

Read `.claude/README.md` and answer from it. Keep the reply short:

1. One paragraph: CodeAtlas is a Python engine (`atlas_engine/`) that parses the repo exactly into a graph of symbols and their calls, imports and inheritance, plus Claude agents that reason over it. It is exposed as an MCP server, a CLI and a static HTML app in `CodeAtlas/`. It updates itself on edits (Claude Code hook), on commits and pushes (git hooks) and in CI.
2. The command table:

| You want to… | Run |
|---|---|
| Build or refresh the graph | `/atlas-index` |
| Know everything about a symbol | `/atlas-context <symbol>` |
| Know what breaks if you change something | `/atlas-impact <symbol>` |
| See how A reaches B | `/atlas-trace <from> <to>` |
| Debug an error or a wrong result | `/atlas-debug <text>` |
| Check your uncommitted work | `/atlas-changes` |
| Rebuild the HTML pages only | `/atlas-publish` |
| Browse the graph visually (search, layouts, code, flows, AI chat) | `/atlas-explore` |

3. Current state: run `sh scripts/codeatlas.sh status` and summarize the counts. If the graph is empty, say "not indexed yet, start with /atlas-index".
4. Setup checks: is `.venv` present, is the MCP server registered (`.mcp.json`), are git hooks installed (`.git/hooks/post-commit` contains `codeatlas-hook`)? Tell the user what is missing and the one command that fixes it.
5. The honest limits, in two lines (see the README's "Limits").
