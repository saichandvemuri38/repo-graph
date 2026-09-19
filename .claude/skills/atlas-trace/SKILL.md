---
name: atlas-trace
description: Find the shortest call path between two symbols. Use when the user asks "how does A reach B", "how does execution get from X to Y", or "why does this function get called".
argument-hint: "<from-symbol> <to-symbol>"
---

# /atlas-trace — path between two symbols

Arguments: `$ARGUMENTS`. Two symbol names or ids are needed. If the user gave only one and asked "why does this get called", get its callers with `atlas_context` and follow them upward with repeated traces from the flow entry points (`atlas_flows`).

1. Run MCP tool `atlas_trace`, or `sh scripts/codeatlas.sh trace "<from>" "<to>"`.
2. If either end is ambiguous, show the candidates, ask the user, and re-run with full ids.
3. Show the path table and the diagram. Edges of `med` confidence were inferred from types; `low` ones are guesses, so verify those hops in the source.
4. If there is no path, show the nearest reachable symbols and the unresolved calls from the start symbol. The missing link is often a dynamic call (`getattr`, a callback registry, a decorator). Read the start symbol's source to check.

No saved HTML report for traces.
