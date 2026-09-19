"""Nexus-style AI chat: a tool-use loop where Claude answers questions using the code graph.

Runs on the server so the API key never reaches the browser. The key comes from $ANTHROPIC_API_KEY or from
~/.codeatlas/config.json (written with owner-only permissions by the Settings panel). Standard library only.
Set $ANTHROPIC_BASE_URL to point at a proxy or a test double.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable

from . import reports
from .projects import home_dir
from .webapi import WebAPI

DEFAULT_MODEL = "claude-sonnet-5"
MODELS = ["claude-sonnet-5", "claude-opus-5", "claude-fable-5-1", "claude-haiku-4-5-20251001"]
MAX_TURNS = 10
MAX_TOOL_CHARS = 7000
API_VERSION = "2023-06-01"

SYSTEM = """You are Nexus, the code-intelligence assistant inside CodeAtlas. You answer questions about ONE indexed repository
using tools that query its code knowledge graph (symbols, calls, imports, inheritance, clusters, execution flows) and its source.

How to work:
- Start with codebase_overview if you know nothing about the repo, then search_symbols, get_symbol, trace_path, impact.
- Confirm claims by reading source with read_source before you state them. The graph is an index, not the truth: edges marked
  low confidence and unresolved calls are only possible, not proven.
- Use run_sql for counting and listing questions ("most called functions", "files with parse errors"). Tables: symbols, edges, files,
  unresolved, clusters, cluster_members, processes, process_steps. edges(type, src, dst, line, conf, file).
