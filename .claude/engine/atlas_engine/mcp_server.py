"""MCP server: exposes the graph to Claude Code (or any MCP client) as tools over stdio.

Every query tool first brings the graph up to date (a no-op when nothing changed), so answers reflect the
working tree even without git hooks. Set CODEATLAS_AUTO_REFRESH=0 to disable.
"""

from __future__ import annotations

import functools
import os
import threading
from pathlib import Path

from . import reports
from .indexer import index
from .queries import Atlas

INSTRUCTIONS = """CodeAtlas is a code knowledge graph of this repository (symbols, calls, imports, inheritance, clusters, flows).
Use it instead of grepping when you need structure:
- atlas_search: find symbols by concept or name
- atlas_context: callers, callees and flows of one symbol
- atlas_impact: blast radius BEFORE editing a symbol or file (warn the user on HIGH or CRITICAL)
- atlas_trace: how does A reach B
- atlas_locate_error: map an error message or stack trace to symbols, with the call path around them
- atlas_changes: which symbols your uncommitted changes touch, and what depends on them
- atlas_unused: symbols nothing calls, with signs of hidden (dynamic) use; check before saying code is dead
- atlas_taint / atlas_defs: Python data flow: untrusted input reaching dangerous calls, and where a variable's value comes from
The graph is an index, not the truth. Edges marked low confidence, and unresolved calls, are possible not proven.
Confirm important conclusions in the source."""


def _make_server():
    try:
        from mcp.server.mcpserver import MCPServer as Server   # mcp >= 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server        # mcp 1.x
    return Server("codeatlas", instructions=INSTRUCTIONS)


