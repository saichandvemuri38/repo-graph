"""Markdown views of query results. Shapes follow .claude/prompts/risk-rubric.md and debug-playbook.md."""

from __future__ import annotations

from .queries import LEVEL_LABEL, is_test_id


def short(sid: str) -> str:
    """`pkg/a.py::Cls.method` -> `Cls.method`."""
    return sid.split("::", 1)[-1].replace("<module>", "(module)")


def loc(sym: dict) -> str:
    return f"{sym['file']}:{sym['start']}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_none_\n"
    esc = lambda v: str(v).replace("|", "¦")
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def mermaid(edges: list[tuple[str, str, str]], highlight: set[str] | None = None, direction: str = "LR", limit: int = 25) -> str:
    """Flowchart source. Node ids are n1, n2, ... and labels are short symbol names (see html-generation-rules.md)."""
    ids: dict[str, str] = {}
    for a, b, _ in edges:
        for x in (a, b):
            if x not in ids and len(ids) < limit:
                ids[x] = f"n{len(ids) + 1}"
    lines = [f"flowchart {direction}"]
    for sid, nid in ids.items():
        label = short(sid).replace('"', "'").replace("<", "").replace(">", "")
        lines.append(f'  {nid}["{label}"]')
    for a, b, conf in edges:
        if a in ids and b in ids:
            lines.append(f"  {ids[a]} {'-.->' if conf == 'low' else '-->'} {ids[b]}")
    for sid in (highlight or ()):
        if sid in ids:
            lines.append(f"  style {ids[sid]} fill:#fde68a,stroke:#b45309")
    dropped = len({x for a, b, _ in edges for x in (a, b)}) - len(ids)
    text = "\n".join(lines)
    return text + (f"\n%% +{dropped} more not shown" if dropped > 0 else "")


def _fence(diagram: str) -> str:
    return f"```mermaid\n{diagram}\n```\n"


def freshness_line(fresh: bool, file: str) -> str:
    return "Graph freshness: current" if fresh else f"Graph freshness: **stale for {file}** — run `codeatlas index` (source is authoritative)"


# ------------------------------------------------------------------------------------ status / search


def md_status(s: dict) -> str:
    m = s["meta"]
    out = ["# CodeAtlas status", ""]
    if not m or m.get("fileCount", "0") == "0":
        return "# CodeAtlas status\n\nGraph is empty. Run `codeatlas index` first.\n"
    out += [f"- Files: **{m.get('fileCount')}** ({', '.join(f'{l} {n}' for l, n in s['languages'])})",
            f"- Symbols: **{m.get('symbolCount')}**, edges: **{m.get('edgeCount')}**, unresolved: **{m.get('unresolvedCount')}**",
            f"- Last full index: {m.get('lastFullIndex', 'never')} | last link: {m.get('lastLink', 'never')}",
            f"- Clustering: {m.get('clusterAlgo', '?')}",
            f"- Parsers available: {', '.join(s['usable_languages'])}", ""]
    skipped = int(m.get("skippedCount", 0) or 0)
    if skipped:
        out += [f"- Skipped files: **{skipped}** (too large, binary, or no parser; see the explorer status bar)", ""]
    if s["parse_errors"]:
        out += ["## Files with parse errors (symbols recovered by a tolerant scan, no call edges)", ""]
        out += [f"- `{p}` — {e}" for p, e in s["parse_errors"][:10]]
        out.append("")
    if s["clusters"]:
        out += ["## Clusters", "", _table(["id", "label", "size", "cohesion"], [[c["id"], c["label"], c["size"], c["cohesion"]] for c in s["clusters"]])]
    if s["processes"]:
        out += ["## Flows", "", _table(["id", "name", "steps"], [[p["id"], p["name"], p["steps"]] for p in s["processes"]])]
    return "\n".join(out)


def md_search(text: str, rows: list[dict]) -> str:
    if not rows:
        return f"# Search: {text}\n\nNo symbols matched.\n"
    return f"# Search: {text}\n\n" + _table(
        ["symbol", "kind", "location", "callers", "summary"],
        [[short(r["id"]), r["kind"], f"{r['file']}:{r['start']}", r["callers"], r["summary"] or r["signature"][:70]] for r in rows])


