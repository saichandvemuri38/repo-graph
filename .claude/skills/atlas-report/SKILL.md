---
name: atlas-report
description: Open the HTML report of the code graph (graph view, search, code, flows, clusters, checks). Use when the user says "show me the graph", "open the report", "let me see the code map", or wants to browse the repo visually.
argument-hint: "[--no-server]"
---

# /atlas-report — the graph as one HTML file

The report is a single self-contained file, `.claude/atlas/report/index.html`. It has the whole graph, the source, the flows, the clusters, the checks and the current changes inside it, so it opens by double-click with no server and no network.

1. Run `pwsh -NoProfile -File .claude/scripts/atlas.ps1 web --open` to refresh the graph, rebuild the report if the graph changed, serve that one file on `http://localhost:4848/` and open the browser. Run it in the background (`run_in_background: true`); stop it later with Ctrl+C or by killing the process.
2. If localhost is a problem (port in use, a proxy, a remote machine), skip the server: run `pwsh -NoProfile -File .claude/scripts/atlas.ps1 report --open`, or `atlas.ps1 web --no-server --open`. Both write the file and open it from disk. Tell the user the file path and that they can open it directly.
3. Say what they can do in it: search (Ctrl/⌘ K, with `type:class` and `path/` filters, and the **⇢ Related** toggle for graph search), click or double-click nodes, the Filters tab, Flows and Clusters tabs, **Checks** (Python security data flow, code nothing calls) and **Changes**.
4. Remind them it is a snapshot: after code changes, `atlas.ps1 report` (or `web`) makes a new one. Pushes and `work.ps1 finish` rebuild it automatically.

Read-only for the user's code. Never edit source files from this skill.