def build_server(root: Path):
    server = _make_server()
    atlas = Atlas(root, shared=True)
    lock = threading.Lock()   # tools run on worker threads; the shared SQLite connection is used one at a time
    auto = os.environ.get("CODEATLAS_AUTO_REFRESH", "1") != "0"

    def tool():
        def wrap(fn):
            @functools.wraps(fn)
            def locked(*args, **kwargs):
                with lock:
                    return fn(*args, **kwargs)
            return server.tool()(locked)
        return wrap

    def fresh() -> Atlas:
        if auto:
            atlas.refresh(min_interval=2.0)
        return atlas

    def pick(a: Atlas, target: str):
        """Resolve one symbol or return a Markdown message explaining why we cannot."""
        rows, cands, kind = a.resolve_target(target)
        if kind == "symbol":
            return rows[0]["id"], None
        if kind == "file":
            return None, None
        return None, reports.md_candidates(f"'{target}'", [dict(r) for r in cands]) if cands else f"No symbol matched '{target}'."

    @tool()
    def atlas_status() -> str:
        """Graph statistics: files, symbols, edges, unresolved calls, clusters, flows, files with parse errors."""
        return reports.md_status(fresh().status())

    @tool()
    def atlas_index(full: bool = False) -> str:
        """Re-index the repository now. Only changed files are re-parsed unless full=True."""
        result = index(atlas.root, full=full)
        atlas._last_refresh = 0.0
        return result.summary()

    @tool()
    def atlas_search(text: str, limit: int = 15, graph: bool = True) -> str:
        """Find symbols by name or concept. graph=True (default) also returns code connected to the keyword matches through calls, references and inheritance, each with the reason it was returned; graph=False is plain keyword search."""
        a = fresh()
        return reports.md_graph_search(a.graph_search(text, limit)) if graph else reports.md_search(text, a.search(text, limit))

    @tool()
    def atlas_context(symbol: str) -> str:
        """360-degree view of one symbol: definition, callers, callees, class relations, cluster, flows, source. Accepts a name, Class.method, or full id 'path.py::Class.method'."""
        a = fresh()
        sid, message = pick(a, symbol)
        if sid is None:
            return message or f"'{symbol}' is a file; use atlas_impact for files or pass a symbol."
        return reports.md_context(a.context(sid))

    @tool()
    def atlas_impact(target: str, direction: str = "upstream", depth: int = 3) -> str:
        """Blast radius of changing a symbol or file. direction='upstream' = who depends on it (default), 'downstream' = what it depends on. Returns risk LOW/MEDIUM/HIGH/CRITICAL, dependants by depth, flows touched and suggested checks. Run before editing."""
        return reports.md_impact(fresh().impact(target, direction if direction in ("upstream", "downstream") else "upstream", max(1, min(depth, 3))))

    @tool()
    def atlas_trace(source: str, dest: str) -> str:
        """Shortest call path from one symbol to another (answers 'how does A reach B' and 'why does this get called')."""
        return reports.md_trace(fresh().trace(source, dest))

    @tool()
    def atlas_locate_error(text: str) -> str:
        """Debug entry point. Paste an error message and/or stack trace; returns the frames mapped to symbols, where the message text appears in the source, and the callers and callees around the most relevant symbol."""
        return reports.md_locate(fresh().locate(text))

    @tool()
    def atlas_changes(base: str = "") -> str:
        """Symbols changed by your uncommitted work (vs git HEAD, or vs a ref you pass; vs the last index when there is no git), their direct dependants, flows touched and overall risk. Run before committing."""
        return reports.md_changes(atlas.changes(base or None))

    @tool()
    def atlas_unused(limit: int = 60) -> str:
        """Functions, methods and classes nothing calls, split into: no sign of hidden use (likely dead), leads to check (a string, run-time lookup or unresolved call), and reached implicitly (entry points, decorators, overrides). Use before claiming code is unused."""
        return reports.md_unused(fresh().unused(limit))

    @tool()
    def atlas_taint(target: str = "", include_tests: bool = False, file: str = "") -> str:
        """Python security data flow: paths from untrusted input (input(), sys.argv, request.args, HTTP handler parameters, environment) to dangerous calls (os.system, eval, cursor.execute, open, pickle.loads ...), across function calls, with each step and a fix. target limits it to a file or symbol. Leads to verify, not proof: it is not path-sensitive."""
        return reports.md_taint(fresh().taint(target or file, include_tests))

    @tool()
    def atlas_defs(symbol: str, variable: str) -> str:
        """Reaching definitions in one Python function: for each use of `variable`, which assignments can supply its value. Use it to answer 'where does this value come from'."""
        return reports.md_defs(fresh().defs(symbol, variable))

    @tool()
    def atlas_flows(limit: int = 15) -> str:
        """List detected execution flows (entry point to terminal call) with their steps."""
        a = fresh()
        out = ["# Flows", ""]
        for p in a.db.execute("SELECT * FROM processes ORDER BY CAST(SUBSTR(id,2) AS INTEGER) LIMIT ?", (limit,)):
            steps = [reports.short(r["symbol_id"]) for r in a.db.execute("SELECT symbol_id FROM process_steps WHERE process_id=? ORDER BY ord", (p["id"],))]
            out.append(f"- **{p['id']} {p['name']}** ({p['summary']}): " + " → ".join(steps))
        return "\n".join(out) if len(out) > 2 else "No flows detected yet."

    @tool()
    def atlas_clusters(limit: int = 15) -> str:
        """List functional clusters (areas of the code) with their size, cohesion and main members."""
        a = fresh()
        out = ["# Clusters", ""]
        for c in a.db.execute("SELECT * FROM clusters ORDER BY CAST(SUBSTR(id,2) AS INTEGER) LIMIT ?", (limit,)):
            members = [reports.short(r["symbol_id"]) for r in a.db.execute("SELECT symbol_id FROM cluster_members WHERE cluster_id=? LIMIT 8", (c["id"],))]
            out.append(f"- **{c['id']} {c['label']}** ({c['size']} symbols, cohesion {c['cohesion']}): {', '.join(members)}")
        return "\n".join(out) if len(out) > 2 else "No clusters yet."

    return server


def serve(root: Path) -> None:
    server = build_server(root)
    try:
        server.run(transport="stdio")
    except TypeError:
        server.run()