def md_candidates(title: str, rows: list[dict], hint: str = "Pass the full id to disambiguate.") -> str:
    body = _table(["id", "kind", "location"], [[r["id"], r["kind"], f"{r['file']}:{r['start']}"] for r in rows])
    return f"# {title}\n\nNo single match. {hint}\n\n{body}"


# ------------------------------------------------------------------------------------ context


def md_context(c: dict) -> str:
    s = c["symbol"]
    out = [f"# Context: {s['id']}", freshness_line(c["fresh"], s["file"]), "",
           f"**{s['kind']}** at `{loc(s)}` (lines {s['start']}–{s['end']})", "", f"`{s['signature']}`", ""]
    if s["summary"]:
        out += [s["summary"], ""]
    if c["cluster"]:
        out.append(f"Cluster: **{c['cluster']['label']}** ({c['cluster']['id']})")
    if c["processes"]:
        out.append("Flows: " + ", ".join(f"{p['name']} (step {p['ord']})" for p in c["processes"]))
    out += ["", f"## Callers ({len(c['callers'])})", "",
            _table(["caller", "edge", "line", "confidence"], [[short(e["src"]), e["type"], e["line"], e["conf"]] for e in c["callers"]]),
            f"## Callees ({len(c['callees'])})", "",
            _table(["callee", "edge", "line", "confidence"], [[short(e["dst"]), e["type"], e["line"], e["conf"]] for e in c["callees"]])]
    if c["unresolved"]:
        out += ["## Calls the graph could not resolve", "", ", ".join(f"`{e['dst'][1:]}` (line {e['line']})" for e in c["unresolved"]), ""]
    if c["children"]:
        out += ["## Defines", "", ", ".join(f"`{short(x['id'])}`" for x in c["children"]), ""]
    if c["imported_by"] and s["kind"] == "module" or s["qname"].count(".") == 0 and c["imported_by"]:
        out += [f"## File imported by ({len(c['imported_by'])})", "", ", ".join(f"`{f}`" for f in c["imported_by"][:15]), ""]
    edges = [(e["src"], s["id"], e["conf"]) for e in c["callers"]] + [(s["id"], e["dst"], e["conf"]) for e in c["callees"]]
    if edges:
        out += ["## Diagram", "", _fence(mermaid(edges, {s["id"]}))]
    if c["source"]:
        out += ["## Source", "", "```", c["source"], "```", ""]
    return "\n".join(out)


# ------------------------------------------------------------------------------------ impact


