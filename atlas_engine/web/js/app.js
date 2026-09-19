// CodeAtlas Explorer: wires the graph canvas, search, panels, modals and chat together.

import {
  fill, $, $$, h, api, toast, debounce, short, fileOf, fmtAge, highlightLine,
  KIND_COLORS, KIND_LABELS, EDGE_COLORS, EDGE_LABELS, CLUSTER_PALETTE, RISK_COLORS,
} from "./util.js";
import { GraphView } from "./graph.js";
import { ChatPanel } from "./chat.js";
import { helpModal, settingsModal, analyzeModal, queryModal, changesModal, flowModal, parseErrorsModal, skippedModal, closeTopModal, hasModal } from "./modals.js";

const LARGE_GRAPH = 15000;
const PREF_KEY = "codeatlas.prefs";
const S = {
  project: null, projects: [], home: null, status: null, data: null, nodes: new Map(), byFile: new Map(), tree: null,
  expanded: new Set(), selected: null, hl: null, hlOn: true, processes: [], clusters: [], codeCache: new Map(), citations: [],
  loadToken: 0, layout: "idle", showTests: false, chat: null, polling: null, indexing: false, lastQuery: "", codeView: { file: null },
  prefs: { theme: "auto", colorBy: "kind", autoRefresh: true, labelDensity: 0.9, rightWidth: 400 },
};
try { Object.assign(S.prefs, JSON.parse(localStorage.getItem(PREF_KEY) || "{}")); } catch { /* fresh profile */ }
const savePrefs = () => { try { localStorage.setItem(PREF_KEY, JSON.stringify(S.prefs)); } catch { /* private mode */ } };
const P = () => encodeURIComponent(S.project);

// ------------------------------------------------------------------------------------ graph view

const view = new GraphView($("#sigma"), {
  onSelect: (id) => selectNode(id),
  onFocus: (id) => { view.setFilters({ depth: Math.max(view.filters.depth, 2) }); syncFilterUI(); selectNode(id, { focus: true }); },
  onLayoutState: (st) => { S.layout = st; renderToolbar(); },
  onStats: () => renderStatus(),
});
window.__atlas = { S, view };       // handy in the browser console and for automated tests

// ------------------------------------------------------------------------------------ theme and prefs

function applyTheme() {
  const t = S.prefs.theme === "auto" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : S.prefs.theme;
  document.documentElement.dataset.theme = t;
  view.setTheme(t);
}
function setPrefs(patch) {
  Object.assign(S.prefs, patch);
  savePrefs();
  applyTheme();
  if (view.ready) { view.setColorBy(S.prefs.colorBy); view.sigma.setSetting("labelDensity", S.prefs.labelDensity); }
  renderLegend();
  restartPolling();
}
$("#btn-theme").addEventListener("click", () => setPrefs({ theme: document.documentElement.dataset.theme === "dark" ? "light" : "dark" }));

// ------------------------------------------------------------------------------------ projects

async function refreshProjects() {
  const r = await api("/projects");
  S.projects = r.projects;
  S.home = r.home;
}

async function waitForJob(id, onUpdate) {
  for (;;) {
    const j = await api(`/jobs/${id}`);
    onUpdate && onUpdate(j);
    if (j.status === "done") return j;
    if (j.status === "error") throw new Error(j.message);
    await new Promise((r) => setTimeout(r, 400));
  }
}

async function openProject(name) {
  const token = ++S.loadToken;
  S.project = name;
  S.selected = null;
  S.hl = null;
  S.citations = [];
  S.chat = null;
  S.processes = []; S.clusters = [];
  S.codeCache.clear();
  S.pendingNode = new URLSearchParams(location.hash.slice(1)).get("node");   // deep link, read before the hash is cleared
  $("#project-name").textContent = name;
  document.title = `${name} · CodeAtlas`;
  const url = new URL(location.href);
  url.searchParams.set("project", name);
  url.hash = "";
  history.replaceState(null, "", url);
  showOverlay(h("div", { class: "box" }, h("span", { class: "spin" }), h("p", {}, "Loading graph…")));
  try {
    S.status = await api(`/p/${P()}/status`);
    if (token !== S.loadToken) return;
    if (!S.status.counts.files) {
      showOverlay(h("div", { class: "box" }, h("h3", {}, "Not indexed yet"), h("p", { class: "muted" }, "This project has no graph. Build it now."),
        h("button", { class: "btn primary", on: { click: () => reindex(true) } }, "Analyze this repository")));
      renderAll(); return;
    }
    const estimate = S.status.counts.files + S.status.counts.symbols;
    if (estimate > LARGE_GRAPH) {
      showOverlay(h("div", { class: "box" }, h("h3", {}, "Large project"), h("p", { class: "muted" }, `This project has about ${estimate.toLocaleString()} nodes. Drawing all of them can make the browser slow.`),
        h("div", { class: "row wrap", style: { justifyContent: "center" } },
          h("button", { class: "btn primary", on: { click: () => loadGraph({ overview: true }) } }, "Load overview (folders and files)"),
          h("button", { class: "btn", on: { click: () => loadGraph({ overview: false }) } }, "Load full graph anyway")),
        h("p", { class: "muted small" }, "Search, details, flows and Nexus AI work without the full graph.")));
      renderAll();
      loadSideData(token);
      return;
    }
    await loadGraph({ overview: false }, token);
    loadSideData(token);
  } catch (e) {
    showOverlay(h("div", { class: "box" }, h("h3", {}, "Could not load this project"), h("p", { class: "muted" }, e.message),
      h("button", { class: "btn", on: { click: () => location.reload() } }, "Reload")));
  }
}

async function loadGraph({ overview }, token = S.loadToken, keepView = false) {
  showOverlay(h("div", { class: "box" }, h("span", { class: "spin" }), h("p", {}, "Building graph…")));
  const data = await api(`/p/${P()}/graph?overview=${overview ? 1 : 0}`);
  if (token !== S.loadToken) return;
  S.data = data;
  S.nodes = new Map(data.nodes.map((n) => [n.id, n]));
  S.byFile = new Map();
  data.nodes.forEach((n) => { if (n.k !== "file" && n.k !== "folder") (S.byFile.get(n.f) || S.byFile.set(n.f, []).get(n.f)).push(n); });
  S.byFile.forEach((list) => list.sort((a, b) => (a.s || 0) - (b.s || 0)));
  buildTree();
  hideOverlay();
  view.colorBy = S.prefs.colorBy;
  view.load(data, keepView);
  view.sigma.setSetting("labelDensity", S.prefs.labelDensity);
  renderAll();
  const hash = S.pendingNode;
  S.pendingNode = null;
  if (hash && S.nodes.has(hash)) selectNode(hash, { focus: true });
  else if (S.selected && S.nodes.has(S.selected)) view.select(S.selected);
  if (data.truncated) toast(`Showing the ${data.nodes.length.toLocaleString()} most connected nodes of ${data.total_nodes.toLocaleString()}`);
}

