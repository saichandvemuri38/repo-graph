// Answers every request the explorer makes from the data embedded in this HTML file, so the report needs no server.
// The data is written by static_report.py as gzip + base64. Impact, trace, search and node details are worked out here,
// in the browser, from the embedded edges.

let DATA = null;
const ready = (async () => {
  if (typeof DecompressionStream === "undefined") throw new Error("This browser cannot open the report (it needs DecompressionStream: Chrome 80+, Edge 80+, Firefox 113+, Safari 16.4+).");
  const bin = atob(document.getElementById("atlas-data").textContent.trim());
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const text = await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
  DATA = JSON.parse(text);
})();
const DEP = new Set(["CALLS", "REFERENCES", "EXTENDS"]);
const RANK = { high: 3, med: 2, low: 1 };
const MAX_LEVEL = 40;
const TEST_RE = /(^|\/)(tests?|__tests__|spec)\/|(^|\/)test_[^/]*$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$|Tests?\.java$/;

const isTest = (id) => TEST_RE.test(String(id).split("::")[0]);
const minConf = (a, b) => (RANK[a] <= RANK[b] ? a : b);
const short = (id) => String(id).split("::").pop();
const words = (s) => String(s).replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_.:/#\-]+/g, " ").toLowerCase().split(/\s+/).filter(Boolean);

let cache = null;
function model() {
  if (cache) return cache;
  const nodes = new Map(DATA.graph.nodes.map((n) => [n.id, n]));
  const out = new Map(), inn = new Map();
  const push = (m, k, v) => { (m.get(k) || m.set(k, []).get(k)).push(v); };
  for (const [s, d, type, line, conf] of DATA.deps) {
    push(out, s, { o: d, type, line, conf });
    push(inn, d, { o: s, type, line, conf });
  }
  const byFile = new Map(), imports = new Map(), importedBy = new Map(), defines = new Map();
  for (const n of nodes.values()) if (n.k !== "file" && n.k !== "folder") push(byFile, n.f, n);
  for (const [a, b, type] of DATA.graph.edges) {
    if (type === "IMPORTS") { push(imports, a, b); push(importedBy, b, a); }
    else if (type === "DEFINES") push(defines, a, b);
  }
  const rows = DATA.search.map((r) => ({ id: r[0], label: r[1], kind: r[2], file: r[3], line: r[4], summary: r[5], hay: `${r[1]} ${words(r[1]).join(" ")} ${r[3]} ${r[5]}`.toLowerCase() }));
  cache = { nodes, out, inn, byFile, imports, importedBy, defines, rows, procs: DATA.processes };
  return cache;
}

// ------------------------------------------------------------------ impact

function walk(m, seeds, direction, depth) {
  const visited = new Set(seeds);
  let frontier = new Map(seeds.map((s) => [s, "high"]));
  const levels = {}, truncated = {};
  const adj = direction === "upstream" ? m.inn : m.out;
  for (let d = 1; d <= depth; d++) {
    const found = new Map();
    for (const [node, pathConf] of frontier) {
      for (const e of adj.get(node) || []) {
        if (!DEP.has(e.type) || visited.has(e.o)) continue;
        const conf = minConf(pathConf, e.conf);
        const prev = found.get(e.o);
        if (!prev || RANK[conf] > RANK[prev.conf]) found.set(e.o, { via: node, type: e.type, line: e.line, conf });
      }
    }
    if (!found.size) break;
    let entries = [...found.entries()];
    if (entries.length > MAX_LEVEL) {
      truncated[d] = entries.length;
      entries = entries.sort((a, b) => RANK[b[1].conf] - RANK[a[1].conf] || (a[0] < b[0] ? -1 : 1)).slice(0, MAX_LEVEL);
    }
    levels[d] = Object.fromEntries(entries);
    entries.forEach(([k]) => visited.add(k));
    frontier = new Map(entries.map(([k, v]) => [k, v.conf]));
    if (truncated[d]) break;
  }
  return { levels, truncated };
}

