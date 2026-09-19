---
name: atlas-unused
description: Find code nothing calls, sorted by whether anything hints at hidden use. Use when the user asks "what is dead code", "can I delete X", or "is X used anywhere".
argument-hint: "[symbol]"
---

# /atlas-unused — is this code really unused?

Arguments: `$ARGUMENTS` (optional symbol).

1. With no argument, run `pwsh -NoProfile -File .claude/scripts/atlas.ps1 unused` (or the MCP tool `atlas_unused`). With a symbol, run `pwsh -NoProfile -File .claude/scripts/atlas.ps1 context $ARGUMENTS` and read the section "Possible use the graph cannot see".
2. Report the three groups separately:
   - **No caller and no sign of hidden use**: likely dead. Search the text for each name (docs, configs, other languages, scripts) before saying it can go.
   - **No caller, but check these**: a string, a run-time lookup or an unresolved call may reach it. Open the place the evidence points to and decide.
   - **Reached without a visible call**: not dead code. Do not list these as removable.
3. For anything you would recommend deleting, run `/atlas-impact` first and confirm with a text search.

Read-only. Never delete code in this skill.
