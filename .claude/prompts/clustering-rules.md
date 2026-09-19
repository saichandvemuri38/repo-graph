# Clustering and Process Rules

The engine derives two things from the graph. It has no algorithm library, so it works by reasoning over the graph text. Say plainly in the output that this is an approximation.

## Clusters (functional areas)

Goal: group symbols that work together, so a human can see the code's areas at a glance.

1. **Seed by location.** Start with one candidate cluster per directory (or per file when the repo is flat).
2. **Adjust by coupling.** Count `CALLS`, `EXTENDS` and `REFERENCES` edges between candidates.
   - Merge two candidates when more than about 60% of one side's outgoing edges go to the other.
   - Split a candidate when it holds two groups with almost no edges between them.
3. **Size limits.** Aim for 3–30 symbols per cluster. Fold anything smaller into its closest neighbour. Split anything larger.
4. **Exactly one cluster per symbol.** Skip `<module>` pseudo-symbols that have no edges.
5. **Label by responsibility**, not by folder name: "Sorting algorithms", "Request parsing". Use 2–4 words.
6. **Cohesion** (`low`, `med`, `high`): the share of the cluster's edges that stay inside it. Above 0.7 is high, 0.4–0.7 is med, below 0.4 is low.
7. **Summary**: one sentence, at most 140 characters.

Output the records in `clusters.md` using the schema. Then add a `# cross-cluster` comment section listing the 10 heaviest connections between clusters as `# <clusterA> -> <clusterB> : <edge count>`.

## Processes (execution flows)

Goal: show how execution moves through the code from an entry point.

1. **Find entry points.** A symbol qualifies when any of these hold:
   - It is `<module>` with executable top-level calls or an `if __name__ == "__main__":` block
   - It has no incoming `CALLS` edges and is exported or public
   - It carries a route, CLI, event-handler or test-runner decorator (`@app.route`, `@click.command`, `@pytest.fixture`, and so on)
   - The name is `main`, `run`, `handler`, `cli`, or begins with `test_` (note tests as a separate kind of process)
2. **Trace forward** from each entry point along resolved `CALLS` edges (skip `low` edges).
   - Breadth-first, maximum depth 8.
   - Keep at most 3 branches per step, choosing the callees with the most outgoing edges. That keeps the flow's main path.
   - Stop at a leaf (no resolved callees) or a cycle.
3. **Keep flows with at least 3 steps.** Drop shorter ones.
4. **Name the flow** `"<Entry> → <terminal>"` in plain words, for example `"main → print sorted result"`.
5. **Cap the total** at 40 processes. Prefer the longest and the ones that cross the most clusters.
6. Write `PROCESS` and `STEP` records in the schema.

## Scale warning

If the graph has more than about 400 symbols, tell the user that clustering by reasoning is coarse at this size and that a real graph algorithm (Leiden or Louvain) run as code would give better results. Continue with the best effort anyway.