async function loadSideData(token) {
  const [p, c] = await Promise.all([api(`/p/${P()}/processes`).catch(() => ({ processes: [] })), api(`/p/${P()}/clusters`).catch(() => ({ clusters: [] }))]);
  if (token !== S.loadToken) return;
  S.processes = p.processes;
  S.clusters = c.clusters;
  renderFlows(); renderClusters(); renderTabBadges();
  if (S.selected) renderDetails(S.selected);
}

async function reindex(full = false) {
  if (S.indexing) return;
  S.indexing = true;
  renderStatus();
  try {
    const r = await api(`/projects/${encodeURIComponent(S.project)}/reindex`, { method: "POST", body: { full } });
    showOverlay(h("div", { class: "box" }, h("span", { class: "spin" }), h("p", { id: "job-msg" }, "Indexing…"), h("div", { class: "progress" }, h("div"))));
    await waitForJob(r.job, (j) => { const m = $("#job-msg"); if (m) m.textContent = j.message; });
    toast("Index updated");
    S.indexing = false;
    await openProject(S.project);
  } catch (e) { toast(e.message, "bad"); hideOverlay(); }
  S.indexing = false;
  renderStatus();
}

function renderProjectMenu() {
  const menu = $("#project-menu");
  const filter = h("input", { type: "search", class: "plain", placeholder: "Search repositories…", "aria-label": "Search repositories", style: { margin: "4px 4px 6px", width: "calc(100% - 8px)" } });
  const list = h("div");
  const draw = () => {
    const q = filter.value.toLowerCase();
    const rows = S.projects.filter((p) => p.name.toLowerCase().includes(q));
    fill(list, rows.length ? rows.map((p) => h("div", { class: "dd-item" + (p.name === S.project ? " sel" : ""), on: { click: () => { menu.hidden = true; openProject(p.name); } } },
      h("div", { class: "grow" }, h("div", { class: "t" }, p.name, p.name === S.project ? h("span", { class: "badge ok", style: { marginLeft: "6px" } }, "active") : null),
        h("div", { class: "s" }, p.stats ? `${p.stats.files} files · ${p.stats.symbols} symbols · ${p.root}` : `not indexed · ${p.root}`)),
      h("button", { class: "btn small", title: `Re-analyze ${p.name}`, on: { click: (e) => { e.stopPropagation(); menu.hidden = true; if (p.name !== S.project) openProject(p.name).then(() => reindex(true)); else reindex(true); } } }, "↻"),
      h("button", { class: "btn small", title: `Remove ${p.name} from the list (your files are not touched)`, on: { click: async (e) => {
        e.stopPropagation();
        if (!confirm(`Remove "${p.name}" from CodeAtlas? Your source files are not touched.`)) return;
        await api(`/projects/${encodeURIComponent(p.name)}`, { method: "DELETE" });
        await refreshProjects();
        if (p.name === S.project) S.projects.length ? openProject(S.projects[0].name) : landing();
        renderProjectMenu();
      } } }, "✕"))) : h("div", { class: "dd-empty" }, `No repositories found for "${filter.value}"`));
  };
  filter.addEventListener("input", draw);
  draw();
  fill(menu, filter, list, h("div", { class: "dd-sep" }), h("div", { class: "dd-item", on: { click: () => { menu.hidden = true; analyzeModal(ctx()); } } }, h("span", {}, "＋"), h("div", { class: "grow t" }, "Analyze a new repository…")));
  setTimeout(() => filter.focus(), 10);
}
$("#btn-project").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = $("#project-menu");
  menu.hidden = !menu.hidden;
  $("#btn-project").setAttribute("aria-expanded", String(!menu.hidden));
  if (!menu.hidden) renderProjectMenu();
});

function landing() {
  S.project = null;
  $("#project-name").textContent = "No repository";
  showOverlay(h("div", { class: "box" }, h("h3", {}, "Welcome to CodeAtlas Explorer"), h("p", { class: "muted" }, "Analyze a repository to see its graph, search it, trace flows and ask questions."),
    h("button", { class: "btn primary", on: { click: () => analyzeModal(ctx()) } }, "Analyze a repository")));
}

// ------------------------------------------------------------------------------------ overlay, status, toolbar

function showOverlay(content) { const o = $("#overlay"); o.hidden = false; fill(o, content); }
function hideOverlay() { $("#overlay").hidden = true; }

function renderStatus() {
  const st = S.status, el = $("#status");
  if (!st) { fill(el, h("span", {}, "Loading…")); return; }
  const vc = view.ready ? view.visibleCounts() : { nodes: 0, edges: 0 };
  const total = S.data ? S.data.nodes.length : 0;
  const errs = st.parse_errors.length;
  fill(el, 
    h("span", { class: S.indexing ? "warn" : "ok" }, S.indexing ? "◌ Indexing…" : "● Ready"),
    h("span", {}, `${vc.nodes.toLocaleString()}${vc.nodes !== total ? ` / ${total.toLocaleString()}` : ""} nodes · ${vc.edges.toLocaleString()} edges`),
    h("span", {}, `${st.counts.files} files · ${st.languages.map(([l, n]) => `${l} ${n}`).join(", ") || "—"}`),
    h("span", { title: st.meta.lastLink }, `Indexed ${fmtAge(st.meta.lastLink)}`),
    st.stale_count ? h("span", { class: "warn" }, `${st.stale_count} file(s) changed `, h("button", { on: { click: () => reindex(false) } }, "Re-index")) : null,
    errs ? h("span", { class: "warn" }, h("button", { on: { click: () => parseErrorsModal(ctx(), st.parse_errors) } }, `${errs} parse error${errs > 1 ? "s" : ""}`)) : null,
    st.skipped && st.skipped.length ? h("span", { class: "warn" }, h("button", { on: { click: () => skippedModal(st.skipped) }, title: "Files not in the graph" }, `${st.skipped.length} skipped`)) : null,
    h("span", { class: "grow" }),
    h("span", {}, `clustering: ${st.meta.clusterAlgo || "n/a"}`));
}

