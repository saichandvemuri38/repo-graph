"""Functional clusters (Louvain) and execution flows (entry point -> forward call trace).

Implements .claude/prompts/clustering-rules.md. Louvain needs networkx; without it we fall back to
one cluster per file, merged by directory, and say so in meta (`clusterAlgo`).
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict

from .store import Store

ENTRY_NAMES = {"main", "run", "handler", "cli", "start", "serve", "app"}
ROUTE_HINT = re.compile(r"(^|\.)(route|get|post|put|delete|patch|command|task|fixture|listener|on|handler|hookimpl|main)$")
GENERIC_WORDS = {"self", "get", "set", "init", "new", "the", "run", "main", "test", "util", "utils", "helper", "helpers"}
MAX_PROCESSES = 40
MAX_DEPTH = 8
MAX_BRANCH = 3
MIN_CLUSTER = 3


def compute(store: Store) -> str:
    db = store.db
    syms = {r["id"]: r for r in db.execute("SELECT * FROM symbols")}
    edges = [(r["type"], r["src"], r["dst"], r["conf"]) for r in db.execute(
        "SELECT type, src, dst, conf FROM edges WHERE type IN ('CALLS','REFERENCES','EXTENDS') AND dst NOT LIKE '?%'")]
    db.execute("DELETE FROM clusters")
    db.execute("DELETE FROM cluster_members")
    db.execute("DELETE FROM processes")
    db.execute("DELETE FROM process_steps")

    connected = {s for _, s, d, _ in edges} | {d for _, s, d, _ in edges}
    nodes = sorted(i for i, r in syms.items() if r["kind"] != "module" or i in connected)
    assign, algo = _communities(nodes, syms, edges)
    _write_clusters(store, syms, edges, assign)
    _write_processes(store, syms, edges, assign)
    return algo


# ------------------------------------------------------------------------------------ clusters


def _communities(nodes: list[str], syms, edges) -> tuple[dict[str, int], str]:
    node_set = set(nodes)
    try:
        import networkx as nx
    except ImportError:
        return _by_file(nodes, syms), "by-file (install networkx for Louvain)"
    g = nx.Graph()
    g.add_nodes_from(nodes)
    for _type, src, dst, conf in edges:
        if src in node_set and dst in node_set and src != dst:
            w = 1.0 if conf != "low" else 0.5
            g.add_edge(src, dst, weight=g.get_edge_data(src, dst, {}).get("weight", 0.0) + w)
    # A pseudo-node per file, weakly tied to its symbols, so isolated symbols land with their file-mates.
    for i in nodes:
        anchor = f"file::{syms[i]['file']}"
        g.add_edge(i, anchor, weight=0.6)
    if g.number_of_edges() == 0:
        return _by_file(nodes, syms), "by-file (no edges)"
    parts = nx.community.louvain_communities(g, weight="weight", seed=42)
    assign: dict[str, int] = {}
    for idx, members in enumerate(sorted(parts, key=lambda p: (-len(p), sorted(p)[0]))):
        for m in members:
            if m in node_set:
                assign[m] = idx
    return _merge_small(assign, syms, edges), "louvain"


def _by_file(nodes: list[str], syms) -> dict[str, int]:
    files = {f: i for i, f in enumerate(sorted({syms[n]["file"] for n in nodes}))}
    return {n: files[syms[n]["file"]] for n in nodes}


def _merge_small(assign: dict[str, int], syms, edges) -> dict[str, int]:
    """Fold clusters with fewer than MIN_CLUSTER symbols into the neighbour they connect to most (linear-time bookkeeping)."""
    members: dict[int, set[str]] = defaultdict(set)
    for n, c in assign.items():
        members[c].add(n)
    adj: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for _t, s, d, _c in edges:
        if s in assign and d in assign and assign[s] != assign[d]:
            adj[assign[s]][assign[d]] += 1
            adj[assign[d]][assign[s]] += 1
    file_dir: dict[str, str] = {}
    dirs_of: dict[int, set[str]] = {}
    for c, ns in members.items():
        ds = set()
        for n in ns:
            f = syms[n]["file"]
            if f not in file_dir:
                file_dir[f] = posixpath.dirname(f)
            ds.add(file_dir[f])
        dirs_of[c] = ds
    for c in sorted(members, key=lambda k: len(members[k])):
        if c not in members or len(members[c]) >= MIN_CLUSTER:
            continue
        options = {b: w for b, w in adj[c].items() if b in members and b != c}
        if not options:
            options = {b: 0.1 for b, ds in dirs_of.items() if b != c and b in members and dirs_of[c] & ds}
        if not options:
            continue
        target = max(sorted(options), key=lambda b: options[b])
        for n in members[c]:
            assign[n] = target
        members[target] |= members.pop(c)
        dirs_of[target] |= dirs_of.pop(c)
        for b, w in adj.pop(c, {}).items():                 # re-point the small cluster's links at the target
            adj[b].pop(c, None)
            if b != target:
                adj[target][b] += w
                adj[b][target] += w
    return assign


def _label(ids: list[str], syms) -> str:
    areas = Counter()
    tokens = Counter()
    for i in ids:
        r = syms[i]
        parts = r["file"].split("/")
        areas[parts[-2] if len(parts) > 1 else posixpath.splitext(parts[-1])[0]] += 1
        for tok in re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", r["name"]).replace("_", " ").lower().split():
            if len(tok) > 2 and tok not in GENERIC_WORDS and not tok.startswith("<"):
                tokens[tok] += 1
    area = areas.most_common(1)[0][0] if areas else "misc"
    top = [t for t, _ in sorted(tokens.items(), key=lambda kv: (-kv[1], kv[0]))[:2]]
    return f"{area} · {' '.join(top)}" if top else area


def _write_clusters(store: Store, syms, edges, assign: dict[str, int]) -> None:
    groups: dict[int, list[str]] = defaultdict(list)
    for n, c in assign.items():
        groups[c].append(n)
    ordered = sorted(groups.values(), key=lambda ids: (-len(ids), sorted(ids)[0]))
    cid_of = {n: f"c{i}" for i, ids in enumerate(ordered, 1) for n in ids}
    internal: Counter = Counter()
    external: Counter = Counter()
    for _t, s, d, _c in edges:                              # one pass: which cluster does each edge stay inside?
        a, b = cid_of.get(s), cid_of.get(d)
        if a and a == b:
            internal[a] += 1
        else:
            if a:
                external[a] += 1
            if b:
                external[b] += 1
    for idx, ids in enumerate(ordered, 1):
        cid = f"c{idx}"
        total = internal[cid] + external[cid]
        share = internal[cid] / total if total else 0.0
        cohesion = "high" if share > 0.7 else "med" if share >= 0.4 else "low"
        files = sorted({syms[i]["file"] for i in ids})
        summary = f"{len(ids)} symbols in {len(files)} file(s): {', '.join(files[:3])}{'…' if len(files) > 3 else ''}"[:140]
        store.db.execute("INSERT INTO clusters(id, label, size, cohesion, summary) VALUES (?,?,?,?,?)",
                         (cid, _label(ids, syms), len(ids), cohesion, summary))
        store.db.executemany("INSERT INTO cluster_members(cluster_id, symbol_id) VALUES (?,?)", [(cid, i) for i in sorted(ids)])


# ------------------------------------------------------------------------------------ processes


def _write_processes(store: Store, syms, edges, assign: dict[str, int]) -> None:
    calls = defaultdict(list)          # src -> [dst] (CALLS, not low)
    incoming = Counter()
    for t, s, d, conf in edges:
        if t in ("CALLS", "REFERENCES"):
            incoming[d] += 1
        if t == "CALLS" and conf != "low":
            calls[s].append(d)
    outdeg = {n: len(set(v)) for n, v in calls.items()}
    routed = {r["src"] for r in store.db.execute("SELECT src, target FROM raw_edges WHERE type='REFERENCES'")
              if ROUTE_HINT.search(r["target"])}

    entries = []
    for i, r in sorted(syms.items()):
        if r["kind"] == "module":
            if outdeg.get(i):
                entries.append(i)
        elif i in routed or r["name"] in ENTRY_NAMES or r["name"].startswith("test_"):
            entries.append(i)
        elif r["kind"] == "function" and "." not in r["qname"] and not r["name"].startswith("_") and incoming[i] == 0 and outdeg.get(i):
            entries.append(i)

    flows = []
    for entry in entries:
        steps, seen, frontier = [entry], {entry}, [entry]
        for _depth in range(MAX_DEPTH):
            nxt = []
            for node in frontier:
                callees = sorted({d for d in calls.get(node, []) if d in syms and d not in seen},
                                 key=lambda d: (-outdeg.get(d, 0), d))[:MAX_BRANCH]
                for d in callees:
                    seen.add(d)
                    steps.append(d)
                    nxt.append(d)
            frontier = nxt
            if not frontier:
                break
        if len(steps) >= 3:
            crossed = len({assign.get(s) for s in steps if s in assign})
            is_test = syms[entry]["name"].startswith("test_")
            flows.append((len(steps) * 10 + crossed - (1000 if is_test else 0), entry, steps))   # tests rank after real flows
    flows.sort(key=lambda f: (-f[0], f[1]))
    kept: list[tuple[int, str, list[str]]] = []
    kept_sets: list[set[str]] = []
    owners: dict[str, list[int]] = defaultdict(list)          # step -> indexes of kept flows containing it
    for flow in flows:
        # A flow whose steps are all inside a bigger flow is the same path seen from a later entry point.
        steps = set(flow[2])
        candidates = set.intersection(*(set(owners[s]) for s in steps)) if steps else set()
        if candidates:
            continue
        kept.append(flow)
        kept_sets.append(steps)
        for s in steps:
            owners[s].append(len(kept) - 1)
        if len(kept) >= MAX_PROCESSES:
            break
    for idx, (_score, entry, steps) in enumerate(kept[:MAX_PROCESSES], 1):
        pid = f"p{idx}"
        first, last = syms[entry]["qname"], syms[steps[-1]]["qname"]
        clusters = len({assign.get(s) for s in steps if s in assign})
        kind = "test" if syms[entry]["name"].startswith("test_") else "flow"
        name = f"{first} → {last}" if first != "<module>" else f"{syms[entry]['file']} → {last}"
        store.db.execute(
            "INSERT INTO processes(id, name, entry, steps, summary) VALUES (?,?,?,?,?)",
            (pid, name, entry, len(steps), f"{kind}: {len(steps)} steps across {clusters} cluster(s)"),
        )
        store.db.executemany("INSERT INTO process_steps(process_id, ord, symbol_id) VALUES (?,?,?)",
                             [(pid, n, s) for n, s in enumerate(steps, 1)])
