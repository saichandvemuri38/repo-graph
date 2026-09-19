---
name: atlas-context
description: Show everything about one symbol - definition, callers, callees, class relations, cluster and flows. Use when the user asks "what is X", "who calls X", "what does X call", or "explain this function".
argument-hint: "<symbol name or id>"
---

# /atlas-context — 360° view of a symbol

Target: `$ARGUMENTS`. If it is empty, ask the user which symbol.

1. Get the facts from the engine: MCP tool `atlas_context`, or `pwsh -NoProfile -File .claude/scripts/atlas.ps1 context "<symbol>"`. It refreshes the graph first, so it is never stale.
2. If the answer is a candidate list because the name is ambiguous, show the candidates and ask which one. Then re-run with the full id (`path.py::Class.method`).
3. Present the report: definition, callers, callees, flows, and the diagram as a mermaid block.
4. Add judgment the engine cannot: in two or three sentences, say what the symbol does and how it is used, based on the source shown in the report. Flag anything `low` confidence or listed under "could not resolve" as possible rather than proven.

No saved HTML report for context lookups.