function renderToolbar() {
  const t = $("#toolbar");
  const btn = (label, title, fn, active = false) => h("button", { class: "tb-btn" + (active ? " active" : ""), title, "aria-label": title, "aria-pressed": active ? "true" : "false", on: { click: fn } }, label);
  const running = S.layout === "running";
  fill(t, 
    h("div", { class: "tb-group", role: "group", "aria-label": "Graph view mode" },
      btn("Force", "Force graph (1)", () => setMode("force"), view.viewMode === "force"),
      btn("Tree", "Sequential layout (2)", () => setMode("tree"), view.viewMode === "tree"),
      btn("Radial", "Radial layout (3)", () => setMode("radial"), view.viewMode === "radial")),
    h("div", { class: "tb-group" },
      btn("＋", "Zoom in (+)", () => view.zoomIn()), btn("－", "Zoom out (−)", () => view.zoomOut()), btn("⤢", "Fit to screen (F)", () => view.fit()),
      btn("◎", "Focus on selected node", () => S.selected && view.focusNode(S.selected)), btn("✕", "Clear selection (Esc)", () => selectNode(null))),
    h("div", { class: "tb-group" },
      btn(running ? "■ Stop" : "↻ Layout", running ? "Stop layout" : "Run layout again", () => (running ? view.stopLayout() : view.viewMode === "force" ? view.runForce() : view.setViewMode(view.viewMode))),
      btn(S.hlOn ? "Highlights on" : "Highlights off", S.hlOn ? "Turn off all highlights" : "Turn on highlights", () => { S.hlOn = !S.hlOn; applyHighlight(); renderToolbar(); }, S.hlOn)),
    running ? h("div", { class: "tb-group" }, h("span", { class: "tb-btn muted" }, h("span", { class: "spin" }), " Layout optimizing…")) : null);
}
function setMode(m) { view.setViewMode(m); renderToolbar(); }

function renderLegend() {
  const lg = $("#legend");
  if (S.prefs.colorBy === "cluster") fill(lg, h("span", { class: "lg" }, "Coloured by cluster"), S.clusters.slice(0, 8).map((c) => h("span", { class: "lg" }, h("span", { class: "dot", style: { background: CLUSTER_PALETTE[(+c.id.slice(1) - 1) % CLUSTER_PALETTE.length] } }), c.label)));
  else fill(lg, Object.entries(KIND_LABELS).map(([k, l]) => h("span", { class: "lg" }, h("span", { class: "dot " + k }), l)));
}

// ------------------------------------------------------------------------------------ highlights

function setHighlight(source, label, ids, edges = new Set()) {
  const map = ids instanceof Map ? ids : new Map([...ids].map((i) => [i, true]));
  S.hl = { source, label, map, edges };
  S.hlOn = true;
  applyHighlight();
  renderToolbar();
}
function clearHighlight() { S.hl = null; applyHighlight(); }
function applyHighlight() {
  view.setHighlights(S.hl && S.hlOn ? S.hl.map : new Map(), S.hl && S.hlOn ? S.hl.edges : new Set());
  $$(".chip-info").forEach((c) => c.remove());
  if (S.hl) $("#stage").append(h("div", { class: "chip-info" }, h("span", {}, `Highlighting: ${S.hl.label}`), h("span", { class: "muted small" }, `${S.hl.map.size} nodes`),
    h("button", { class: "btn small", on: { click: clearHighlight } }, "Clear")));
}
function highlightFlow(proc) {
  const edges = new Set(proc.edges.map((e) => `${e.src}|${e.dst}`));
  setHighlight("flow", proc.name || "flows", new Set(proc.steps_ids), edges);
  view.fitTo(new Set(proc.steps_ids));
}

// ------------------------------------------------------------------------------------ selection and details

const tokenOf = () => ++S.detailToken;
S.detailToken = 0;

async function selectNode(id, { focus = false, tab = "details" } = {}) {
  S.selected = id || null;
  // In the overview graph a symbol is not a node; light up its file instead and still show the symbol's details.
  const inGraph = id && S.nodes.has(id);
  const proxy = inGraph ? id : (id && S.nodes.has(fileOf(id)) ? fileOf(id) : null);
  view.select(proxy, { focus });
  if (S.selected) revealInTree(S.selected);
  renderTree();
  const url = new URL(location.href);
  url.hash = S.selected ? `node=${encodeURIComponent(S.selected)}` : "";
  history.replaceState(null, "", url);
  if (!S.selected) { renderDetails(null); return; }
  if (tab) setTab("right", tab);
  const d = await renderDetails(S.selected);
  if (!d || S.selected !== id) return;
  if (d.type === "symbol") showCode(d.symbol.file, d.symbol.start, d.symbol.end, { quiet: true });
  else if (d.type === "file") showCode(d.path, null, null, { quiet: true });
}

function listItem(id, extra = "", conf = null, onClick = null) {
  const n = S.nodes.get(id);
  const kind = n ? n.k : id.startsWith("dir:") ? "folder" : id.includes("::") ? "function" : "file";
  return h("div", { class: "list-item", on: { click: onClick || (() => selectNode(id, { focus: true })) } }, h("span", { class: "dot " + kind }),
    h("span", { class: "grow", title: id }, id.startsWith("dir:") ? id.slice(4).split("/").pop() : id.includes("::") ? short(id) : id.split("/").pop()),
    conf && conf !== "high" ? h("span", { class: "badge " + (conf === "low" ? "warn" : "") }, conf) : null, extra ? h("span", { class: "muted small" }, extra) : null);
}
const section = (title, items, open = true) => items && items.length ? h("div", {}, h("h4", {}, `${title} (${items.length})`), open ? items : items.slice(0, 8)) : null;

async function renderDetails(id) {
  const host = $("#right-details");
  const token = tokenOf();
  if (!id) {
    fill(host, h("div", { class: "empty" }, "Select a node to see its details.", h("div", { class: "small", style: { marginTop: "6px" } }, "Click a node in the graph, the explorer, or search results.")),
      h("h4", {}, "Try"), h("div", { class: "muted small" }, "• Double-click a node to focus its neighbourhood", h("br"), "• Ctrl/⌘ K to search", h("br"), "• Ask Nexus AI a question"));
    return null;
  }
  fill(host, h("span", { class: "spin" }));
  let d;
  try { d = await api(`/p/${P()}/node?id=${encodeURIComponent(id)}`); } catch (e) { fill(host, h("div", { class: "card" }, e.message)); return null; }
  if (token !== S.detailToken) return null;
  if (d.type === "missing") { fill(host, h("div", { class: "empty" }, "This node is not in the index.")); return null; }
  fill(host, d.type === "symbol" ? symbolDetails(d) : d.type === "file" ? fileDetails(d) : folderDetails(d));
  return d;
}

