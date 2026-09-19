---
name: atlas-publish
description: Regenerate the CodeAtlas HTML app from the existing graph without re-indexing. Use when the user says "refresh the UI", "rebuild the pages", or wants the app opened.
---

# /atlas-publish — rebuild the app pages

1. Run `sh scripts/codeatlas.sh publish`. It re-renders Overview, Symbols, Flows, Clusters and the three report lists from the graph database.
2. Reply with the absolute path of `CodeAtlas/index.html` so the user can open it in a browser. On macOS you can offer `open <path>`.
3. If it says the graph is empty, run `/atlas-index` first.

The pages are static files. Diagrams use Mermaid from a CDN, so they need an internet connection. The tables work offline.
