# HTML Generation Rules — CodeAtlas app pages

> **Implementation:** `atlas_engine/render.py` generates these pages deterministically (`sh scripts/codeatlas.sh publish`), and it is the reference for this spec. The Symbols page also has a small filter-box script. The `ui-publisher` agent follows this file only as a fallback when the engine cannot run.

The ui-publisher agent turns graph data into the pages under `CodeAtlas/`. There is no application code. Every page is static HTML that an agent writes from the graph files. Open any page by double-clicking it in a browser.

## Pages

| File | Built from | Content |
|---|---|---|
| `index.html` | `meta.md`, `clusters.md`, `processes.md`, `manifest.md` | Overview: counts, freshness, top clusters, top flows, report shortcuts |
| `symbols.html` | all shards | Every symbol grouped by file: kind, lines, summary, callers count, callees count |
| `flows.html` | `processes.md` | One card per process with a mermaid flowchart of its steps |
| `clusters.html` | `clusters.md` | One card per cluster with members, cohesion, and a mermaid overview of the links between clusters |
| `impact.html` | `CodeAtlas/reports/impact-*.html` | List of saved impact reports, newest first |
| `debug.html` | `CodeAtlas/reports/debug-*.html` | List of saved debug reports, newest first |
| `changes.html` | `CodeAtlas/reports/changes-*.html` | List of saved change-detection reports, newest first |
| `reports/<type>-<slug>-<YYYYMMDD-HHMM>.html` | one analysis | A single report: full detail and a diagram |

`assets/style.css` is the shared stylesheet. Do not modify it unless the user asks. Link it from every page (`assets/style.css` for top-level pages, `../assets/style.css` for pages in `reports/`).

## Page shell (use for every page)

- `<!doctype html>`, `<html lang="en">`, a `<title>` of the form `<Page> — CodeAtlas`.
- A `<header>` containing the product name and the `<nav>` with links to all seven top-level pages. Mark the current page with `aria-current="page"`. In `reports/` prefix links with `../`.
- One `<main>` with a single `<h1>`.
- A `<footer>` with the generation date and the graph's `lastFullIndex` from `meta.md`.
- Empty states: when data is missing, show a `<p class="empty">` that names the slash command that produces it (for example "Run /atlas-index to build the graph").

## Escaping (important)

Source code contains `<`, `>`, `&` and quotes. Escape all four as HTML entities in every value taken from the graph (signatures especially). Never paste raw signatures into HTML.

## Diagrams

Use Mermaid. Diagrams are `<pre class="mermaid">` blocks. Add this exact snippet once, just before `</body>`, on any page that contains at least one diagram:

```html
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({ startOnLoad: true, theme: "neutral" });
</script>
```

Rules for diagram content:
- `flowchart LR` for call paths, `flowchart TB` for cluster overviews.
- Node ids must be short and safe: `n1`, `n2`, ... and the label goes in brackets with the symbol name only: `n1["Sorter.partition"]`. Never use the raw symbol ID as a Mermaid id.
- Maximum 25 nodes per diagram. If more, keep the highest-connectivity nodes and add a note `+N more not shown` below the diagram.
- Solid arrows for `high`/`med` edges, dotted arrows (`-.->`) for `low`.
- Highlight the target or entry node with `style n1 fill:#fde68a,stroke:#b45309`.
- If the graph is empty, do not emit a diagram.

## symbols.html specifics

- One `<details>` per file, open by default only when there are 5 or fewer files.
- Inside, a `<table>` with columns: Symbol, Kind, Lines, Summary, Callers, Callees.
- Callers and callees are counts of resolved edges, computed by grep counts. Do not estimate.
- Add a paragraph at the top: "Use your browser's find (Ctrl/Cmd+F) to search."

## Report pages

- Title = the query, for example `Impact: sorting/quick.py::partition`.
- Put the risk badge or the freshness note first: `<span class="badge high">HIGH</span>` (classes: `low`, `medium`, `high`, `critical`).
- Body = the analyst's Markdown report converted to HTML sections, tables and one diagram.
- Filename slug: lowercase, non-alphanumerics replaced by `-`, at most 40 characters.

## List pages (impact / debug / changes)

Glob the matching `reports/` files, sort by the timestamp in the filename (newest first), and for each show: the title (read from its `<title>`), the date, the risk badge if any, and a link. Keep the 50 newest.

## Never

- No external assets except the Mermaid snippet above. No fonts, images or trackers.
- No hand-written JavaScript except that snippet.
- Do not fabricate data. If a number cannot be derived from the graph files, leave it out.
- Do not edit files under `CodeAtlas/graph/`.
