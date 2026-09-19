# Understanding code

Start with the graph, then read source. It is faster than grepping and it shows relationships grep cannot.

- **"Where is X handled?"** `atlas_search "<words>"`. Graph search returns keyword matches and the code connected to them (callers, callees), each with the reason it was listed.
- **"What is this symbol?"** `atlas_context <symbol>`: definition, callers, callees, class relations, cluster, flows, and any possible hidden use.
- **"How does A reach B?"** `atlas_trace A B`.
- **"What are the main parts?"** `atlas_clusters` and `atlas_flows`.
- **"Is this used?"** `atlas_unused` sorts uncalled symbols into likely dead, leads to check, and reached implicitly (entry points, decorators, overrides). Never say "unused" from an empty caller list alone.
- **"Is this safe?"** For Python: `atlas_taint <file>` follows untrusted input to dangerous calls; `atlas_defs` shows where a value comes from.
- **Show the user:** `pwsh -NoProfile -File .claude/scripts/atlas.ps1 web --open` opens the graph as an HTML report (search, layouts, flows, checks). If localhost is a problem, `atlas.ps1 report --open` writes the same single file and opens it from disk.

## Reading the results

- Confidence: `high` is proven by scope or import; `med` is inferred from types or a unique name; `low` is a guess. Say which when it matters.
- Tests are listed separately from real dependants.
- If a result is surprising, open the source to confirm before you build on it.
