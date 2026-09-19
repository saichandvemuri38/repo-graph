"""Command line: `python -m atlas_engine <command>` (or the `codeatlas` script when pip-installed)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, reports, static_report
from .config import Paths, find_root
from .indexer import index
from .queries import Atlas


def _atlas(args: argparse.Namespace, refresh: bool = True) -> Atlas:
    atlas = Atlas(args.root or find_root())
    if refresh and not getattr(args, "no_refresh", False):
        atlas.refresh()
    return atlas


def _jsonable(value):
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if hasattr(value, "keys") and hasattr(value, "__getitem__") and not isinstance(value, dict):
        return {k: value[k] for k in value.keys()}          # sqlite3.Row
    return str(value)


def _emit(args: argparse.Namespace, result, markdown: str) -> None:
    """Print the Markdown view, or the raw result as one line of JSON for scripts (--json)."""
    if getattr(args, "json", False):
        print(json.dumps(result, default=_jsonable, separators=(",", ":")))
    else:
        print(markdown)


def cmd_index(args: argparse.Namespace) -> int:
    root = Path(args.root) if args.root else find_root()
    log = None if args.quiet else (lambda m: print(f"  {m}", file=sys.stderr)) if args.verbose else None
    result = index(root, full=args.full, log=log)
    if not args.quiet:
        print(result.summary())
        for path, why in result.parse_errors[:5]:
            print(f"  parse error: {path}: {why}")
        for path, why in result.skipped[:5]:
            print(f"  skipped: {path}: {why}")
        if result.cluster_algo and not result.cluster_algo.startswith("louvain"):
            print(f"  clustering: {result.cluster_algo}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    atlas = _atlas(args, refresh=False)
    status = atlas.status()
    _emit(args, status, reports.md_status(status))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    if args.graph:
        found = atlas.graph_search(args.text, args.limit)
        _emit(args, found, reports.md_graph_search(found))
    else:
        rows = atlas.search(args.text, args.limit)
        _emit(args, rows, reports.md_search(args.text, rows))
    return 0


def cmd_taint(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    result = atlas.taint(args.target or "", include_tests=args.tests, limit=args.limit)
    _emit(args, result, reports.md_taint(result))
    return 1 if "error" in result else 0


def cmd_defs(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    result = atlas.defs(args.symbol, args.variable)
    _emit(args, result, reports.md_defs(result))
    return 0


def cmd_at(args: argparse.Namespace) -> int:
    atlas = _atlas(args, refresh=False)
    found = atlas.symbols_at(args.file, args.start, args.end)
    _emit(args, {"file": args.file, "start": args.start, "end": args.end or args.start, "symbols": found},
          "\n".join(f"{x['id']}  (lines {x['start']}-{x['end']})" for x in found) or "No function, method or class covers those lines.")
    return 0


def cmd_unused(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    result = atlas.unused(args.limit)
    _emit(args, result, reports.md_unused(result))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    sym, cands, kind = atlas.resolve_target(args.symbol)
    if kind == "symbol":
        result = atlas.context(sym[0]["id"])
        _emit(args, result, reports.md_context(result))
        return 0
    _emit(args, {"error": kind, "candidates": [dict(r) for r in cands]}, reports.md_candidates(f"Context: {args.symbol}", [dict(r) for r in cands]))
    return 1


def cmd_impact(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    result = atlas.impact(args.target, args.direction, args.depth)
    text = reports.md_impact(result)
    _emit(args, result, text)
    return 1 if "error" in result else 0


def cmd_trace(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    result = atlas.trace(args.source, args.dest)
    _emit(args, result, reports.md_trace(result))
    return 0


def cmd_changes(args: argparse.Namespace) -> int:
    atlas = _atlas(args, refresh=False)
    result = atlas.changes(args.base)
    text = reports.md_changes(result)
    _emit(args, result, text)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Pre-push safety net. Prints a one-screen summary and never blocks unless --fail-on is set."""
    atlas = _atlas(args, refresh=False)
    base = args.base
    if not base and atlas.in_git():
        upstream = atlas._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        base = upstream.strip() if upstream else None
    result = atlas.changes(base)
    order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    if args.json:
        print(json.dumps(result, default=_jsonable, separators=(",", ":")))
        return 0
    print(f"CodeAtlas: {result['risk']} risk vs {result['baseline']} — {len(result['modified'])} modified, "
          f"{len(result['removed'])} removed, {result['affected']} dependants, {len(result['processes'])} flows")
    if args.markdown:
        print(reports.md_changes(result))
    if args.fail_on != "none" and order.index(result["risk"]) >= order.index(args.fail_on.upper()):
        return 1
    return 0


