---
name: atlas-impact
description: Blast-radius analysis before changing code. Use when the user asks "what breaks if I change X", "is it safe to edit X", or before editing any function, class or method. 
argument-hint: "<symbol|file> [--direction downstream]"
---

# /atlas-impact — what breaks if this changes?

Arguments: `$ARGUMENTS`. The first word is the target (a symbol, `Class.method`, a full id, or a file path). If it is missing, ask.

1. Run `pwsh -NoProfile -File .claude/scripts/atlas.ps1 impact $ARGUMENTS` (or the MCP tool `atlas_impact`). The engine refreshes the graph, walks dependants to depth 3, scores risk with the rubric in `.claude/prompts/risk-rubric.md`, and prints the report.
2. If the result lists candidates (ambiguous target), show them, ask the user, and re-run.
3. **Warn first.** If the risk is HIGH or CRITICAL, open your reply with a clear warning and the reason. Do not go on to edit code in the same turn without the user's confirmation.
4. Add judgment on top of the numbers. Read the target's source and the direct dependants' call sites, then say what kind of change would break them (signature, return value, side effects) and which checks matter most.
5. Reply with: the risk level, the top direct dependants, flows touched, your suggested checks.

Read-only. Never change source files in this skill.