function riskLevel(affected, flows, isEntry) {
  if (affected > 30 || flows >= 4 || isEntry) return "CRITICAL";
  if (affected >= 11 || flows >= 2) return "HIGH";
  if (affected >= 4 || flows >= 1) return "MEDIUM";
  return "LOW";
}

function impact(target, direction) {
  const m = model();
  const dir = direction === "downstream" ? "downstream" : "upstream";
  let seeds = [], kind = "symbol", file = "";
  const node = m.nodes.get(target);
  if (!node) return { error: "none", candidates: [], target };
  if (node.k === "file") { kind = "file"; file = node.f; seeds = (m.byFile.get(node.f) || []).map((n) => n.id); }
  else if (node.k === "folder") { kind = "file"; file = node.f; seeds = [...m.nodes.values()].filter((n) => n.k !== "file" && n.k !== "folder" && (n.f === node.f || n.f.startsWith(node.f + "/"))).map((n) => n.id); }
  else {
    file = node.f; seeds = [node.id];
    if (node.k === "class") seeds.push(...(m.byFile.get(node.f) || []).filter((n) => n.l.startsWith(node.l + ".")).map((n) => n.id));
  }
  seeds = [...new Set(seeds)];
  const { levels, truncated } = walk(m, seeds, dir, 3);
  const tests = {};
  const kept = {};
  for (const [d, lv] of Object.entries(levels)) {
    for (const [sid, v] of Object.entries(lv)) {
      if (isTest(sid)) tests[sid] = { depth: Number(d), ...v };
      else (kept[d] ||= {})[sid] = v;
    }
  }
  const counted = new Set(), possible = {};
  for (const lv of Object.values(kept)) for (const [sid, v] of Object.entries(lv)) (v.conf === "low" ? (possible[sid] = v) : counted.add(sid));
  const touched = new Set([...seeds, ...Object.values(kept).flatMap((lv) => Object.keys(lv))]);
  const processes = m.procs.filter((p) => !p.is_test && p.steps_ids.some((s) => touched.has(s))).map((p) => ({ id: p.id, name: p.name, entry: p.entry, summary: p.summary }));
  const isEntry = processes.some((p) => seeds.includes(p.entry));
  return {
    target, kind, direction: dir, seeds, levels: kept, truncated, possible, processes, unresolved_matches: [], importing_files: [], tests,
    risk: riskLevel(counted.size, processes.length, isEntry), affected: counted.size, is_entry: isEntry, fresh: true, file, symbols: {},
  };
}

// ------------------------------------------------------------------ trace

function findSymbols(m, q) {
  const all = [...m.nodes.values()].filter((n) => n.k !== "file" && n.k !== "folder");
  const exact = all.filter((n) => n.id === q);
  if (exact.length) return exact;
  const byQ = all.filter((n) => n.l === q || n.l.endsWith("." + q));
  if (byQ.length) return byQ;
  return all.filter((n) => short(n.id) === q);
}

function bfsPath(m, start, goal, allowLow) {
  const queue = [[start, 0]], prev = new Map(), seen = new Set([start]);
  while (queue.length) {
    const [node, d] = queue.shift();
    if (node === goal) {
      const path = [];
      let cur = goal;
      while (cur !== start) { const [parent, line, conf] = prev.get(cur); path.push([cur, line, conf]); cur = parent; }
      path.push([start, 0, "high"]);
      return path.reverse();
    }
    if (d >= 8) continue;
    const next = (m.out.get(node) || []).filter((e) => e.type === "CALLS" || e.type === "REFERENCES").sort((a, b) => a.line - b.line);
    for (const e of next) {
      if (seen.has(e.o) || (e.conf === "low" && !allowLow) || !m.nodes.has(e.o)) continue;
      seen.add(e.o); prev.set(e.o, [node, e.line, e.conf]); queue.push([e.o, d + 1]);
    }
  }
  return null;
}