function symbolDetails(d) {
  const s = d.symbol;
  const cluster = S.clusters.find((c) => c.members.includes(d.id));
  const byType = (rows, key) => rows.reduce((m, r) => { (m[r.type] = m[r.type] || []).push(r); return m; }, {});
  const callers = byType(d.callers), callees = byType(d.callees);
  const out = [
    h("div", { class: "row wrap" }, h("span", { class: "dot " + s.kind }), h("h3", { style: { margin: 0 } }, s.qname === "<module>" ? "(module)" : s.qname), h("span", { class: "badge" }, s.kind),
      d.fresh ? null : h("span", { class: "badge warn", title: "The file changed since it was indexed" }, "stale")),
    h("div", { class: "muted small", style: { margin: "3px 0" } }, h("a", { href: "#", on: { click: (e) => { e.preventDefault(); showCode(s.file, s.start, s.end); setTab("right", "code"); } } }, `${s.file}:${s.start}-${s.end}`)),
    s.signature ? h("code", { class: "sig" }, s.signature) : null,
    s.summary ? h("p", { class: "muted", style: { margin: "4px 0" } }, s.summary) : null,
    h("div", { class: "row wrap", style: { margin: "8px 0" } },
      cluster ? h("button", { class: "badge", style: { cursor: "pointer" }, on: { click: () => highlightCluster(cluster) } }, `◈ ${cluster.label}`) : null,
      ...d.processes.map((p) => h("button", { class: "badge", style: { cursor: "pointer" }, title: `Step ${p.ord}`, on: { click: () => openProcess(p.id) } }, `↯ ${p.name}`))),
    h("div", { class: "row wrap" },
      h("button", { class: "btn small", on: { click: () => runImpact(d.id, "upstream", box) } }, "💥 Impact"),
      h("button", { class: "btn small", on: { click: () => runImpact(d.id, "downstream", box) } }, "↓ Depends on"),
      h("button", { class: "btn small", on: { click: () => { view.setFilters({ depth: Math.max(view.filters.depth, 2) }); syncFilterUI(); view.select(d.id, { focus: true }); } } }, "◎ Focus subgraph"),
      h("button", { class: "btn small", on: { click: () => { showCode(s.file, s.start, s.end); setTab("right", "code"); } } }, "</> Code")),
  ];
  const traceIn = h("input", { type: "text", placeholder: "Trace to… (symbol name)", class: "plain", style: { marginTop: "8px" }, "aria-label": "Trace to symbol" });
  traceIn.addEventListener("keydown", (e) => { if (e.key === "Enter") runTrace(d.id, traceIn.value, box); });
  const box = h("div", { style: { marginTop: "8px" } });
  out.push(traceIn, box);
  const groups = [["Called by", callers.CALLS], ["Referenced by", callers.REFERENCES], ["Extended by", callers.EXTENDS]];
  groups.forEach(([t, rows]) => rows && out.push(section(t, rows.map((r) => listItem(r.src, `line ${r.line}`, r.conf)))));
  [["Calls", callees.CALLS], ["References", callees.REFERENCES], ["Extends", callees.EXTENDS]].forEach(([t, rows]) => rows && out.push(section(t, rows.map((r) => listItem(r.dst, `line ${r.line}`, r.conf)))));
  out.push(section("Defines", d.children.map((c) => listItem(c.id, c.kind))));
  out.push(section("Unresolved calls (not in the graph)", d.unresolved.map((u) => h("div", { class: "list-item", title: "The graph could not tell which function this calls" }, h("span", { class: "grow mono small" }, u.dst.slice(1)), h("span", { class: "muted small" }, `line ${u.line}`)))));
  if (d.imported_by.length && s.qname.indexOf(".") < 0) out.push(section("File imported by", d.imported_by.map((f) => listItem(f))));
  return out.filter(Boolean);
}

function fileDetails(d) {
  const out = [
    h("div", { class: "row wrap" }, h("span", { class: "dot file" }), h("h3", { style: { margin: 0 } }, d.path.split("/").pop()), h("span", { class: "badge" }, d.lang), h("span", { class: "badge" }, `${d.lines} lines`)),
    h("div", { class: "muted small", style: { margin: "3px 0" } }, d.path),
    d.error ? h("div", { class: "card", style: { borderColor: "var(--warn)" } }, h("strong", {}, "Syntax error: "), d.error, h("div", { class: "muted small" }, "Symbols were recovered by a tolerant scan. This file has no call edges until the syntax is fixed.")) : null,
    h("div", { class: "row wrap", style: { margin: "8px 0" } },
      h("button", { class: "btn small", on: { click: () => { showCode(d.path); setTab("right", "code"); } } }, "</> View code"),
      h("button", { class: "btn small", on: { click: () => runImpact(d.id, "upstream", box) } }, "💥 Impact of this file"),
      h("button", { class: "btn small", on: { click: () => { view.setFilters({ depth: Math.max(view.filters.depth, 2) }); syncFilterUI(); view.select(d.id, { focus: true }); } } }, "◎ Focus subgraph")),
  ];
  const box = h("div");
  out.push(box);
  out.push(section("Symbols", d.symbols.map((s) => listItem(s.id, `line ${s.start}`))));
  out.push(section("Imports", d.imports.map((f) => (f.startsWith("ext:") ? h("div", { class: "list-item" }, h("span", { class: "dot folder" }), h("span", { class: "grow mono small" }, f.slice(4)), h("span", { class: "muted small" }, "external")) : listItem(f)))));
  out.push(section("Imported by", d.imported_by.map((f) => listItem(f))));
  out.push(section("Unresolved calls", d.unresolved.map((u) => h("div", { class: "list-item" }, h("span", { class: "grow mono small" }, u.name), h("span", { class: "muted small" }, `line ${u.line}`)))));
  return out.filter(Boolean);
}

function folderDetails(d) {
  return [
    h("div", { class: "row wrap" }, h("span", { class: "dot folder" }), h("h3", { style: { margin: 0 } }, d.path.split("/").pop() || d.path), h("span", { class: "badge" }, `${d.total_files} files`), h("span", { class: "badge" }, `${d.lines} lines`)),
    h("div", { class: "muted small", style: { margin: "3px 0 8px" } }, d.path),
    h("div", { class: "row wrap" }, h("button", { class: "btn small", on: { click: () => { const ids = new Set(S.data.nodes.filter((n) => n.f === d.path || n.f.startsWith(d.path + "/")).map((n) => n.id)); setHighlight("folder", d.path, ids); view.fitTo(ids); } } }, "Highlight everything inside")),
    section("Folders", d.folders.map((f) => listItem(`dir:${d.path}/${f}`))),
    section("Files", d.files.map((f) => listItem(f))),
  ].filter(Boolean);
}