def md_impact(r: dict) -> str:
    if "error" in r:
        return md_candidates(f"Impact: {r['target']}", r["candidates"]) if r["candidates"] else f"# Impact: {r['target']}\n\nNo symbol or file matched.\n"
    syms = r["symbols"]
    warn = f"> **WARNING: {r['risk']} risk. Review the dependants before editing.**\n\n" if r["risk"] in ("HIGH", "CRITICAL") else ""
    reason = f"{r['affected']} affected symbol(s), {len(r['processes'])} flow(s)" + (", target is a flow entry point" if r["is_entry"] else "")
    out = [warn + f"# Impact ({r['direction']}): {r['target']}", f"Risk: **{r['risk']}** — {reason}",
           freshness_line(r["fresh"], r["file"]), ""]
    if r["kind"] == "file":
        out += [f"Analyzed all {len(r['seeds'])} symbols in `{r['file']}`.", ""]
    edges = []
    for depth, level in r["levels"].items():
        rows = []
        for sid, v in level.items():
            if v["conf"] == "low":
                continue
            sym = syms.get(sid)
            rows.append([short(sid), loc(sym) if sym else "?", f"{v['type']} → {short(v['via'])} (line {v['line']})", v["conf"]])
            edges.append((sid, v["via"], v["conf"]))
        note = f" (truncated: {r['truncated'][depth]} found, showing 40)" if depth in r["truncated"] else ""
        out += [f"## {LEVEL_LABEL[depth]} — depth {depth}{note}", "", _table(["symbol", "location", "edge", "confidence"], rows)]
    if r["possible"]:
        out += ["## Possible (low confidence)", "", _table(["symbol", "edge", "line"], [[short(k), v["type"], v["line"]] for k, v in r["possible"].items()])]
        edges += [(k, v["via"], "low") for k, v in r["possible"].items()]
    if r["unresolved_matches"]:
        out += ["## Unresolved calls with a matching name (possible dependants)", "",
                _table(["caller", "written as", "line"], [[short(m["src"]), m["name"], m["line"]] for m in r["unresolved_matches"][:15]])]
    out += ["## Files that import this file", "", ", ".join(f"`{f}`" for f in r["importing_files"]) or "_none_", "",
            "## Flows touched", "", _table(["flow", "name"], [[p["id"], p["name"]] for p in r["processes"]])]
    if r["tests"]:
        out += [f"## Tests that exercise this ({len(r['tests'])})", "", "_Not counted in the risk score. Run these after the change._", "",
                _table(["test", "location", "distance"], [[short(k), loc(syms[k]) if k in syms else "?", f"depth {v['depth']}"] for k, v in sorted(r["tests"].items(), key=lambda kv: (kv[1]["depth"], kv[0]))[:25]])]
    out += ["## Suggested checks", ""]
    direct = [short(s) for s, v in r["levels"].get(1, {}).items() if v["conf"] != "low"][:4]
    checks = [f"Re-read and re-test `{d}`, which calls or extends the target directly." for d in direct]
    checks.append("Confirm the signature and return value still satisfy every direct caller.")
    if r["tests"]:
        checks.append(f"Run the {len(r['tests'])} test(s) listed above.")
    if r["processes"]:
        checks.append(f"Run the code path of flow `{r['processes'][0]['name']}` end to end.")
    if r["unresolved_matches"] or r["possible"]:
        checks.append("Grep for dynamic use (getattr, string dispatch, decorators, tests); the graph cannot see it.")
    out += [f"- {c}" for c in checks] + [""]
    if edges:
        out += ["## Diagram", "", _fence(mermaid(edges, set(r["seeds"][:1]), "RL"))]
    return "\n".join(out)


# ------------------------------------------------------------------------------------ trace


def md_trace(r: dict) -> str:
    if "error" in r:
        parts = ["# Trace", "", "Could not pick a single symbol at each end."]
        for side, key in (("from", "from"), ("to", "to")):
            if r[f"{key}"]:
                parts += ["", f"**{side}:**", _table(["id", "kind", "location"], [[x["id"], x["kind"], f"{x['file']}:{x['start']}"] for x in r[key]])]
        return "\n".join(parts)
    syms = r["symbols"]
    title = f"# Path: {short(r['start'])} → {short(r['goal'])}"
    if not r["path"]:
        out = [title, "", "No call path found within 8 hops.", ""]
        if r.get("reachable"):
            out += ["Nearest symbols reachable from the start:", ", ".join(f"`{short(s)}`" for s in r["reachable"]), ""]
        if r.get("unresolved_from_start"):
            out += ["Calls from the start the graph could not resolve (the missing link may be here):",
                    ", ".join(f"`{u['name']}` (line {u['line']})" for u in r["unresolved_from_start"]), ""]
        return "\n".join(out)
    rows = [[i, short(sid), loc(syms[sid]), f"call site line {line}" if line else "start", conf] for i, (sid, line, conf) in enumerate(r["path"])]
    edges = [(r["path"][i][0], r["path"][i + 1][0], r["path"][i + 1][2]) for i in range(len(r["path"]) - 1)]
    note = "\n_Uses low-confidence edges; verify each hop in the source._\n" if r["low"] else ""
    return "\n".join([title, note, _table(["#", "symbol", "location", "reached by", "confidence"], rows), "## Diagram", "", _fence(mermaid(edges, {r["start"], r["goal"]}))])


# ------------------------------------------------------------------------------------ changes


