"""Command line: `python -m atlas_engine <command>` (or the `codeatlas` script when pip-installed)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, render, reports
from .config import Paths, find_root
from .indexer import index
from .queries import Atlas


def _atlas(args: argparse.Namespace, refresh: bool = True) -> Atlas:
    atlas = Atlas(args.root or find_root())
    if refresh and not getattr(args, "no_refresh", False):
        atlas.refresh()
    return atlas


def _save(atlas: Atlas, kind: str, title: str, markdown: str) -> None:
    path = render.write_report(atlas.paths, atlas.store, kind, title, markdown)
    print(f"\nSaved report: {path}", file=sys.stderr)


def cmd_index(args: argparse.Namespace) -> int:
    root = Path(args.root) if args.root else find_root()
    log = None if args.quiet else (lambda m: print(f"  {m}", file=sys.stderr)) if args.verbose else None
    result = index(root, full=args.full, publish=not args.no_publish, log=log)
    if not args.quiet:
        print(result.summary())
        for path, why in result.parse_errors[:5]:
            print(f"  parse error: {path}: {why}")
        for path, why in result.skipped[:5]:
            print(f"  skipped: {path}: {why}")
        if result.cluster_algo and not result.cluster_algo.startswith("louvain"):
            print(f"  clustering: {result.cluster_algo}")
        print(f"  app: {Paths(root).app / 'index.html'}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    atlas = _atlas(args, refresh=False)
    print(reports.md_status(atlas.status()))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    print(reports.md_search(args.text, atlas.search(args.text, args.limit)))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    sym, cands, kind = atlas.resolve_target(args.symbol)
    if kind == "symbol":
        print(reports.md_context(atlas.context(sym[0]["id"])))
        return 0
    print(reports.md_candidates(f"Context: {args.symbol}", [dict(r) for r in cands]))
    return 1


def cmd_impact(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    result = atlas.impact(args.target, args.direction, args.depth)
    text = reports.md_impact(result)
    print(text)
    if args.save and "error" not in result:
        _save(atlas, "impact", f"Impact: {args.target}", text)
    return 1 if "error" in result else 0


def cmd_trace(args: argparse.Namespace) -> int:
    atlas = _atlas(args)
    print(reports.md_trace(atlas.trace(args.source, args.dest)))
    return 0


def cmd_changes(args: argparse.Namespace) -> int:
    atlas = _atlas(args, refresh=False)
    result = atlas.changes(args.base)
    text = reports.md_changes(result)
    print(text)
    if args.save:
        _save(atlas, "changes", "Changes since last index" if not args.base else f"Changes vs {args.base}", text)
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
    print(md)
    if args.save:
        _save(atlas, "debug", f"Debug: {result['message'] or 'error'}"[:70], md)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Render Markdown from stdin (for example an agent's debug analysis) into a saved HTML report."""
    atlas = _atlas(args, refresh=False)
    markdown = sys.stdin.read()
    if not markdown.strip():
        print("No Markdown on stdin.", file=sys.stderr)
        return 1
    path = render.write_report(atlas.paths, atlas.store, args.kind, args.title, markdown)
    print(path)
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    atlas = _atlas(args, refresh=False)
    render.publish_all(atlas.store, atlas.paths)
    print(f"Published {atlas.paths.app / 'index.html'}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from .webserver import serve_web
    root = Path(args.root) if args.root else find_root()
    if not args.no_index:
        index(root, publish=False)              # cheap when nothing changed; makes a first run useful
    serve_web(args.port, root, args.open, args.project)
    return 0


def cmd_projects(args: argparse.Namespace) -> int:
    from . import projects
    if args.action == "list":
        rows = projects.list_projects()
        if not rows:
            print("No projects registered. Add one with: codeatlas projects add /path/to/repo")
        for p in rows:
            st = p.stats()
            print(f"{p.name:24} {p.root}  " + (f"{st['files']} files, {st['symbols']} symbols" if st else "not indexed"))
        return 0
    if args.action == "add":
        if not args.path:
            print("Give a folder: codeatlas projects add /path/to/repo", file=sys.stderr)
            return 2
        root = Path(args.path).expanduser()
        if not root.is_dir():
            print(f"Not a folder: {root}", file=sys.stderr)
            return 2
        proj = projects.register(root, args.name)
        result = index(proj.root, publish=False, data_dir=proj.data_dir)
        print(f"{proj.name}: {result.summary()}")
        return 0
    if args.action == "remove":
        ok = projects.remove(args.path or "")
        print("removed" if ok else "no such project")
        return 0 if ok else 1
    return 2


def cmd_serve(args: argparse.Namespace) -> int:
    from .mcp_server import serve
    serve(Path(args.root) if args.root else find_root())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codeatlas", description="Code knowledge graph for AI agents.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--root", help="project root (default: nearest folder containing CodeAtlas/, or $CODEATLAS_ROOT)")
    p.add_argument("--no-refresh", action="store_true", help="query without re-indexing changed files first")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("index", help="build or refresh the graph")
    s.add_argument("--full", action="store_true", help="re-parse every file")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--verbose", action="store_true")
    s.add_argument("--no-publish", action="store_true", help="skip regenerating HTML")
    s.set_defaults(fn=cmd_index)

    s = sub.add_parser("status", help="graph statistics")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("search", help="full-text search over symbols")
    s.add_argument("text")
    s.add_argument("--limit", type=int, default=15)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("context", help="callers, callees, flows for one symbol")
    s.add_argument("symbol")
    s.set_defaults(fn=cmd_context)

    s = sub.add_parser("impact", help="blast radius of changing a symbol or file")
    s.add_argument("target")
    s.add_argument("--direction", choices=["upstream", "downstream"], default="upstream")
    s.add_argument("--depth", type=int, default=3)
    s.add_argument("--save", action="store_true", help="save an HTML report")
    s.set_defaults(fn=cmd_impact)

    s = sub.add_parser("trace", help="shortest call path between two symbols")
    s.add_argument("source")
    s.add_argument("dest")
    s.set_defaults(fn=cmd_trace)

    s = sub.add_parser("changes", help="which symbols changed (vs git ref, or vs last index without git)")
    s.add_argument("--base", help="git ref to compare against (default HEAD)")
    s.add_argument("--save", action="store_true")
    s.set_defaults(fn=cmd_changes)

    s = sub.add_parser("check", help="one-line risk summary; used by the pre-push hook")
    s.add_argument("--base")
    s.add_argument("--markdown", action="store_true", help="also print the full report (for CI job summaries)")
    s.add_argument("--fail-on", choices=["none", "high", "critical"], default="none")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("locate", help="map an error message or stack trace to symbols ('-' reads stdin)")
    s.add_argument("text")
    s.add_argument("--save", action="store_true")
    s.set_defaults(fn=cmd_locate)

    s = sub.add_parser("report", help="render Markdown from stdin as an HTML report page")
    s.add_argument("--kind", choices=["impact", "debug", "changes"], required=True)
    s.add_argument("--title", required=True)
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("publish", help="regenerate the HTML pages from the existing graph")
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("web", help="open the interactive explorer (graph view, search, code, flows, AI chat)")
    s.add_argument("--port", type=int, default=4848)
    s.add_argument("--open", action="store_true", help="open the browser")
    s.add_argument("--project", help="project to show first")
    s.add_argument("--no-index", action="store_true", help="do not refresh the graph on start")
    s.set_defaults(fn=cmd_web)

    s = sub.add_parser("projects", help="manage the projects the explorer can browse")
    s.add_argument("action", choices=["list", "add", "remove"])
    s.add_argument("path", nargs="?", help="folder to add, or the project name to remove")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_projects)

    s = sub.add_parser("serve", help="run the MCP server on stdio")
    s.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130