async function runImpact(id, direction, box) {
  fill(box, h("span", { class: "spin" }));
  try {
    const r = await api(`/p/${P()}/impact?target=${encodeURIComponent(id)}&direction=${direction}`);
    if (r.error) { fill(box, h("div", { class: "card" }, "Could not analyze this target.")); return; }
    const map = new Map([[id, "#4f46e5"]]);
    const edges = new Set();
    Object.entries(r.levels).forEach(([d, lv]) => Object.entries(lv).forEach(([sid, v]) => { map.set(sid, RISK_COLORS[d]); edges.add(direction === "upstream" ? `${sid}|${v.via}` : `${v.via}|${sid}`); }));
    (r.seeds || []).forEach((s) => { if (!map.has(s)) map.set(s, "#4f46e5"); });
    Object.keys(r.possible || {}).forEach((sid) => { if (!map.has(sid)) map.set(sid, "#a8a29e"); });
    setHighlight("impact", `${direction === "upstream" ? "Impact of" : "Dependencies of"} ${short(id)}`, map, edges);
    view.fitTo(new Set(map.keys()));
    const count = (d) => Object.keys(r.levels[d] || {}).length;
    fill(box, h("div", { class: "card" },
      h("div", { class: "row wrap" }, h("span", { class: "badge " + r.risk.toLowerCase() }, r.risk + " RISK"), h("span", { class: "muted small" }, `${r.affected} affected · ${r.processes.length} flow(s)`), !r.fresh ? h("span", { class: "badge warn" }, "stale") : null),
      [1, 2, 3].map((d) => count(d) ? h("div", { class: "row small", style: { marginTop: "4px" } }, h("span", { class: "dot", style: { background: RISK_COLORS[d] } }), h("span", {}, `${["", "Will break", "Likely affected", "May need testing"][d]}: ${count(d)}`)) : null),
      ...[1, 2, 3].map((d) => Object.entries(r.levels[d] || {}).slice(0, 12).map(([sid, v]) => listItem(sid, `${v.type.toLowerCase()} · ${v.conf}`))),
      Object.keys(r.tests || {}).length ? [h("h4", {}, `Tests that exercise this (${Object.keys(r.tests).length})`), Object.keys(r.tests).slice(0, 10).map((t) => listItem(t))] : null,
      h("button", { class: "btn small", style: { marginTop: "6px" }, on: { click: clearHighlight } }, "Clear highlight")));
  } catch (e) { fill(box, h("div", { class: "card" }, e.message)); }
}

async function runTrace(from, to, box) {
  if (!to.trim()) return;
  fill(box, h("span", { class: "spin" }));
  try {
    const r = await api(`/p/${P()}/trace?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to.trim())}`);
    if (r.error) { fill(box, h("div", { class: "card" }, "Pick a single symbol: ", (r.to || []).slice(0, 8).map((c) => h("button", { class: "btn small", style: { margin: "2px" }, on: { click: () => runTrace(from, c.id, box) } }, c.qname)))); return; }
    if (!r.path.length) { fill(box, h("div", { class: "card" }, "No call path found within 8 hops.", r.unresolved_from_start?.length ? h("div", { class: "muted small" }, `The missing link may be one of ${r.unresolved_from_start.length} calls the graph could not resolve.`) : null)); return; }
    const ids = r.path.map((p) => p[0]);
    setHighlight("trace", `${short(ids[0])} → ${short(ids[ids.length - 1])}`, new Set(ids), new Set(ids.slice(0, -1).map((x, i) => `${x}|${ids[i + 1]}`)));
    view.fitTo(new Set(ids));
    fill(box, h("div", { class: "card" }, h("strong", {}, `Path (${ids.length - 1} hops)`), r.low ? h("div", { class: "muted small" }, "Uses low-confidence edges: verify each hop.") : null,
      ids.map((sid, i) => listItem(sid, i ? `call at line ${r.path[i][1]}` : "start", r.path[i][2]))));
  } catch (e) { fill(box, h("div", { class: "card" }, e.message)); }
}

// ------------------------------------------------------------------------------------ code inspector

async function showCode(file, start = null, end = null, { quiet = false } = {}) {
  const host = $("#right-code");
  S.codeView = { file, start, end };
  if (!S.codeCache.has(file)) {
    if (!quiet) fill(host, h("span", { class: "spin" }));
    try { S.codeCache.set(file, await api(`/p/${P()}/source?file=${encodeURIComponent(file)}`)); } catch (e) { fill(host, h("div", { class: "card" }, e.message)); return; }
  }
  const src = S.codeCache.get(file);
  if (S.codeView.file !== file) return;
  renderCode(src, file, start, end);
}
function renderCode(src, file, start, end) {
  const host = $("#right-code");
  if (src.error) { fill(host, h("div", { class: "empty" }, `Source unavailable: ${src.error}`)); return; }
  const cites = S.citations.map((c) => h("span", { class: "badge", style: { cursor: "pointer", margin: "2px" }, on: { click: () => openCitation(c) } }, `${c.file.split("/").pop()}:${c.start}`));
  const lines = src.lines.map((t, i) => {
    const n = i + 1, hl = start && n >= start && n <= (end || start);
    return h("div", { class: "ln" + (hl ? " hl" : ""), dataset: { n } }, h("span", { class: "no" }, n), h("span", { class: "tx" }));
  });
  const wrap = h("div", { class: "code mono" }, lines);
  src.lines.forEach((t, i) => { lines[i].lastChild.innerHTML = highlightLine(t, src.lang) || " "; });
  fill(host, 
    h("div", { class: "code-head" }, h("div", { class: "row" }, h("strong", { class: "grow", style: { overflow: "hidden", textOverflow: "ellipsis" } }, file), h("span", { class: "muted small" }, `${src.total} lines`),
      h("button", { class: "btn small", on: { click: () => navigator.clipboard.writeText(file).then(() => toast("Path copied")) } }, "Copy path")),
      start ? h("div", { class: "muted small" }, `Selected: lines ${start}${end && end !== start ? `–${end}` : ""}`) : null,
      S.citations.length ? h("div", { style: { marginTop: "4px" } }, h("span", { class: "muted small" }, "AI citations: "), cites, h("button", { class: "btn small", on: { click: () => { S.citations = []; renderCode(src, file, start, end); } } }, "Clear")) : null),
    src.truncated ? h("div", { class: "hint" }, "Showing the first 3000 lines.") : null, wrap);
  if (start) { const target = lines[Math.max(0, start - 4)]; target && target.scrollIntoView({ block: "start" }); }
}
function openCitation(c) {
  const id = c.id && S.nodes.has(c.id) ? c.id : null;
  if (id) selectNode(id, { focus: true, tab: "code" });
  else { const fileNode = S.nodes.get(c.file); if (fileNode) selectNode(c.file, { focus: true, tab: null }); }
  showCode(c.file, c.start, c.end);
  setTab("right", "code");
}

