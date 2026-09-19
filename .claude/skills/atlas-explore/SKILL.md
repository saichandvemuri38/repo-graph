---
name: atlas-explore
description: Open the interactive CodeAtlas explorer (graph view, search, code inspector, flows, AI chat) in the browser. Use when the user says "open the graph", "show me the graph view", "start the explorer", or wants to browse a repository visually.
argument-hint: "[project-name | /path/to/repo]"
---

# /atlas-explore — the interactive graph explorer

Arguments: `$ARGUMENTS` (optional: a project name already in the explorer, or a folder to add).

1. If the argument is a folder path, register and index it first: `sh scripts/codeatlas.sh projects add "<path>"`. The folder is never modified; its graph is stored under `~/.codeatlas`.
2. Check whether the explorer is already running: `curl -s -o /dev/null -w "%{http_code}" http://localhost:4848/`. If it answers 200, do not start a second one.
3. Otherwise start it in the background with Bash (`run_in_background: true`): `sh scripts/codeatlas.sh web --port 4848 [--project <name>]`.
4. Tell the user the URL: `http://localhost:4848/?project=<name>` (the server prints the exact one). It only listens on this machine.
5. Mention what they can do there: search (Ctrl/⌘ K, with `type:class` and `path/` filters), click or double-click nodes, the Filters tab, the Flows and Clusters tabs, the Query console (read-only SQL), and Nexus AI (needs an Anthropic API key in Settings).
6. To stop it: `pkill -f "atlas_engine web"`.

Read-only for the user's code. Never edit source files from this skill.