- Cite code as `path/file.py:LINE` or `path/file.py::Qualified.name` so the user can click it.
- Be concrete and brief. If the graph cannot answer, say what is missing instead of guessing."""

TOOLS = [
    {"name": "codebase_overview", "description": "Graph statistics: files, languages, symbols, edges, clusters, top flows, files with parse errors.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "search_symbols", "description": "Find symbols, files or folders by name or concept. Syntax: words, `path/prefix`, `type:function|class|method|file|folder`.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_symbol", "description": "Definition, callers, callees, class relations, cluster, flows and source of one symbol (name, Class.method, or full id `path::Qual.name`).",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}},
    {"name": "impact", "description": "Blast radius of changing a symbol or file: dependants by depth, tests, flows touched, risk level.",
     "input_schema": {"type": "object", "properties": {"target": {"type": "string"}, "direction": {"type": "string", "enum": ["upstream", "downstream"]}}, "required": ["target"]}},
    {"name": "trace_path", "description": "Shortest call path from one symbol to another.",
     "input_schema": {"type": "object", "properties": {"from": {"type": "string"}, "to": {"type": "string"}}, "required": ["from", "to"]}},
    {"name": "read_source", "description": "Read lines of a source file (1-based, inclusive). Omit start/end for the top of the file (max 200 lines).",
     "input_schema": {"type": "object", "properties": {"file": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}, "required": ["file"]}},
    {"name": "run_sql", "description": "Read-only SQL (SELECT) over the graph database. Max 500 rows.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "locate_error", "description": "Map an error message or stack trace to symbols, with callers and callees around the most relevant one.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "changes", "description": "Symbols changed by uncommitted work (vs git HEAD, or the last index without git), with dependants and risk.",
     "input_schema": {"type": "object", "properties": {}}},
]


# ------------------------------------------------------------------------------------ settings


def _config_path():
    return home_dir() / "config.json"


def load_settings() -> dict:
    try:
        return json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(update: dict) -> None:
    cfg = load_settings()
    cfg.update({k: v for k, v in update.items() if k in ("anthropic_api_key", "model")})
    home_dir().mkdir(parents=True, exist_ok=True)
    path = _config_path()
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def public_settings() -> dict:
    cfg = load_settings()
    key = os.environ.get("ANTHROPIC_API_KEY") or cfg.get("anthropic_api_key") or ""
    return {"has_key": bool(key), "key_hint": f"…{key[-4:]}" if len(key) > 8 else "", "key_source": "env" if os.environ.get("ANTHROPIC_API_KEY") else ("config" if cfg.get("anthropic_api_key") else ""),
            "model": cfg.get("model") or DEFAULT_MODEL, "models": MODELS}


# ------------------------------------------------------------------------------------ tools


def run_tool(api: WebAPI, name: str, args: dict) -> tuple[str, list[dict]]:
    """Execute one tool. Returns (text for the model, citations found)."""
    cites: list[dict] = []
    a = api.atlas
    if name == "codebase_overview":
        text = api.overview()
    elif name == "search_symbols":
        rows = api.search(str(args.get("query", "")), 15)
        text = "\n".join(f"- {r['kind']} `{r['id']}` ({r['file']}:{r['line']})" for r in rows) or "No matches."
        cites = [{"file": r["file"], "start": r["line"], "end": r["line"], "id": r["id"]} for r in rows[:5] if r["kind"] not in ("folder",)]
    elif name == "get_symbol":
        rows, cands, kind = a.resolve_target(str(args.get("symbol", "")))
        if kind == "symbol":
            ctx = a.context(rows[0]["id"])
            text = reports.md_context(ctx)
            s = ctx["symbol"]
            cites = [{"file": s["file"], "start": s["start"], "end": s["end"], "id": s["id"]}]
        else:
            text = reports.md_candidates(f"'{args.get('symbol')}'", [dict(r) for r in cands]) if cands else "No symbol matched."
    elif name == "impact":
        r = a.impact(str(args.get("target", "")), args.get("direction") or "upstream")
        text = reports.md_impact(r)
        cites = [{"file": s["file"], "start": s["start"], "end": s["end"], "id": sid} for sid, s in list(r.get("symbols", {}).items())[:6]]
    elif name == "trace_path":
        r = a.trace(str(args.get("from", "")), str(args.get("to", "")))
        text = reports.md_trace(r)
        cites = [{"file": s["file"], "start": s["start"], "end": s["end"], "id": sid} for sid, s in list(r.get("symbols", {}).items())[:8]]
    elif name == "read_source":
        start = max(1, int(args.get("start") or 1))
        end = int(args.get("end") or start + 199)
        src = api.source(str(args.get("file", "")))
        if "error" in src:
            text = f"Error: {src['error']}"
        else:
            end = min(end, start + 399, src["total"])
            text = "\n".join(f"{n:>5}  {line}" for n, line in enumerate(src["lines"][start - 1:end], start))
            cites = [{"file": src["file"], "start": start, "end": end}]
    elif name == "run_sql":
        r = api.sql(str(args.get("query", "")))
        if "error" in r:
            text = f"Error: {r['error']}"
        else:
            body = ["| " + " | ".join(r["columns"]) + " |", "|" + "|".join("---" for _ in r["columns"]) + "|"]
            body += ["| " + " | ".join(str(v).replace("|", "¦") for v in row) + " |" for row in r["rows"][:60]]
            text = "\n".join(body) + (f"\n\n({len(r['rows'])} rows{', truncated' if r['truncated'] else ''}; showing up to 60)" if r["rows"] else "\n(no rows)")
    elif name == "locate_error":
        text = reports.md_locate(a.locate(str(args.get("text", ""))))
    elif name == "changes":
        text = reports.md_changes(a.changes(None))
    else:
        text = f"Unknown tool {name}"
    if len(text) > MAX_TOOL_CHARS:
        text = text[:MAX_TOOL_CHARS] + "\n… (truncated)"
    return text, cites


# ------------------------------------------------------------------------------------ the loop


def _post(url: str, key: str, body: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _cite_from_text(api: WebAPI, text: str) -> list[dict]:
    known = {r["path"] for r in api.db.execute("SELECT path FROM files")}
    out = []
    for m in re.finditer(r"([\w./-]+\.\w+):(\d+)(?:-(\d+))?", text):
        if m.group(1) in known:
            s = int(m.group(2))
            out.append({"file": m.group(1), "start": s, "end": int(m.group(3) or s)})
    return out


def run_chat(api: WebAPI, history: list[dict], emit: Callable[[dict], None], lock) -> None:
    """Answer the last user message. `history` is a list of {role, content} text turns. Emits NDJSON-ready events."""
    settings = load_settings()
    key = os.environ.get("ANTHROPIC_API_KEY") or settings.get("anthropic_api_key")
    if not key:
        emit({"type": "error", "code": "no_key", "message": "No API key configured. Open Settings and add an Anthropic API key, or start the server with ANTHROPIC_API_KEY set."})
        return
    model = settings.get("model") or DEFAULT_MODEL
    base = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    messages = [{"role": m["role"], "content": m["content"]} for m in history if m.get("role") in ("user", "assistant") and m.get("content")]
    if not messages or messages[-1]["role"] != "user":
        emit({"type": "error", "message": "Nothing to answer."})
        return
    system = f"{SYSTEM}\n\nProject: {api.project.name}"
    citations: list[dict] = []
    for _ in range(MAX_TURNS):
        try:
            reply = _post(f"{base}/v1/messages", key, {"model": model, "max_tokens": 4096, "system": system, "tools": TOOLS, "messages": messages})
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read()).get("error", {}).get("message", "")
            except Exception:
                pass
            emit({"type": "error", "message": f"Anthropic API error {exc.code}: {detail or exc.reason}", "code": "api_error"})
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            emit({"type": "error", "message": f"Could not reach the Anthropic API: {getattr(exc, 'reason', exc)}", "code": "network"})
            return
        blocks = reply.get("content", [])
        messages.append({"role": "assistant", "content": blocks})
        for b in blocks:
            if b.get("type") == "text" and b.get("text"):
                emit({"type": "text", "text": b["text"]})
                citations += _cite_from_text(api, b["text"])
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        if reply.get("stop_reason") != "tool_use" or not uses:
            break
        results = []
        for u in uses:
            emit({"type": "tool_start", "id": u["id"], "name": u["name"], "input": u.get("input", {})})
            try:
                with lock:
                    text, cites = run_tool(api, u["name"], u.get("input") or {})
                ok = True
            except Exception as exc:                      # a broken tool must not end the conversation
                text, cites, ok = f"Tool error: {exc}", [], False
            citations += cites
            emit({"type": "tool_result", "id": u["id"], "ok": ok, "preview": text[:1200]})
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": text, **({} if ok else {"is_error": True})})
        messages.append({"role": "user", "content": results})
    else:
        emit({"type": "text", "text": "\n\n_(Stopped after the maximum number of tool steps. Ask a narrower question to continue.)_"})
    seen, unique = set(), []
    for c in citations:
        key_ = (c["file"], c["start"], c["end"])
        if key_ not in seen:
            seen.add(key_)
            unique.append(c)
    emit({"type": "done", "citations": unique[:30]})
