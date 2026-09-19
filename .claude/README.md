# CodeAtlas — a code knowledge graph for Claude agents

CodeAtlas reads a codebase and builds a **graph**: symbols (functions, classes, methods) and the relations between them (calls, imports, inheritance, references), grouped into clusters and execution flows. Claude uses the graph to answer three questions:

1. **What is this and who uses it?** — `/atlas-context`
2. **What breaks if I change it?** — `/atlas-impact`, `/atlas-changes`
3. **Why is it failing?** — `/atlas-debug`, `/atlas-trace`

Results show up in the terminal and as static HTML pages in `CodeAtlas/`.

## Two layers

| Layer | Made of | Does |
|---|---|---|
| **Engine** `atlas_engine/` | Python code | The exact work: parse, link, store, cluster, query, diff, render, serve |
| **Agents** `.claude/` | Markdown: agents, skills, prompts | The judgment: read the source, weigh evidence, rank hypotheses, propose fixes, explain |

The engine never guesses and never edits your code. The agents never re-derive facts the engine already has.

```
source files
   │  Python: ast (exact)   JS/TS/Java/Go: tree-sitter
   ▼
parsers ──► resolver ──► SQLite graph ──► clusters (Louvain) + flows
               │             │
   scope, imports,           ├──► MCP server ──► Claude Code   (atlas_* tools)
   class hierarchy,          ├──► CLI                          (sh scripts/codeatlas.sh ...)
   inferred types            ├──► text shards (CodeAtlas/graph/, the format agents can grep)
                             └──► HTML app (CodeAtlas/*.html, reports/)
```

### What the engine understands
- **Python** (exact `ast`): definitions with exact line ranges, calls, imports (absolute, relative, aliased, star, re-exports), inheritance and method resolution, decorators, callbacks passed as values, `self.method()`, `super()`, and receiver types inferred from constructors (`x = Foo()`), parameter annotations, `self.attr = Foo()`, and function return annotations (`x = make()` where `make() -> Foo`).
- **JavaScript, TypeScript, TSX, Java, Go** (tree-sitter): the same, with each language's import and package rules (Go and Java packages share a namespace across files).
- **Broken files**: a file with a syntax error still yields its classes and functions through a tolerant scan, so it is never invisible.
- **Confidence**: `high` = proven by scope, import or hierarchy; `med` = inferred from types or a unique name; `low` = guessed by proximity. Unresolved calls are listed, never silently dropped.

## Commands

| You want to… | Claude Code | Terminal |
|---|---|---|
| Build or refresh the graph | `/atlas-index` | `sh scripts/codeatlas.sh index [--full]` |
| Everything about a symbol | `/atlas-context X` | `... context X` |
| What breaks if I change X | `/atlas-impact X` | `... impact X --save` |
| How does A reach B | `/atlas-trace A B` | `... trace A B` |
| Debug an error | `/atlas-debug <text>` | `... locate '<text>' --save` |
| What did I change | `/atlas-changes` | `... changes [--base origin/main] --save` |
| Rebuild the static pages | `/atlas-publish` | `... publish` |
| Open the interactive explorer | `/atlas-explore` | `... web --open` |
| Add another repository to the explorer | | `... projects add /path/to/repo` |

MCP tools (server `codeatlas`): `atlas_status`, `atlas_index`, `atlas_search`, `atlas_context`, `atlas_impact`, `atlas_trace`, `atlas_locate_error`, `atlas_changes`, `atlas_flows`, `atlas_clusters`.

## Staying up to date

| When | What updates the graph | Mechanism |
|---|---|---|
| Claude edits a file | Immediately | Claude Code `PostToolUse` hook in `.claude/settings.json` |
| A Claude session starts | On start | `SessionStart` hook |
| You ask Claude a question through MCP | Before answering | The MCP server re-indexes changed files first |
| `git commit`, `merge`, `checkout`, `rebase` | After the operation | Git hooks from `scripts/install-hooks.sh` |
| `git push` | Before the push, with a one-line risk summary that never blocks | `pre-push` hook |
| Every push and pull request on GitHub | Full rebuild, tests, an impact summary on the run page, and the app as a downloadable artifact | `.github/workflows/codeatlas.yml` |

Only changed files are re-parsed (size and mtime, then SHA-256). An index with nothing new takes a fraction of a second.

## The interactive explorer (graph view, search, code, flows, AI chat)

```sh
sh scripts/codeatlas.sh web --open        # http://localhost:4848  (or /atlas-explore in Claude Code)
sh scripts/codeatlas.sh projects add /path/to/another/repo    # browse several repositories; the folder is never written to
```

A local web app served by the engine (standard library only, plain JavaScript, no build step). It reads the same database as the MCP server, so it is always consistent with what Claude sees. Deep links work: `http://localhost:4848/?project=frontend-farmers#node=path.tsx::Name`.