// ------------------------------------------------------------------------------------ flows and clusters panels

function renderFlows() {
  const host = $("#right-flows");
  const q = h("input", { type: "search", class: "plain", placeholder: "Filter processes…", "aria-label": "Filter processes" });
  const tests = h("input", { type: "checkbox", checked: S.showTests });
  const list = h("div");
  const draw = () => {
    S.showTests = tests.checked;
    const f = q.value.toLowerCase();
    const rows = S.processes.filter((p) => (S.showTests || !p.is_test) && p.name.toLowerCase().includes(f));
    fill(list, rows.length ? rows.map((p) => h("div", { class: "card", style: { cursor: "pointer" }, on: { click: () => openProcess(p.id) }, title: "Click to highlight in the graph" },
      h("div", { style: { fontWeight: 600, marginBottom: "4px" } }, p.name),
      h("div", { class: "row wrap" }, h("span", { class: "badge" }, `${p.steps} steps`), h("span", { class: "badge" }, `${p.clusters} cluster${p.clusters === 1 ? "" : "s"}`), h("span", { class: "badge " + (p.cross ? "warn" : "ok") }, p.cross ? "Cross-cluster" : "Intra-cluster"), p.is_test ? h("span", { class: "badge" }, "test") : null,
        h("span", { class: "grow" }), h("button", { class: "btn small", on: { click: (e) => { e.stopPropagation(); flowModal(ctx(), p); } } }, "View"))))
      : h("div", { class: "empty" }, S.processes.length ? "No processes match." : "No processes detected. Flows are traced from entry points that reach at least two other symbols."));
  };
  q.addEventListener("input", draw);
  tests.addEventListener("change", draw);
  draw();
  const real = S.processes.filter((p) => !p.is_test);
  fill(host, 
    h("div", { class: "muted small", style: { marginBottom: "6px" } }, `${S.processes.length} process${S.processes.length === 1 ? "" : "es"} detected`), q,
    h("label", { class: "check small" }, tests, "Show test flows"),
    real.length > 1 ? h("button", { class: "btn small", style: { margin: "4px 0 8px" }, on: { click: () => flowModal(ctx(), real) } }, `Full process map (${real.length} combined)`) : null, list);
}

async function openProcess(id) {
  setTab("right", "flows");
  try {
    const p = await api(`/p/${P()}/process?id=${id}`);
    if (p.error) return;
    highlightFlow(p);
  } catch (e) { toast(e.message, "bad"); }
}

function renderClusters() {
  const host = $("#right-clusters");
  const colour = h("input", { type: "checkbox", checked: S.prefs.colorBy === "cluster", on: { change: (e) => setPrefs({ colorBy: e.target.checked ? "cluster" : "kind" }) } });
  fill(host, h("label", { class: "check" }, colour, "Colour the graph by cluster"),
    h("div", { class: "muted small", style: { margin: "4px 0 8px" } }, "Functional areas found by community detection: symbols that call each other a lot."),
    S.clusters.length ? S.clusters.map((c) => h("div", { class: "card", style: { cursor: "pointer" }, on: { click: () => highlightCluster(c) } },
      h("div", { class: "row wrap" }, h("span", { class: "dot", style: { background: CLUSTER_PALETTE[(+c.id.slice(1) - 1) % CLUSTER_PALETTE.length] } }), h("strong", { class: "grow" }, c.label), h("span", { class: "badge " + (c.cohesion === "high" ? "ok" : c.cohesion === "med" ? "warn" : "bad") }, `cohesion ${c.cohesion}`)),
      h("div", { class: "muted small" }, `${c.size} symbols in ${c.files.length} file(s)`))) : h("div", { class: "empty" }, "No clusters yet."));
}
function highlightCluster(c) { setHighlight("cluster", c.label, new Set(c.members)); view.fitTo(new Set(c.members)); }

function renderTabBadges() {
  const flows = $('#right .tab[data-tab="flows"]'), cl = $('#right .tab[data-tab="clusters"]');
  if (flows) flows.textContent = `Flows${S.processes.length ? ` ${S.processes.filter((p) => !p.is_test).length}` : ""}`;
  if (cl) cl.textContent = `Clusters${S.clusters.length ? ` ${S.clusters.length}` : ""}`;
}

// ------------------------------------------------------------------------------------ explorer and filters

function buildTree() {
  const root = { name: "", path: "", dirs: new Map(), files: [] };
  S.data.nodes.forEach((n) => {
    if (n.k !== "file") return;
    const parts = n.f.split("/");
    let cur = root;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!cur.dirs.has(parts[i])) cur.dirs.set(parts[i], { name: parts[i], path: parts.slice(0, i + 1).join("/"), dirs: new Map(), files: [] });
      cur = cur.dirs.get(parts[i]);
    }
    cur.files.push(n);
  });
  S.tree = root;
  S.expanded = new Set();
  const files = S.data.nodes.filter((n) => n.k === "file").length;
  if (files <= 80) { const walk = (d) => { d.dirs.forEach((c) => { S.expanded.add(c.path); walk(c); }); }; walk(root); }
  else root.dirs.forEach((c) => S.expanded.add(c.path));
}

let treeFilter = "";
function renderExplorer() {
  const host = $("#left-explorer");
  const q = h("input", { type: "search", class: "plain", placeholder: "Search files…", "aria-label": "Search files", value: treeFilter, on: { input: debounce((e) => { treeFilter = e.target.value.toLowerCase(); renderTree(); }, 120) } });
  const tree = h("div", { id: "tree", role: "tree", style: { marginTop: "8px" } });
  fill(host, q, tree);
  renderTree();
}