function trace(from, to) {
  const m = model();
  const a = findSymbols(m, from), b = findSymbols(m, to);
  if (a.length !== 1 || b.length !== 1) {
    const card = (n) => ({ id: n.id, qname: n.l, kind: n.k, file: n.f, start: n.s });
    return { error: "ambiguous", from: a.map(card), to: b.map(card) };
  }
  const start = a[0].id, goal = b[0].id;
  for (const allowLow of [false, true]) {
    const path = bfsPath(m, start, goal, allowLow);
    if (path) return { path, low: allowLow, symbols: {}, start, goal };
  }
  const reach = Object.values(walk(m, [start], "downstream", 3).levels).flatMap((lv) => Object.keys(lv)).slice(0, 12);
  return { path: [], start, goal, reachable: reach, unresolved_from_start: [], symbols: {} };
}

// ------------------------------------------------------------------ search

function search(query, limit, related) {
  const m = model();
  const kinds = new Set([...query.matchAll(/\btype:(\w+)/gi)].map((x) => x[1].toLowerCase()));
  const pathTerms = [...query.matchAll(/\bpath:(\S+)/gi)].map((x) => x[1].toLowerCase());
  let rest = query.replace(/\b(?:type|path):\S+/gi, " ").trim();
  const ws = rest.split(/\s+/).filter(Boolean);
  pathTerms.push(...ws.filter((w) => w.includes("/")).map((w) => w.toLowerCase()));
  const terms = ws.filter((w) => !w.includes("/")).flatMap((w) => words(w));
  if (!terms.length && !kinds.size && !pathTerms.length) return [];
  const hits = [];
  for (const r of m.rows) {
    if (kinds.size && !kinds.has(r.kind)) continue;
    if (pathTerms.length && !pathTerms.every((p) => r.file.toLowerCase().includes(p))) continue;
    let score = 0;
    if (terms.length) {
      const lab = r.label.toLowerCase(), lw = words(r.label);
      let matched = 0;
      for (const t of terms) {
        if (lab === t) score += 8;
        else if (lw.includes(t)) score += 4;
        else if (lab.includes(t)) score += 3;
        else if (r.hay.includes(t)) score += 1;
        else continue;
        matched++;
      }
      if (matched < terms.length) { if (!matched) continue; score *= 0.4; }
      if (r.kind === "file" || r.kind === "folder") score *= 0.8;
    } else score = 1;
    hits.push({ id: r.id, label: r.label, kind: r.kind, file: r.file, line: r.line, score });
  }
  hits.sort((a, b) => b.score - a.score || a.label.length - b.label.length);
  const direct = hits.slice(0, limit);
  if (!related || !terms.length) return direct;
  const seen = new Set(direct.map((h) => h.id));
  const extra = [];
  for (const h of direct.slice(0, 12)) {
    for (const [dir, adj] of [["calls", m.out], ["is called by", m.inn]]) {
      for (const e of adj.get(h.id) || []) {
        if (!DEP.has(e.type) || seen.has(e.o) || !m.nodes.has(e.o) || isTest(e.o)) continue;
        seen.add(e.o);
        const n = m.nodes.get(e.o);
        extra.push({ id: n.id, label: n.l, kind: n.k, file: n.f, line: n.s, score: h.score * 0.3, why: `${dir === "calls" ? "is called by" : "calls"} ${h.label}`, direct: false });
      }
    }
  }
  direct.forEach((h) => { h.why = "matches the name or description"; h.direct = true; });
  const keep = Math.max(1, Math.ceil(limit * 0.65));
  return [...direct.slice(0, keep), ...extra.sort((a, b) => b.score - a.score).slice(0, limit - Math.min(keep, direct.length)), ...direct.slice(keep, limit)]
    .sort((a, b) => b.score - a.score).slice(0, limit);
}

// ------------------------------------------------------------------ nodes