def cmd_locate(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    text = args.text if args.text != "-" else sys.stdin.read()
    result = atlas.locate(text)
    md = reports.md_locate(result)
    _emit(args, result, md)
    return 0


def _open_file(path: Path) -> None:
    import webbrowser
    webbrowser.open(path.resolve().as_uri())


def cmd_report(args: argparse.Namespace) -> int:
    """Write the one-file HTML report (the interactive explorer with the graph embedded)."""
    root = Path(args.root) if args.root else find_root()
    if not args.no_refresh:
        index(root)
    out = static_report.build(root)
    print(out)
    print(out.resolve().as_uri())
    if args.open:
        _open_file(out)
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    """Show the HTML report. Rebuilds it first when the graph has changed; serves that one file on localhost."""
    root = Path(args.root) if args.root else find_root()
    if not args.no_refresh:
        index(root)
    if args.rebuild or not static_report.is_current(root):
        out = static_report.build(root)
    else:
        out = static_report.report_path(Paths(root))
    if args.no_server:
        print(out)
        print(out.resolve().as_uri())
        if args.open:
            _open_file(out)
        return 0
    from .static_server import serve
    serve(out, args.port, args.open)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .mcp_server import serve
    serve(Path(args.root) if args.root else find_root())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codeatlas", description="Code knowledge graph for AI agents.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--root", help="project root (default: nearest folder containing .claude/atlas/, or $CODEATLAS_ROOT)")
    p.add_argument("--no-refresh", action="store_true", help="query without re-indexing changed files first")
    sub = p.add_subparsers(dest="command", required=True)
    js = argparse.ArgumentParser(add_help=False)
    js.add_argument("--json", action="store_true", help="print the result as one line of JSON (for scripts)")

    s = sub.add_parser("index", help="build or refresh the graph")
    s.add_argument("--full", action="store_true", help="re-parse every file")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(fn=cmd_index)

    s = sub.add_parser("status", parents=[js], help="graph statistics")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("search", parents=[js], help="full-text search over symbols")
    s.add_argument("text")
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--graph", action="store_true", help="also return code connected to the matches through the graph")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("taint", parents=[js], help="paths from untrusted input to dangerous calls (Python)")
    s.add_argument("target", nargs="?", default="", help="a file or symbol to limit the report to")
    s.add_argument("--tests", action="store_true", help="include test files")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(fn=cmd_taint)

    s = sub.add_parser("defs", parents=[js], help="which assignments reach each use of a variable in a function (Python)")
    s.add_argument("symbol")
    s.add_argument("variable")
    s.set_defaults(fn=cmd_defs)

    s = sub.add_parser("at", parents=[js], help="which function, method or class covers lines of a file")
    s.add_argument("file")
    s.add_argument("start", type=int)
    s.add_argument("end", type=int, nargs="?")
    s.set_defaults(fn=cmd_at)

    s = sub.add_parser("unused", parents=[js], help="symbols nothing calls, with signs of hidden (dynamic) use")
    s.add_argument("--limit", type=int, default=60)
    s.set_defaults(fn=cmd_unused)

    s = sub.add_parser("context", parents=[js], help="callers, callees, flows for one symbol")
    s.add_argument("symbol")
    s.set_defaults(fn=cmd_context)

    s = sub.add_parser("impact", parents=[js], help="blast radius of changing a symbol or file")
    s.add_argument("target")
    s.add_argument("--direction", choices=["upstream", "downstream"], default="upstream")
    s.add_argument("--depth", type=int, default=3)
    s.set_defaults(fn=cmd_impact)

    s = sub.add_parser("trace", parents=[js], help="shortest call path between two symbols")
    s.add_argument("source")
    s.add_argument("dest")
    s.set_defaults(fn=cmd_trace)

    s = sub.add_parser("changes", parents=[js], help="which symbols changed (vs git ref, or vs last index without git)")
    s.add_argument("--base", help="git ref to compare against (default HEAD)")
    s.set_defaults(fn=cmd_changes)

    s = sub.add_parser("check", parents=[js], help="one-line risk summary; used by the pre-push hook")
    s.add_argument("--base")
    s.add_argument("--markdown", action="store_true", help="also print the full report (for CI job summaries)")
    s.add_argument("--fail-on", choices=["none", "high", "critical"], default="none")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("locate", parents=[js], help="map an error message or stack trace to symbols ('-' reads stdin)")
    s.add_argument("text")
    s.set_defaults(fn=cmd_locate)

    s = sub.add_parser("report", help="write the one-file HTML report (the explorer with the whole graph embedded)")
    s.add_argument("--open", action="store_true", help="open the file in the browser")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("web", help="show the HTML report on localhost (rebuilds it when the graph changed)")
    s.add_argument("--port", type=int, default=4848)
    s.add_argument("--open", action="store_true", help="open the browser")
    s.add_argument("--no-server", action="store_true", help="do not start a server; just make sure the file is current and print its path")
    s.add_argument("--rebuild", action="store_true", help="rebuild the report even if it is current")
    s.set_defaults(fn=cmd_web)

    s = sub.add_parser("serve", help="run the MCP server on stdio")
    s.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130
