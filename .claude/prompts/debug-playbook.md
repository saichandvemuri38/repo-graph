# Debug Playbook

Used by the atlas-dev agent (see `.claude/instructions/debugging.md`). The graph tells you where to look. The source tells you what is true. Always confirm with the source.

## Inputs you may receive

- An error message or stack trace
- A symptom ("output is wrong when the list has duplicates")
- A suspect symbol
- A failing test name

## Procedure

1. **Freshness check.** Compare the manifest hash of any file you rely on with its current hash (`shasum -a 256`). If it differs, say the graph is stale for that file and read the source directly for it.
2. **Locate the entry into the code.**
   - Stack trace → each frame gives file and line. Map each to a symbol by finding the `SYM` whose line range contains it.
   - Error text → Grep the source for the literal string, then map the hit to its symbol.
   - Symptom only → search `SYM` summaries and signatures for the key nouns, then rank candidates by how many of the keywords they match.
3. **Build the call path.**
   - Find the process(es) containing the suspect symbol (`processes.md`).
   - Walk *upstream* to the entry point (who reaches this?) and *downstream* (what does this call?) until the data path is clear.
   - Print the path as `A → B → C`, with `file:line` for each step.
4. **Read the actual source** of the two or three most suspicious symbols on the path. Not the whole repo.
5. **Form hypotheses.** List at most 3, ranked. Each hypothesis needs:
   - the exact `file:line` in question
   - the evidence, quoted from the code
   - what would prove or disprove it (a specific input, a print, or a test)
6. **Check blast radius** of the likely fix using `risk-rubric.md` (depth 1 only is fine) so the user knows what else the fix touches.
7. **Propose the fix, but do not apply it** unless the caller asked for it.

## Rules

- Do not present a hypothesis as a finding. Label it hypothesis until confirmed by code you actually read.
- If the graph has `low` confidence edges or unresolved calls on the path, say so. The real path may go through an edge the graph missed.
- Never claim a function is unused only because the graph has no callers. Check for dynamic use (`getattr`, decorators, string dispatch, tests) first.
- Keep the report under 60 lines.

## Report shape

```
# Debug: <one-line problem statement>
Graph freshness: <current | stale: ...>

## Where it enters the code   — file:line → symbol
## Execution path             — A → B → C (file:line each)
## Hypotheses (ranked)        — evidence, and how to confirm
## Suggested fix              — small diff description, not applied
## Blast radius of the fix    — direct dependants
## Gaps                       — unresolved / low-confidence edges on the path
## Diagram                    — mermaid flowchart of the path, max 15 nodes
```