def md_changes(c: dict) -> str:
    warn = f"> **WARNING: {c['risk']} risk.**\n\n" if c["risk"] in ("HIGH", "CRITICAL") else ""
    syms = c["symbols"]
    out = [warn + f"# Changes vs {c['baseline']}", f"Overall risk: **{c['risk']}** — {len(c['modified'])} modified, {len(c['removed'])} removed, "
           f"{len(c['added'])} added symbol(s); {c['affected']} dependant(s); {len(c['processes'])} flow(s) touched", ""]
    if not c["git"]:
        out += ["_No git repository: compared with the last index. Initialise git for a commit baseline._", ""]
    if not c["files"]:
        return "\n".join(out + ["No source files changed.\n"])
    out += ["## Files", "", _table(["file", "status"], [[f, {"A": "added", "M": "modified", "D": "removed"}.get(s, s)] for f, s in sorted(c["files"].items())])]
    rows = []
    for kind, ids in (("modified", c["modified"]), ("removed", c["removed"]), ("added", c["added"])):
        for sid in ids:
            deps = c["dependants"].get(sid, [])
            rows.append([short(sid), kind, ", ".join(sorted({short(d["src"]) for d in deps})[:5]) or "—"])
    out += ["## Symbols changed", "", _table(["symbol", "change", "direct dependants"], rows)]
    broken = [s for s in c["removed"] if c["dependants"].get(s)]
    if broken:
        out += ["## Removed symbols that are still referenced", "", ", ".join(f"`{short(s)}`" for s in broken), ""]
    if c["tests"]:
        out += [f"## Tests that exercise the changed symbols ({len(c['tests'])})", "", "_Run these before committing._", "",
                ", ".join(f"`{short(t)}`" for t in c["tests"][:25]), ""]
    out += ["## Flows touched", "", _table(["flow", "name"], [[p["id"], p["name"]] for p in c["processes"]])]
    edges = [(d["src"], sid, d["conf"]) for sid, ds in c["dependants"].items() for d in ds if not is_test_id(d["src"])]
    if edges:
        out += ["## Diagram", "", _fence(mermaid(edges, set(c["modified"]) | set(c["removed"]), "RL"))]
    out += ["## Recommended next step", "", "Re-run the tests for the flows above, then `codeatlas index` if you have not.", ""]
    return "\n".join(out)


# ------------------------------------------------------------------------------------ locate


def md_locate(r: dict) -> str:
    out = [f"# Error location", f"Message: `{r['message']}`" if r["message"] else "", ""]
    if r["frames"]:
        out += ["## Stack frames mapped to symbols", "", _table(["file", "line", "symbol"], [[f["file"], f["line"], short(f["symbol"]) if f["symbol"] else "—"] for f in r["frames"]])]
    if r["raise_sites"]:
        out += ["## Where the message text appears in source", "", _table(["file", "line", "symbol", "text"],
               [[s["file"], s["line"], short(s["symbol"]) if s["symbol"] else "—", s["text"]] for s in r["raise_sites"]])]
    if r["search_hits"]:
        out += ["## Symbols matching the text", "", _table(["symbol", "location"], [[short(h["id"]), f"{h['file']}:{h['start']}"] for h in r["search_hits"]])]
    f = r["focus"]
    if f:
        out += [f"## Focus: {short(f['symbol'])}", "", "Callers (2 levels up):", ""]
        for depth, level in f["upstream"].items():
            out.append(f"- depth {depth}: " + ", ".join(f"`{short(s)}`" for s in level))
        down = [s for level in f["downstream"].values() for s in level]
        out += ["", "Callees: " + (", ".join(f"`{short(s)}`" for s in down) or "none"), "", "```", f["context"]["source"], "```", ""]
        edges = [(s, v["via"], v["conf"]) for lv in f["upstream"].values() for s, v in lv.items()] + [(v["via"], s, v["conf"]) for lv in f["downstream"].values() for s, v in lv.items()]
        if edges:
            out += ["## Diagram", "", _fence(mermaid(edges, {f["symbol"]}))]
    elif not (r["frames"] or r["raise_sites"] or r["search_hits"]):
        out.append("Nothing in the graph matched. Try `codeatlas search` with key words from the symptom.")
    return "\n".join(out)
