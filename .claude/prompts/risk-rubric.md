# Impact Risk Rubric

Used by the atlas-dev agent and the engine.

## Walking the graph (upstream = "who depends on this?")

Start from the target symbol. Follow edges backwards (find edges whose destination is the current symbol):

- Depth 1: `CALLS`, `EXTENDS`, `REFERENCES` into the target. Label **WILL BREAK** if the signature or behavior changes.
- Depth 2: callers of depth-1 symbols. Label **LIKELY AFFECTED**.
- Depth 3: callers of depth-2 symbols. Label **MAY NEED TESTING**.
- Stop at depth 3, or when a level has more than 40 symbols. Say that the list was truncated and give the count.
- Track visited IDs. Never list a symbol twice. Report cycles once.
- Include `IMPORTS` edges only at depth 1 and only to report the *files* that import the target's file.

If asked for downstream ("what does this depend on?"), follow `CALLS` and `REFERENCES` forward instead.

## Edge confidence

- Carry the lowest confidence found along a path to the symbol at the end of the path.
- List `low` paths in a separate "Possible (low confidence)" section. Do not count them toward the risk score unless the target is a public entry point.

## Processes

After collecting affected symbols, read `.claude/atlas/graph/processes.md`. List every process that has at least one affected symbol as a step. If that file is missing or stale, say so and continue without it.

## Risk level

Count distinct affected symbols at depth ≤ 3 (excluding low-confidence-only paths) and the processes touched.

| Level | Rule |
|---|---|
| LOW | ≤ 3 affected symbols and 0 processes |
| MEDIUM | 4–10 affected symbols, or exactly 1 process |
| HIGH | 11–30 affected symbols, or 2–3 processes |
| CRITICAL | > 30 affected symbols, or ≥ 4 processes, or the target is a process entry point |

When two rules give different levels, use the higher one.

## Report shape (Markdown, returned to the caller)

```
# Impact: <symbol-id>
Risk: <LOW|MEDIUM|HIGH|CRITICAL> — <one-sentence reason>
Index freshness: <current | stale: file X changed since indexing>

## Direct (WILL BREAK)      — table: symbol | file:line | edge | confidence
## Likely affected (d2)     — same table
## May need testing (d3)    — same table
## Possible (low confidence)
## Files that import this file
## Processes touched
## Suggested checks         — 3–6 concrete things to test or re-read
## Diagram                  — a mermaid flowchart, max 25 nodes, target highlighted
```