function renderTree() {
  const tree = $("#tree");
  if (!tree || !S.tree) return;
  const out = [];
  const row = (depth, kind, label, { id, twisty = "", count = "", click }) => h("div", { class: "tree-node" + (id && id === S.selected ? " sel" : ""), style: { paddingLeft: `${4 + depth * 14}px` }, role: "treeitem", on: { click }, title: id || label },
    h("span", { class: "twisty" }, twisty), h("span", { class: "dot " + kind }), h("span", { class: "lbl" }, label), count ? h("span", { class: "cnt" }, count) : null);
  if (treeFilter) {
    const files = S.data.nodes.filter((n) => n.k === "file" && n.f.toLowerCase().includes(treeFilter)).slice(0, 300);
    fill(tree, ...(files.length ? files.map((f) => row(0, "file", f.f, { id: f.id, click: () => selectNode(f.id, { focus: true }) })) : [h("div", { class: "dd-empty" }, "No files match.")]));
    return;
  }
  const walk = (dir, depth) => {
    [...dir.dirs.values()].sort((a, b) => a.name.localeCompare(b.name)).forEach((d) => {
      const open = S.expanded.has(d.path);
      out.push(row(depth, "folder", d.name, { id: `dir:${d.path}`, twisty: open ? "▾" : "▸", click: () => { open ? S.expanded.delete(d.path) : S.expanded.add(d.path); selectNode(`dir:${d.path}`, { tab: "details" }); } }));
      if (open) walk(d, depth + 1);
    });
    dir.files.sort((a, b) => a.l.localeCompare(b.l)).forEach((f) => {
      const syms = S.byFile.get(f.f) || [];
      const open = S.expanded.has(f.f);
      out.push(row(depth, "file", f.l, { id: f.id, twisty: syms.length ? (open ? "▾" : "▸") : "", count: syms.length ? String(syms.length) : "",
        click: (e) => { if (syms.length && e.offsetX < 40 + depth * 14) { open ? S.expanded.delete(f.f) : S.expanded.add(f.f); renderTree(); } selectNode(f.id, { focus: true }); } }));
      if (open) syms.forEach((s) => out.push(row(depth + 1, s.k, s.l.includes(".") ? s.l.split(".").slice(-1)[0] : s.l, { id: s.id, click: () => selectNode(s.id, { focus: true }) })));
    });
  };
  walk(S.tree, 0);
  fill(tree, ...(out.length ? out : [h("div", { class: "empty" }, "No files loaded.")]));
  const sel = tree.querySelector(".sel");
  sel && sel.scrollIntoView({ block: "nearest" });
}

function revealInTree(id) {
  const n = S.nodes.get(id) || S.nodes.get(fileOf(id));
  if (!n || !S.tree) return;
  const parts = n.f.split("/");
  for (let i = 1; i < parts.length; i++) S.expanded.add(parts.slice(0, i).join("/"));
  if (n.k !== "file" && n.k !== "folder") S.expanded.add(n.f);
}

function renderFilters() {
  const host = $("#left-filters");
  if (!S.data) { fill(host, h("div", { class: "empty" }, "Load a graph to filter it.")); return; }
  const kc = {}, ec = {};
  S.data.nodes.forEach((n) => { kc[n.k] = (kc[n.k] || 0) + 1; });
  S.data.edges.forEach((e) => { ec[e[2]] = (ec[e[2]] || 0) + 1; });
  const toggle = (set, key) => (e) => { e.target.checked ? set.add(key) : set.delete(key); view.setFilters({}); };
  const depth = h("input", { type: "range", min: "0", max: "5", value: String(view.filters.depth), "aria-label": "Focus depth", on: { input: (e) => { view.setFilters({ depth: +e.target.value }); depthLbl.textContent = depthText(); } } });
  const depthText = () => (view.filters.depth === 0 ? "Off" : `${view.filters.depth} hop${view.filters.depth > 1 ? "s" : ""}`);
  const depthLbl = h("span", { class: "muted small" }, depthText());
  fill(host, 
    h("h4", { style: { marginTop: 0 } }, "Node types"), h("div", { class: "muted small" }, "Toggle visibility of node types in the graph"),
    Object.keys(KIND_LABELS).filter((k) => kc[k]).map((k) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: view.filters.kinds.has(k), on: { change: toggle(view.filters.kinds, k) } }), h("span", { class: "dot " + k }), h("span", { class: "grow" }, KIND_LABELS[k]), h("span", { class: "muted small" }, kc[k]))),
    h("h4", {}, "Edge types"), h("div", { class: "muted small" }, "Toggle visibility of relationship types"),
    Object.keys(EDGE_LABELS).filter((k) => ec[k]).map((k) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: view.filters.edges.has(k), on: { change: toggle(view.filters.edges, k) } }), h("span", { style: { width: "16px", height: "3px", background: EDGE_COLORS[k], display: "inline-block" } }), h("span", { class: "grow" }, EDGE_LABELS[k]), h("span", { class: "muted small" }, ec[k]))),
    h("h4", {}, "Options"),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: view.filters.hideLeaves, on: { change: (e) => view.setFilters({ hideLeaves: e.target.checked }) } }), "Hide leaf nodes"),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: view.filters.hideLow, on: { change: (e) => view.setFilters({ hideLow: e.target.checked }) } }), "Hide low-confidence edges"),
    h("h4", {}, "Focus depth"), h("div", { class: "muted small" }, "Show nodes within N hops of the selection"),
    h("div", { class: "row" }, depth, depthLbl),
    h("div", { class: "muted small", id: "depth-hint" }, S.selected ? "" : "Select a node to apply the depth filter."),
    h("h4", {}, "Colour by"),
    h("select", { on: { change: (e) => setPrefs({ colorBy: e.target.value }) } }, [["kind", "Node type"], ["cluster", "Cluster"]].map(([v, l]) => h("option", { value: v, selected: S.prefs.colorBy === v }, l))));
}
function syncFilterUI() { renderFilters(); }

// ------------------------------------------------------------------------------------ tabs

function setTab(side, name) {
  const root = side === "left" ? $("#left") : $("#right");
  $$(".tab", root).forEach((t) => { const on = t.dataset.tab === name; t.classList.toggle("active", on); t.setAttribute("aria-selected", String(on)); });
  $$(".pane", root).forEach((p) => { p.hidden = p.id !== `${side}-${name}`; });
  if (side === "right" && name === "ai") ensureChat();
  if (side === "left" && name === "filters") renderFilters();
}
$$(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.closest("#left") ? "left" : "right", t.dataset.tab)));

function ensureChat() {
  if (S.chat || !S.project) return;
  S.chat = new ChatPanel($("#right-ai"), {
    project: () => S.project, files: () => new Set(S.data ? S.data.nodes.filter((n) => n.k === "file").map((n) => n.f) : []),
    onCitations: (list) => { S.citations = list; if (list.length) { const ids = new Set(list.map((c) => c.id).filter((i) => i && S.nodes.has(i))); if (ids.size) setHighlight("ai", "Nexus AI references", ids); } },
    openCitation, openSettings: () => settingsModal(ctx()),
  });
}

// ------------------------------------------------------------------------------------ search

const search = $("#search"), results = $("#search-results");
let searchSel = -1, searchHits = [], searchSeq = 0;