| Feature | Where |
|---|---|
| Repository switcher with search, re-analyze, remove, and "Analyze a new repository" (any local folder) | Header |
| Node search with live highlighting: `name`, `path/prefix`, `type:class`, combinations; Ctrl/⌘ K | Header |
| WebGL graph (sigma): Force, Sequential tree and Radial layouts; zoom, fit, focus, clear, stop/re-run layout; drag nodes; hover lights up neighbours; double-click focuses a neighbourhood | Canvas |
| Node and edge type filters, hide leaves, hide low-confidence edges, focus depth (N hops), colour by type or cluster, legend | Left panel, Filters |
| File explorer tree with symbols and counts | Left panel, Explorer |
| Details: definition, callers, callees, extends, flows, cluster, unresolved calls; **Impact** (colours dependants by depth, shows risk and tests), **Depends on**, **Trace to…** | Right panel |
| Code inspector with line numbers, syntax colours, the symbol's range highlighted, AI citations | Right panel, Code |
| Execution flows: list, filter, highlight in graph, SVG flow diagram with pan/zoom, combined map, copy Mermaid | Right panel, Flows |
| Clusters with cohesion, highlight members, colour the graph by cluster | Right panel, Clusters |
| Query console: read-only SQL over the graph with examples, schema, "highlight in graph", copy CSV | ⌗ Query button |
| Changes: symbols changed since the last commit or index, dependants, tests to run, highlight in graph | Header |
| Status bar: node/edge counts, languages, index age, stale-file warning with Re-index, parse errors, skipped files | Bottom |
| Nexus AI chat: Claude answers using tools over the graph (search, symbol context, impact, trace, SQL, read source, error location, changes), with live tool cards and clickable citations | Right panel, Nexus AI |
| Live refresh (polls every 8 s), dark/light theme, keyboard shortcuts (`?` for help), help and settings dialogs | Everywhere |

**Nexus AI setup:** open Settings and paste an Anthropic API key, or start the server with `ANTHROPIC_API_KEY` set. The key lives in `~/.codeatlas/config.json` (owner-readable) and is used by the local server only; the browser never sees it.

**Security:** the server listens on 127.0.0.1 only, rejects requests with a foreign `Host` header, requires a random per-run token that only the served page knows, restricts source viewing to indexed files, and opens the database read-only for the SQL console.

**Large repositories:** above about 15,000 nodes the explorer asks whether to load an overview (folders and files) or the full graph, and never draws more than the 20,000 most-connected nodes. Search, details, flows and AI chat work regardless.

### How this compares with the GitNexus web UI

| GitNexus web UI | CodeAtlas explorer |
|---|---|
| Repo switcher, analyze a repository | Yes. Local folders only (no GitHub-URL clone, no zip drag-and-drop) |
| Search nodes, filters, three layouts, focus depth, legend, file explorer, code inspector, processes with diagrams, help, status bar | Yes |
| Cypher query console | **Different:** SQL over SQLite (the schema is listed in the console) |
| Nexus AI with tool cards and citations | Yes, **Claude only** (GitNexus supports several providers) |
| Semantic search with in-browser embeddings (WebGPU) | **No.** Keyword full-text search only |
| Chinese UI (i18n) | **No.** English only |
| Onboarding tour | **No.** A help dialog only |
| Also here, not in GitNexus's UI | Impact overlay, trace, uncommitted-changes view, live refresh, dark mode |

## Using CodeAtlas on another repo

The engine stays in this folder; each repo you attach only gets small config files and its own graph in `<repo>/CodeAtlas/`.

```sh
sh scripts/attach.sh /path/to/other/repo        # once per repo (add --no-hooks to skip git hooks)
```

That adds to the other repo: `.claude/` (agents, skills, prompts, rules, hooks), `.mcp.json`, two wrapper scripts in `scripts/`, and installs git hooks if it is a git repo. It never overwrites an existing file and merges existing `.mcp.json` / `settings.json`. In a git repo the generated files are added to `.git/info/exclude`, so `git status` in that repo stays clean (`.claude/` is left visible so you can choose to commit it).

You need: this folder's `.venv` (see Setup), Python 3.10+, and Claude Code. Then open the other repo in Claude Code and approve the `codeatlas` MCP server once. `/atlas-*` commands and the `atlas_*` tools work there. Only browsing needs a second step: `sh scripts/codeatlas.sh web --open` from this folder, then pick the repo in the project menu (or `sh scripts/codeatlas.sh projects add /path/to/repo`).

Caveats: the wrappers contain this folder's absolute path, so they are per-machine (rerun `attach.sh` after moving the engine folder). Teammates need the engine too. Any repo language other than Python needs the `langs` extra installed in `.venv`.

