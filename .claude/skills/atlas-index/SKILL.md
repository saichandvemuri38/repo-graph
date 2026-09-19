---
name: atlas-index
description: Build or refresh the CodeAtlas code graph for this repo. Use when the user says "index this repo", "build the graph", "refresh the graph", or when other atlas skills report a missing or stale graph.
argument-hint: "[--full]"
---

# /atlas-index — build or refresh the code graph

The engine does the work. It parses exactly (Python `ast`, tree-sitter for JS/TS/Java/Go), re-parses only changed files, links names across files, clusters, finds flows, exports the text shards and renders the HTML app.

Arguments: `$ARGUMENTS` (pass `--full` through to force a full re-parse).

## Steps

1. Run `sh scripts/codeatlas.sh index $ARGUMENTS` with Bash. If the MCP tool `atlas_index` is available you may call it instead.
2. Show the user the summary line and anything it printed: parse errors, skipped files, and the clustering note.
3. Explain each of those briefly:
   - **parse error**: the file has a syntax error. Its classes and functions were recovered by a tolerant scan, but it has no call edges until the syntax is fixed.
   - **skipped: no parser for X**: the language is not supported by the engine. Offer the fallback: launch the `graph-indexer` agent on those files (see "Fallback" below).
   - **clustering by-file**: networkx is missing. Suggest `pip install networkx` in the project's `.venv`.
4. Tell the user where the app is: the absolute path of `CodeAtlas/index.html`.
5. If the command failed with "No module named" or the engine cannot run at all, tell the user what is missing (`python3 -m venv .venv && .venv/bin/pip install ".[all]"`) and use the fallback.

## Fallback (only when the engine cannot handle the files)

Follow this only for files the engine skipped, or if it cannot run at all:

1. Glob the unsupported source files, hash them with `shasum -a 256` and write `<relpath>|<sha12>` pairs.
2. Launch up to 4 `graph-indexer` agents in parallel with batches of about 8 files. Each writes shards in the format of `.claude/prompts/graph-schema.md`.
3. Launch one `graph-linker` agent for those shards.
4. Run `sh scripts/codeatlas.sh publish`.

Never edit source files from this skill.