const dirname = (p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");

function nodeDetail(id) {
  const m = model();
  const n = m.nodes.get(id);
  if (!n) return { type: "missing", id };
  if (n.k === "folder") {
    const d = n.f;
    const under = [...m.nodes.values()].filter((x) => x.k === "file" && x.f.startsWith(d + "/"));
    return {
      type: "folder", id, path: d, files: under.filter((x) => dirname(x.f) === d).map((x) => x.f).sort(),
      folders: [...new Set(under.filter((x) => dirname(x.f) !== d).map((x) => x.f.slice(d.length + 1).split("/")[0]))].sort(),
      total_files: under.length, lines: under.reduce((sum, x) => sum + (x.n || 0), 0),
    };
  }
  const fx = DATA.fx[n.f] || {};
  if (n.k === "file") {
    return {
      type: "file", id, path: id, lang: n.lang, lines: n.n, error: fx.err || "", sha: "", indexed_at: DATA.status.generated,
      symbols: (m.byFile.get(id) || []).map((x) => ({ id: x.id, kind: x.k, qname: x.l, start: x.s, end: x.e, summary: (DATA.symx[x.id] || {}).sum || "" })),
      imports: [...(m.imports.get(id) || []), ...(fx.ext || [])].sort(),
      imported_by: [...(m.importedBy.get(id) || [])].sort(),
      unresolved: (fx.un || []).map(([name, line, reason]) => ({ name, line, reason })),
    };
  }
  const x = DATA.symx[id] || {};
  const edges = (adj, key) => (adj.get(id) || []).filter((e) => DEP.has(e.type)).map((e) => ({ type: e.type, [key]: e.o, line: e.line, conf: e.conf }));
  const cluster = n.c ? DATA.clusters.find((c) => c.id === n.c) : null;
  const callers = edges(m.inn, "src"), callees = edges(m.out, "dst");
  return {
    type: "symbol", id, impact_counts: { callers: callers.length, callees: callees.length },
    symbol: { id, kind: n.k, name: short(id).split(".").pop(), qname: n.l, file: n.f, start: n.s, end: n.e, signature: x.sig || "", summary: x.sum || "" },
    callers, callees,
    unresolved: (x.un || []).map(([name, line]) => ({ type: "CALLS", dst: "?" + name, line, conf: "low" })),
    children: (m.defines.get(id) || []).map((c) => m.nodes.get(c)).filter(Boolean).map((c) => ({ id: c.id, kind: c.k, start: c.s, end: c.e })).sort((a, b) => a.start - b.start),
    imported_by: [...(m.importedBy.get(n.f) || [])].sort(),
    cluster: cluster ? { id: cluster.id, label: cluster.label } : null,
    processes: m.procs.filter((p) => p.steps_ids.includes(id)).map((p) => ({ id: p.id, name: p.name, ord: p.steps_ids.indexOf(id) + 1 })),
    fresh: true, dynamic: x.dyn || { level: "none", evidence: [] },
  };
}

export async function staticApi(path) {
  await ready;
  const [route, query] = path.split("?");
  const q = new URLSearchParams(query || "");
  if (route === "/projects") return DATA.projects;
  const match = route.match(/^\/p\/[^/]+\/(\w+)$/);
  if (!match) throw new Error("This action needs a live server. Regenerate the report to refresh it.");
  switch (match[1]) {
    case "status": return DATA.status;
    case "graph": return q.get("overview") === "1" && DATA.graphOverview ? DATA.graphOverview : DATA.graph;
    case "processes": return { processes: DATA.processes };
    case "clusters": return { clusters: DATA.clusters };
    case "process": return DATA.processDetail[q.get("id")] || { error: "unknown process" };
    case "node": return nodeDetail(q.get("id") || "");
    case "source": return DATA.sources[q.get("file")] || { error: "the source of this file is not embedded in the report (size limit)" };
    case "search": return { results: search(q.get("q") || "", Number(q.get("limit")) || 30, q.get("mode") === "graph") };
    case "impact": return impact(q.get("target") || "", q.get("direction") || "upstream");
    case "trace": return trace(q.get("from") || "", q.get("to") || "");
    case "changes": return DATA.changes;
    case "unused": return DATA.unused;
    case "taint": return DATA.taint;
    default: throw new Error(`Not available in the offline report: ${match[1]}`);
  }
}
