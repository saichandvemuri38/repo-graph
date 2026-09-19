"""Graph-based search: find code by concept, using the call graph to reach what the words do not.

Keyword search finds symbols whose name, docstring or path contain the query words. That misses the function
that does the work but never says the word (`hash_password` for “authentication”), and it cannot tell a helper
from the code that matters. This search works in three steps:

1. Seeds: full-text matches (BM25) on names, docstrings, signatures and paths, with camelCase/snake_case splitting.
2. Spread: a personalised PageRank walk over the graph starts at the seeds and flows along calls, references,
   inheritance and containment (strong edges carry more than guessed ones, hubs give each neighbour less).
3. Blend: score = seed strength + graph closeness, with a small bonus for sitting in the same cluster as other hits.

Every result says why it was returned (“matches name”, “called by login”, ...). No embeddings, no network.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict

from .store import name_tokens

STOP = {"the", "a", "an", "of", "to", "in", "for", "and", "or", "is", "are", "how", "does", "do", "what", "where", "which",
        "that", "this", "with", "on", "by", "from", "code", "where", "when", "why", "who", "it", "be", "can", "all"}
EDGE_TYPES = ("CALLS", "REFERENCES", "EXTENDS", "DEFINES")
CONF_WEIGHT = {"high": 1.0, "med": 0.7, "low": 0.3}
TYPE_WEIGHT = {"CALLS": 1.0, "REFERENCES": 0.7, "EXTENDS": 0.9, "DEFINES": 0.8}
MAX_NODES = 1500
PER_NODE = 40
RESTART = 0.35
ITERATIONS = 25
SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "er", "ed", "es", "s")


def query_terms(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", name_tokens(text)) if len(w) > 1 and w not in STOP]
    out: list[str] = []
    for w in words:
        for candidate in (w, _stem(w)):
            if candidate and candidate not in out:
                out.append(candidate)
    return out


def _stem(word: str) -> str:
    """Crude suffix strip so `authenticating` reaches `authenticate` through a prefix match."""
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def graph_search(atlas, text: str, limit: int = 15) -> dict:
    db = atlas.db
    terms = query_terms(text)
    wants_tests = bool({"test", "tests", "spec"} & set(terms))
    if not terms:
        return {"query": text, "terms": [], "results": [], "areas": []}
    from .queries import is_test_id

    rows = [r for r in atlas.store.fts(" ".join(terms), 40) if r["kind"] != "module"]
    if not rows:
        return {"query": text, "terms": terms, "results": [], "areas": []}
    best, worst = min(r["score"] for r in rows), max(r["score"] for r in rows)
    span = (worst - best) or 1.0
    seed: dict[str, float] = {r["id"]: 0.25 + 0.75 * (worst - r["score"]) / span for r in rows}
    info = {r["id"]: dict(r) for r in rows}

    # ---- adjacency around the seeds (two hops, capped)
    marks = ",".join("?" * len(EDGE_TYPES))
    adj: dict[str, dict[str, tuple[float, str, str]]] = defaultdict(dict)   # node -> {other: (weight, type, direction)}

    def link(node: str) -> None:
        if node in adj:
            return
        adj[node] = {}
        out = db.execute(f"SELECT dst AS o, type, conf FROM edges WHERE src=? AND type IN ({marks}) AND dst NOT LIKE '?%' AND dst NOT LIKE 'ext:%' LIMIT ?", (node, *EDGE_TYPES, PER_NODE))
        inc = db.execute(f"SELECT src AS o, type, conf FROM edges WHERE dst=? AND type IN ({marks}) LIMIT ?", (node, *EDGE_TYPES, PER_NODE))
        for direction, cursor in (("out", out), ("in", inc)):
            for r in cursor:
                w = TYPE_WEIGHT[r["type"]] * CONF_WEIGHT.get(r["conf"], 0.5)
                if w > adj[node].get(r["o"], (0,))[0]:
                    adj[node][r["o"]] = (w, r["type"], direction)

    frontier = list(seed)
    for _ in range(2):
        nxt: list[str] = []
        for node in frontier:
            if len(adj) >= MAX_NODES:
                break
            link(node)
            nxt += list(adj[node])
        frontier = [n for n in dict.fromkeys(nxt) if n not in adj]
    # make links symmetric so the walk can go both ways
    for node, nbrs in list(adj.items()):
        for other, (w, t, d) in nbrs.items():
            if other in adj and node not in adj[other]:
                adj[other][node] = (w, t, "in" if d == "out" else "out")
    nodes = set(adj) | set(seed)
    for n in nodes:
        adj.setdefault(n, {})

    # ---- personalised PageRank
    total = sum(seed.values())
    restart = {n: seed.get(n, 0.0) / total for n in nodes}
    rank = dict(restart)
    out_sum = {n: sum(w for w, _, _ in adj[n].values() if True) for n in nodes}
    for _ in range(ITERATIONS):
        nxt = {n: RESTART * restart[n] for n in nodes}
        for n in nodes:
            mass = (1 - RESTART) * rank[n]
            if not mass:
                continue
            if out_sum[n] <= 0:
                nxt[n] += mass                      # nowhere to go: keep it
                continue
            for other, (w, _, _) in adj[n].items():
                if other in nxt:
                    nxt[other] += mass * w / out_sum[n]
        rank = nxt
    top_rank = max(rank.values()) or 1.0

    # ---- metadata for candidates
    ids = sorted(nodes, key=lambda n: -(0.55 * seed.get(n, 0) + 0.45 * rank[n] / top_rank))[: max(limit * 4, 60)]
    meta: dict[str, dict] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        q = ",".join("?" * len(chunk))
        for r in db.execute(f"SELECT * FROM symbols WHERE id IN ({q})", chunk):
            meta[r["id"]] = dict(r)
    clusters: dict[str, tuple[str, str]] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        q = ",".join("?" * len(chunk))
        for r in db.execute(f"SELECT m.symbol_id, c.id, c.label FROM cluster_members m JOIN clusters c ON c.id=m.cluster_id WHERE m.symbol_id IN ({q})", chunk):
            clusters[r["symbol_id"]] = (r["id"], r["label"])
    seeds_per_cluster: dict[str, int] = defaultdict(int)
    for sid in seed:
        if sid in clusters:
            seeds_per_cluster[clusters[sid][0]] += 1

    def name_of(sid: str) -> str:
        m = meta.get(sid) or info.get(sid)
        return m["qname"] if m else sid.split("::", 1)[-1]

    def why(sid: str) -> str:
        m = meta.get(sid) or info[sid]
        if sid in seed:
            hay_name = name_tokens(m["name"], m["qname"])
            if any(t in hay_name for t in terms):
                return "matches the name"
            if any(t in name_tokens(m["file"]) for t in terms):
                return "matches the file path"
            return "matches the description"
        best_o, best_v = None, 0.0
        for other, (w, t, d) in adj[sid].items():
            v = w * (seed.get(other, 0.0) or 0.0)
            if v > best_v:
                best_o, best_v, best_t, best_d = other, v, t, d
        if best_o:
            verb = {("CALLS", "out"): "calls", ("CALLS", "in"): "is called by", ("REFERENCES", "out"): "uses",
                    ("REFERENCES", "in"): "is used by", ("EXTENDS", "out"): "extends", ("EXTENDS", "in"): "is extended by",
                    ("DEFINES", "out"): "defines", ("DEFINES", "in"): "is defined in"}[(best_t, best_d)]
            return f"{verb} {name_of(best_o)}"
        return "two steps from a match"

    results = []
    for sid in ids:
        m = meta.get(sid)
        if not m or m["kind"] == "module" or (is_test_id(sid) and not wants_tests):
            continue
        score = 0.55 * seed.get(sid, 0.0) + 0.45 * rank[sid] / top_rank
        cluster = clusters.get(sid)
        if cluster and seeds_per_cluster.get(cluster[0], 0) >= 2:
            score *= 1 + 0.06 * min(seeds_per_cluster[cluster[0]] - 1, 4)
        results.append({
            "id": sid, "kind": m["kind"], "file": m["file"], "start": m["start"], "end": m["end"], "name": m["name"],
            "qname": m["qname"], "summary": m["summary"], "signature": m["signature"], "score": round(score, 4),
            "direct": sid in seed, "why": why(sid), "cluster": cluster[1] if cluster else "", "cluster_id": cluster[0] if cluster else "",
        })
    results.sort(key=lambda r: -r["score"])
    # Keep a third of the list for connected code: without this, forty keyword hits would crowd out everything the graph adds.
    keep = max(1, math.ceil(limit * 0.65))
    head = results[:keep]
    extra = [r for r in results[keep:] if not r["direct"]][: limit - len(head)]
    rest = [r for r in results[keep:] if r["direct"]][: max(0, limit - len(head) - len(extra))]
    results = sorted([*head, *extra, *rest], key=lambda r: -r["score"])
    for r in results:
        r["callers"] = db.execute("SELECT COUNT(*) FROM edges WHERE dst=? AND type IN ('CALLS','REFERENCES')", (r["id"],)).fetchone()[0]
    areas: dict[str, int] = defaultdict(int)
    for r in results:
        if r["cluster"]:
            areas[r["cluster"]] += 1
    return {"query": text, "terms": terms, "results": results,
            "areas": sorted(areas.items(), key=lambda kv: -kv[1])[:5]}