## Setup (once)

```sh
cd /Users/saichandvemuri/Downloads/Dsa/python
python3 -m venv .venv
.venv/bin/pip install ".[all]"     # or: pip install mcp networkx tree-sitter-language-pack
sh scripts/codeatlas.sh index --full
git init                           # only if this folder is not a repository yet
sh scripts/install-hooks.sh        # commit / merge / checkout / push hooks
```

Open Claude Code in this folder. It asks once to approve the project's MCP server (`.mcp.json`). Then open `CodeAtlas/index.html`.

`stdlib only` also works: without `.venv` the engine still indexes Python with no third-party packages. `networkx` adds Louvain clusters, `tree-sitter-language-pack` adds JS/TS/Java/Go, `mcp` adds the MCP server.

## Folder map

```
atlas_engine/        the engine (Python package; named atlas_engine because macOS treats CodeAtlas/ and codeatlas/ as one folder)
  web/               the explorer front-end (index.html, app.css, js/*, vendor/ = sigma, graphology, forceatlas2 with licences)
  webserver.py webapi.py chat.py projects.py workspace.py   HTTP server, JSON API, AI chat loop, project registry, monorepo/tsconfig alias resolution
  parsers/           python_ast.py (exact), treesitter.py (JS/TS/Java/Go)
  resolve.py         cross-file name resolution and confidence
  store.py           SQLite schema, full-text search
  indexer.py         incremental pipeline with a lock
  cluster.py         Louvain clusters and execution flows
  queries.py         search, context, impact, trace, changes, locate_error
  reports.py         Markdown views    render.py  HTML pages
  mcp_server.py      MCP tools         cli.py     command line
scripts/             codeatlas.sh (launcher), reindex.sh, install-hooks.sh
tests/               pytest suite (parsers, resolution, incremental, git diff, languages)
.mcp.json            registers the MCP server for Claude Code
.github/workflows/   CI: rebuild + tests + impact summary + app artifact
.claude/
  CLAUDE.md          rules Claude follows here
  settings.json      permissions and the auto-index hooks
  agents/            bug-tracer, impact-analyst, symbol-analyst, change-detector (judgment)
                     graph-indexer, graph-linker, cluster-mapper, ui-publisher (fallbacks)
  skills/            the /atlas-* commands
  prompts/           shared rules: schema, resolution, risk rubric, debug playbook, HTML rules, ...
CodeAtlas/           the app: index/symbols/flows/clusters/impact/debug/changes .html, reports/, graph/
```

Generated files (`atlas.db`, shards, generated HTML, reports) are git-ignored. They are rebuilt by hooks, CI and the MCP server, so commits stay free of graph churn.

## Who owns which file

| File | Written by |
|---|---|
| `CodeAtlas/graph/atlas.db` | engine (source of truth) |
| `CodeAtlas/graph/shards/*.graph.md`, `manifest.md`, `meta.md`, `unresolved.md`, `clusters.md`, `processes.md` | engine, exported from the database on every index (fallback agents can write the same format) |
| `CodeAtlas/*.html`, `CodeAtlas/reports/*.html` | engine renderer (`ui-publisher` agent only as a fallback) |
| `CodeAtlas/assets/style.css` | you |

## Limits — read this

- **Static analysis is approximate for dynamic code.** `getattr`, string dispatch, monkey-patching, decorators that register handlers, and duck-typed calls on untyped values are invisible or resolved at low confidence. Type inference reaches annotations and constructors only, not general data flow.
- **JS/TS/Java/Go are shallower than Python.** They handle declarations, imports, inheritance and typed receivers, but not generics, overload resolution, interface implementations across files, or JS re-export chains.
- **Clusters and flows are heuristics** (Louvain over call edges; flows are entry-point traces of at most 8 levels). Use them to orient, not to prove.
- **`changes` and `check`** use exact symbol-body hashes. A moved-but-unchanged symbol counts as unchanged; a rename shows as removed + added.
- **Taint and data-flow analysis is not implemented.** GitNexus's PDG and taint layers are a separate, larger project.
- **The static pages** in `CodeAtlas/*.html` are plain files (diagrams need internet for Mermaid). The interactive explorer is the richer view.
- **Explorer:** verified in Chrome only (not Firefox or Safari, not touch screens). The AI chat has been tested against a fake Claude API, not a live one.
- **Not covered:** languages other than Python, JS/TS/TSX, Java and Go; cross-repo groups; embeddings and semantic search; a wiki generator; a rename tool.
- **JS/TS resolution** understands relative imports, `package.json` workspaces (`exports`), `tsconfig` `paths`/`baseUrl`, default imports, and components wrapped in `forwardRef`/`memo`. It does not follow `tsconfig` `extends`, re-export chains through `export *`, or dynamic `import()`.