async function doSearch() {
  const q = search.value.trim();
  const seq = ++searchSeq;
  if (!q || !S.project) { results.hidden = true; if (S.hl && S.hl.source === "search") clearHighlight(); return; }
  try {
    const r = await api(`/p/${P()}/search?q=${encodeURIComponent(q)}&limit=25`);
    if (seq !== searchSeq) return;
    searchHits = r.results;
    searchSel = searchHits.length ? 0 : -1;
    drawSearch();
    if (searchHits.length) setHighlight("search", `“${q}”`, new Set(searchHits.map((x) => x.id).filter((i) => S.nodes.has(i))));
    else if (S.hl && S.hl.source === "search") clearHighlight();
  } catch (e) { fill(results, h("div", { class: "dd-empty" }, e.message)); results.hidden = false; }
}
function drawSearch() {
  results.hidden = false;
  if (!searchHits.length) { fill(results, h("div", { class: "dd-empty" }, `No nodes found for "${search.value.trim()}"`), h("div", { class: "hint" }, "Try type:class, a path like src/utils, or a name fragment.")); return; }
  fill(results, ...searchHits.map((r, i) => h("div", { class: "dd-item" + (i === searchSel ? " sel" : ""), role: "option", on: { mousedown: (e) => { e.preventDefault(); pickSearch(i); }, mouseenter: () => { searchSel = i; $$(".dd-item", results).forEach((el, j) => el.classList.toggle("sel", j === i)); } } },
    h("span", { class: "dot " + r.kind }), h("div", { class: "grow" }, h("div", { class: "t" }, r.label), h("div", { class: "s" }, r.kind === "folder" ? r.file : `${r.file}${r.line ? ":" + r.line : ""}`)), h("span", { class: "badge" }, r.kind))),
    h("div", { class: "hint" }, "↑↓ to move · Enter to open · Esc to close"));
}
function pickSearch(i) {
  const r = searchHits[i];
  if (!r) return;
  results.hidden = true;
  search.blur();                       // hand the keyboard back to the graph shortcuts
  clearHighlight();
  if (S.nodes.has(r.id)) selectNode(r.id, { focus: true });
  else selectNode(r.id, { tab: "details" });
}
search.addEventListener("input", debounce(doSearch, 140));
search.addEventListener("focus", () => { if (search.value.trim() && searchHits.length) results.hidden = false; });
search.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); searchSel = Math.min(searchHits.length - 1, searchSel + 1); drawSearch(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); searchSel = Math.max(0, searchSel - 1); drawSearch(); }
  else if (e.key === "Enter") { e.preventDefault(); pickSearch(searchSel); }
  else if (e.key === "Escape") { e.stopPropagation(); results.hidden = true; search.blur(); if (S.hl && S.hl.source === "search") clearHighlight(); }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-wrap")) results.hidden = true;
  if (!e.target.closest(".menu-anchor")) { $("#project-menu").hidden = true; $("#btn-project").setAttribute("aria-expanded", "false"); }
});

// ------------------------------------------------------------------------------------ header buttons, shortcuts, resizer

const ctx = () => ({
  project: () => S.project, prefs: S.prefs, setPrefs, lastQuery: S.lastQuery, hasNode: (id) => S.nodes.has(id),
  select: (id, o) => selectNode(id, o), highlight: (src, label, ids) => setHighlight(src, label, ids), highlightFlow,
  waitForJob, refreshProjects, openProject,
});
$("#btn-help").addEventListener("click", () => helpModal(view.ready ? view.visibleCounts() : null));
$("#btn-settings").addEventListener("click", () => settingsModal(ctx()));
$("#btn-ai").addEventListener("click", () => { setTab("right", "ai"); setTimeout(() => $("#right-ai textarea")?.focus(), 30); });
$("#btn-changes").addEventListener("click", () => S.project && changesModal(ctx()));
$("#fab-query").addEventListener("click", () => S.project && queryModal(ctx()));

document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); search.focus(); search.select(); return; }
  if (e.key === "Escape") {
    if (hasModal()) { closeTopModal(); return; }
    if (!results.hidden) { results.hidden = true; return; }
    if (!$("#project-menu").hidden) { $("#project-menu").hidden = true; return; }
    if (S.hl) { clearHighlight(); return; }
    if (S.selected) selectNode(null);
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey || hasModal()) return;
  if (e.key === "/") { e.preventDefault(); search.focus(); }
  else if (e.key === "?") helpModal(view.ready ? view.visibleCounts() : null);
  else if (e.key.toLowerCase() === "f") view.fit();
  else if (e.key === "+" || e.key === "=") view.zoomIn();
  else if (e.key === "-") view.zoomOut();
  else if (e.key === "1") setMode("force");
  else if (e.key === "2") setMode("tree");
  else if (e.key === "3") setMode("radial");
});

(function resizer() {
  const bar = $("#resizer");
  const set = (w) => document.documentElement.style.setProperty("--right", `${Math.max(300, Math.min(760, w))}px`);
  set(S.prefs.rightWidth);
  bar.addEventListener("pointerdown", (e) => {
    bar.setPointerCapture(e.pointerId);
    bar.classList.add("drag");
    const move = (ev) => { const w = window.innerWidth - ev.clientX; set(w); S.prefs.rightWidth = Math.max(300, Math.min(760, w)); view.ready && view.sigma.refresh(); };
    const up = () => { bar.classList.remove("drag"); bar.removeEventListener("pointermove", move); bar.removeEventListener("pointerup", up); savePrefs(); };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up);
  });
})();

// ------------------------------------------------------------------------------------ live refresh

function restartPolling() {
  clearInterval(S.polling);
  if (!S.prefs.autoRefresh) return;
  S.polling = setInterval(async () => {
    if (document.hidden || !S.project || S.indexing || hasModal()) return;
    try {
      const before = S.status?.meta?.lastLink;
      const st = await api(`/p/${P()}/status?refresh=1`);
      S.status = st;
      if (before && st.meta.lastLink !== before) {
        toast("Graph updated: files changed");
        await loadGraph({ overview: S.data?.overview }, S.loadToken, true);
        loadSideData(S.loadToken);
      } else renderStatus();
    } catch { /* server restarting or project removed: try again next tick */ }
  }, 8000);
}

// ------------------------------------------------------------------------------------ render all

function renderAll() {
  renderToolbar(); renderLegend(); renderStatus(); renderExplorer(); renderFilters(); renderFlows(); renderClusters(); renderTabBadges();
  renderDetails(S.selected);
}

// ------------------------------------------------------------------------------------ boot

(async function boot() {
  applyTheme();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
  renderToolbar(); renderStatus(); renderDetails(null);
  try {
    await refreshProjects();
    const wanted = new URLSearchParams(location.search).get("project");
    const pick = S.projects.find((p) => p.name === wanted)?.name || S.home || S.projects[0]?.name;
    if (!pick) { landing(); return; }
    await openProject(pick);
    restartPolling();
  } catch (e) {
    showOverlay(h("div", { class: "box" }, h("h3", {}, "Cannot reach the CodeAtlas server"), h("p", { class: "muted" }, e.message), h("button", { class: "btn", on: { click: () => location.reload() } }, "Reload")));
  }
})();
