// Modal dialogs: help, settings, analyze-a-repository, query console, changes, process flow, parse errors.

import { fill, h, api, toast, short, KIND_COLORS, KIND_LABELS, EDGE_COLORS, EDGE_LABELS, fmtAge } from "./util.js";
import { renderFlow, combineProcesses, flowToolbar } from "./flow.js";

const modalsHost = () => document.getElementById("modals");
let stack = [];

export function closeTopModal() {
  const top = stack.pop();
  if (top) { top.remove(); top.onClose && top.onClose(); }
  return !!top;
}
export const hasModal = () => stack.length > 0;

export function openModal(title, body, { wide = false, footer = null, onClose = null } = {}) {
  const close = () => { const i = stack.indexOf(scrim); if (i >= 0) stack.splice(i, 1); scrim.remove(); onClose && onClose(); };
  const scrim = h("div", { class: "scrim", role: "dialog", "aria-modal": "true", "aria-label": title, on: { mousedown: (e) => { if (e.target === scrim) close(); } } },
    h("div", { class: "modal" + (wide ? " wide" : "") },
      h("div", { class: "modal-h" }, h("h2", {}, title), h("button", { class: "btn icon", "aria-label": "Close", on: { click: close } }, "✕")),
      h("div", { class: "modal-b" }, body),
      footer ? h("div", { class: "modal-f" }, footer) : null));
  scrim.onClose = onClose;
  modalsHost().append(scrim);
  stack.push(scrim);
  const first = scrim.querySelector("textarea, input, button.primary");
  first && setTimeout(() => first.focus(), 30);
  return { close, el: scrim };
}

// ------------------------------------------------------------------------------------ help

export function helpModal(counts) {
  const tabs = {
    Overview: () => h("div", {},
      h("h3", {}, "What is CodeAtlas Explorer?"),
      h("p", {}, "An interactive graph of your codebase. Every folder, file, class, function and method is a node. Calls, imports, inheritance and references are edges. The graph is built by parsing your code (not guessed), and it refreshes itself when files change."),
      counts ? h("p", { class: "muted" }, `Loaded: ${counts.nodes} nodes · ${counts.edges} edges`) : null,
      h("h4", {}, "Three ways to explore"),
      h("ul", {}, h("li", {}, "Click nodes to inspect them: callers, callees, source, flows."), h("li", {}, "Search by name, path or type (Ctrl/⌘ K)."), h("li", {}, "Ask Nexus AI a question in plain language.")),
      h("h4", {}, "Navigation"),
      h("ul", {}, h("li", {}, "Scroll to zoom, drag the background to pan, drag a node to move it."), h("li", {}, "Double-click a node to focus its neighbourhood."), h("li", {}, "Hover a node to light up its neighbours."))),
    Shortcuts: () => h("table", { class: "res" }, h("tbody", {}, [
      ["Search nodes", "Ctrl/⌘ K  or  /"], ["Deselect · close dialogs · clear highlights", "Esc"], ["Fit graph to screen", "F"], ["Zoom in / out", "+  /  −"],
      ["Force / Tree / Radial layout", "1  /  2  /  3"], ["Toggle this help", "?"], ["Run query in the console", "Ctrl/⌘ Enter"],
    ].map(([a, k]) => h("tr", {}, h("td", {}, a), h("td", {}, h("span", { class: "kbd" }, k)))))),
    "Graph & nodes": () => h("div", {},
      h("h4", {}, "Node types"), h("div", { class: "grid2" }, Object.entries(KIND_LABELS).map(([k, l]) => h("div", { class: "row" }, h("span", { class: "dot " + k }), l))),
      h("h4", {}, "Edge types"), h("div", { class: "grid2" }, Object.entries(EDGE_LABELS).map(([k, l]) => h("div", { class: "row" }, h("span", { style: { width: "18px", height: "3px", background: EDGE_COLORS[k], display: "inline-block" } }), l))),
      h("p", { class: "muted" }, "Node size reflects how connected a node is. Edges point from the caller or importer to the callee or imported file. Dashed or pale edges are low-confidence guesses. A red ring marks a file with a syntax error."),
      h("p", { class: "muted" }, "Layouts: Force (clusters by connection), Sequential tree (folder → file → symbol), Radial (rings by depth).")),
    "Search & filter": () => h("div", {},
      h("h4", {}, "Search syntax"),
      h("table", { class: "res" }, h("tbody", {}, [["twoSum", "match by name fragment (camelCase and snake_case aware)"], ["src/utils", "match by path fragment"], ["type:class", "filter by node type: class, function, method, file, folder, module"],
        ["type:function path:api/", "combine filters"], ["type:class Shape", "words and filters together"]].map(([q, d]) => h("tr", {}, h("td", {}, h("code", {}, q)), h("td", {}, d))))),
      h("h4", {}, "Filters panel"), h("p", {}, "Toggle node and edge types, hide leaf nodes, hide low-confidence edges, and limit the graph to N hops around the selected node.")),
    "Status bar": () => h("ul", {}, h("li", {}, "Ready: the graph is loaded and interactive."), h("li", {}, "Nodes · Edges: what is visible out of the total."), h("li", {}, "Indexed: when the graph was last built. If files changed since, a Re-index link appears (the explorer also refreshes automatically)."), h("li", {}, "Parse errors: files with syntax errors. Their symbols are recovered but they have no call edges until fixed.")),
    "Nexus AI": () => h("div", {},
      h("p", {}, "Nexus AI answers questions by calling the same tools you have here: search, symbol context, impact, trace, SQL and source reading. Every answer cites code you can click."),
      h("p", { class: "muted" }, "It needs an Anthropic API key (Settings). Your key stays on this machine, in the server, never in the browser."),
      h("h4", {}, "Try asking"), h("ul", {}, h("li", {}, "Which files depend on the auth module?"), h("li", {}, "What are the most connected components?"), h("li", {}, "Trace how a request reaches the database layer"))),
  };
  const names = Object.keys(tabs);
  const content = h("div", { style: { minHeight: "280px" } });
  const bar = h("div", { class: "tabs", style: { margin: "-4px -4px 10px" } });
  const show = (n) => { fill(content, tabs[n]()); [...bar.children].forEach((b) => b.classList.toggle("active", b.textContent === n)); };
  names.forEach((n) => bar.append(h("button", { class: "tab", on: { click: () => show(n) } }, n)));
  show(names[0]);
  openModal("Help & reference", h("div", {}, bar, content));
}

// ------------------------------------------------------------------------------------ settings

export async function settingsModal(ctx) {
  const s = await api("/settings");
  const key = h("input", { type: "password", placeholder: s.has_key ? `Saved ${s.key_hint} (${s.key_source}). Paste a new key to replace it.` : "sk-ant-…", autocomplete: "off" });
  const model = h("select", {}, s.models.map((m) => h("option", { value: m, selected: m === s.model }, m)));
  const colorBy = h("select", {}, [["kind", "Node type"], ["cluster", "Cluster"]].map(([v, l]) => h("option", { value: v, selected: ctx.prefs.colorBy === v }, l)));
  const auto = h("input", { type: "checkbox", checked: ctx.prefs.autoRefresh });
  const labels = h("select", {}, [["0.4", "Fewer labels"], ["0.9", "Normal"], ["2", "More labels"]].map(([v, l]) => h("option", { value: v, selected: String(ctx.prefs.labelDensity) === v }, l)));
  const theme = h("select", {}, [["auto", "System"], ["light", "Light"], ["dark", "Dark"]].map(([v, l]) => h("option", { value: v, selected: ctx.prefs.theme === v }, l)));
  const save = h("button", { class: "btn primary", on: { click: async () => {
    try {
      const body = { model: model.value };
      if (key.value.trim()) body.anthropic_api_key = key.value.trim();
      await api("/settings", { method: "POST", body });
      ctx.setPrefs({ colorBy: colorBy.value, autoRefresh: auto.checked, labelDensity: parseFloat(labels.value), theme: theme.value });
      toast("Settings saved");
      m.close();
    } catch (e) { toast(e.message, "bad"); }
  } } }, "Save");
  const m = openModal("Settings", h("div", {},
    h("h4", {}, "Nexus AI"),
    h("div", { class: "small muted", style: { marginBottom: "6px" } }, "The key is stored on this machine in ~/.codeatlas/config.json (owner-readable only) and used by the local server. It is never sent to the browser or to anyone except Anthropic."),
    h("label", {}, "Anthropic API key", key), h("div", { style: { height: "8px" } }),
    h("label", {}, "Model", model),
    h("h4", {}, "Display"),
    h("label", {}, "Theme", theme), h("div", { style: { height: "8px" } }),
    h("label", {}, "Colour nodes by", colorBy), h("div", { style: { height: "8px" } }),
    h("label", {}, "Label density", labels),
    h("h4", {}, "Behaviour"),
    h("label", { class: "check" }, auto, "Refresh the graph automatically when files change")),
    { footer: [h("button", { class: "btn", on: { click: () => m.close() } }, "Cancel"), save] });
}

// ------------------------------------------------------------------------------------ analyze a repository

export function analyzeModal(ctx) {
  const path = h("input", { type: "text", placeholder: "/Users/you/projects/my-repo", autocomplete: "off", "aria-label": "Folder path" });
  const name = h("input", { type: "text", placeholder: "Name (optional)" });
  const status = h("div", { class: "muted small", style: { marginTop: "10px" } });
  const bar = h("div", { class: "progress", hidden: true }, h("div"));
  const go = h("button", { class: "btn primary", on: { click: start } }, "Analyze");
  const m = openModal("Analyze a repository", h("div", {},
    h("p", { class: "muted" }, "Index any folder on this machine. Your source folder is never modified: the graph is stored under ~/.codeatlas."),
    h("label", {}, "Folder", path), h("div", { style: { height: "8px" } }), h("label", {}, "Name", name), bar, status), { footer: [go] });
  async function start() {
    go.disabled = true;
    bar.hidden = false;
    status.textContent = "Starting…";
    try {
      const r = await api("/projects", { method: "POST", body: { path: path.value, name: name.value } });
      await ctx.waitForJob(r.job, (j) => { status.textContent = j.message; });
      toast("Repository analyzed");
      m.close();
      await ctx.refreshProjects();
      ctx.openProject(r.project);
    } catch (e) { status.textContent = e.message; status.style.color = "var(--bad)"; go.disabled = false; bar.hidden = true; }
  }
  path.addEventListener("keydown", (e) => { if (e.key === "Enter") start(); });
}

// ------------------------------------------------------------------------------------ query console

const EXAMPLES = [
  ["All functions", "SELECT id, kind, file, start FROM symbols WHERE kind = 'function' ORDER BY file, start"],
  ["All classes", "SELECT id, kind, file, start FROM symbols WHERE kind = 'class' ORDER BY file"],
  ["Function calls", "SELECT src, dst, line, conf FROM edges WHERE type = 'CALLS' AND dst NOT LIKE '?%' LIMIT 200"],
  ["Import dependencies", "SELECT src, dst FROM edges WHERE type = 'IMPORTS' AND dst NOT LIKE 'ext:%' ORDER BY src"],
  ["Most called symbols", "SELECT dst AS id, COUNT(*) AS callers FROM edges WHERE type IN ('CALLS','REFERENCES') AND dst NOT LIKE '?%' GROUP BY dst ORDER BY callers DESC LIMIT 25"],
  ["Unresolved calls", "SELECT src, name, line, reason FROM unresolved ORDER BY file, line LIMIT 100"],
  ["Low-confidence edges", "SELECT src, dst, line FROM edges WHERE conf = 'low' AND dst NOT LIKE '?%' LIMIT 100"],
  ["Largest functions", "SELECT id, (end - start + 1) AS lines, file FROM symbols WHERE kind IN ('function','method') ORDER BY lines DESC LIMIT 25"],
];

export async function queryModal(ctx) {
  const schema = await api(`/p/${encodeURIComponent(ctx.project())}/schema`).catch(() => ({}));
  const ta = h("textarea", { class: "sql", spellcheck: "false", "aria-label": "SQL query" });
  ta.value = ctx.lastQuery || EXAMPLES[0][1];
  const sel = h("select", { style: { width: "auto" }, on: { change: () => { if (sel.value !== "") ta.value = EXAMPLES[+sel.value][1]; } } },
    h("option", { value: "" }, "Examples…"), EXAMPLES.map(([l], i) => h("option", { value: i }, l)));
  const out = h("div", { style: { marginTop: "10px", overflow: "auto", maxHeight: "40vh" } });
  const info = h("span", { class: "muted small" });
  let last = null;
  const hl = h("button", { class: "btn", hidden: true, on: { click: () => {
    const ids = new Set();
    last.rows.forEach((r) => r.forEach((c) => { if (typeof c === "string" && ctx.hasNode(c)) ids.add(c); }));
    if (!ids.size) return toast("No result cell matches a node id");
    ctx.highlight("query", "Query result", ids); m.close();
  } } }, "Highlight in graph");
  const csv = h("button", { class: "btn", hidden: true, on: { click: () => {
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const text = [last.columns.map(q).join(","), ...last.rows.map((r) => r.map(q).join(","))].join("\n");
    navigator.clipboard.writeText(text).then(() => toast("Copied as CSV"), () => toast("Copy failed", "bad"));
  } } }, "Copy CSV");
  async function run() {
    ctx.lastQuery = ta.value;
    fill(out, h("span", { class: "spin" }));
    try {
      const r = await api(`/p/${encodeURIComponent(ctx.project())}/sql`, { method: "POST", body: { query: ta.value } });
      if (r.error) { fill(out, h("div", { class: "card", style: { borderColor: "var(--bad)" } }, r.error)); info.textContent = ""; hl.hidden = csv.hidden = true; return; }
      last = r;
      const shown = r.rows.slice(0, 50);
      info.textContent = r.rows.length ? `Showing ${shown.length} of ${r.rows.length}${r.truncated ? "+" : ""} rows` : "0 rows";
      hl.hidden = csv.hidden = !r.rows.length;
      fill(out, r.rows.length ? h("table", { class: "res" }, h("thead", {}, h("tr", {}, r.columns.map((c) => h("th", {}, c)))),
        h("tbody", {}, shown.map((row) => h("tr", {}, row.map((c) => h("td", { title: String(c ?? "") }, c === null ? "∅" : String(c))))))) : h("div", { class: "empty" }, "No rows."));
    } catch (e) { fill(out, h("div", { class: "card", style: { borderColor: "var(--bad)" } }, e.message)); }
  }
  ta.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); run(); } });
  const tables = Object.entries(schema).map(([t, cols]) => h("div", { class: "small" }, h("strong", {}, t), h("span", { class: "muted" }, ` (${cols.join(", ")})`)));
  const m = openModal("Query console (SQL, read-only)", h("div", {},
    h("div", { class: "row wrap", style: { marginBottom: "8px" } }, sel, h("span", { class: "muted small" }, "Ctrl/⌘ Enter to run · SELECT only")),
    ta, h("div", { class: "row", style: { margin: "8px 0" } }, h("button", { class: "btn primary", on: { click: run } }, "Run"), hl, csv, info),
    out, h("details", { style: { marginTop: "10px" } }, h("summary", { class: "muted" }, "Schema"), h("div", { style: { marginTop: "6px" } }, tables))), { wide: true });
  run();
}

// ------------------------------------------------------------------------------------ changes

export async function changesModal(ctx) {
  const body = h("div", {}, h("span", { class: "spin" }), " Comparing…");
  const m = openModal("Changes", body, { wide: true });
  try {
    const c = await api(`/p/${encodeURIComponent(ctx.project())}/changes`);
    const list = (title, ids, cls) => ids.length ? h("div", {}, h("h4", {}, `${title} (${ids.length})`), ids.map((id) => h("div", { class: "list-item", on: { click: () => { m.close(); ctx.select(id, { focus: true }); } } }, h("span", { class: "badge " + cls }, title.toLowerCase().slice(0, 3)), h("span", { class: "grow" }, short(id)), h("span", { class: "muted small" }, id.split("::")[0])))) : null;
    const dep = new Set(); Object.values(c.dependants).forEach((rows) => rows.forEach((r) => dep.add(r.src)));
    const all = new Map();
    [...c.modified].forEach((i) => all.set(i, "#f59e0b")); [...c.added].forEach((i) => all.set(i, "#10b981")); [...dep].forEach((i) => { if (!all.has(i)) all.set(i, "#eab308"); });
    fill(body, 
      h("div", { class: "row wrap" }, h("span", { class: "badge " + c.risk.toLowerCase() }, c.risk + " RISK"), h("span", { class: "muted" }, `vs ${c.baseline} · ${c.modified.length} modified · ${c.added.length} added · ${c.removed.length} removed · ${c.affected} dependants · ${c.processes.length} flows`),
        h("span", { class: "spacer" }), h("button", { class: "btn", on: { click: () => { ctx.highlight("changes", "Uncommitted changes", all); m.close(); } } }, "Highlight in graph")),
      !c.git ? h("p", { class: "muted small" }, "No git repository here: compared with the last index. Run git init for a commit baseline.") : null,
      Object.keys(c.files).length ? null : h("div", { class: "empty", style: { marginTop: "10px" } }, "No source files changed."),
      list("Modified", c.modified, "medium"), list("Added", c.added, "low"), list("Removed", c.removed, "critical"),
      c.tests.length ? h("div", {}, h("h4", {}, `Tests to run (${c.tests.length})`), c.tests.slice(0, 30).map((t) => h("div", { class: "list-item", on: { click: () => { m.close(); ctx.select(t, { focus: true }); } } }, h("span", { class: "grow" }, short(t)), h("span", { class: "muted small" }, t.split("::")[0])))) : null);
  } catch (e) { fill(body, h("div", { class: "card" }, e.message)); }
}

// ------------------------------------------------------------------------------------ flow diagram

export async function flowModal(ctx, procOrList) {
  const project = ctx.project();
  const many = Array.isArray(procOrList);
  const host = h("div", { style: { height: "62vh" } }, h("span", { class: "spin" }));
  const tools = h("div");
  const mermaidBtn = h("button", { class: "btn small", on: { click: () => navigator.clipboard.writeText(mermaidText).then(() => toast("Mermaid copied"), () => toast("Copy failed", "bad")) } }, "Copy Mermaid");
  let mermaidText = "";
  const title = many ? `All flows (${procOrList.length} combined)` : `Flow: ${procOrList.name}`;
  const m = openModal(title, h("div", {}, tools, host), { wide: true });
  try {
    const detail = many ? await Promise.all(procOrList.map((p) => api(`/p/${encodeURIComponent(project)}/process?id=${p.id}`))) : [await api(`/p/${encodeURIComponent(project)}/process?id=${procOrList.id}`)];
    const proc = many ? combineProcesses(detail) : detail[0];
    if (!proc) { fill(host, h("div", { class: "empty" }, "📊 Diagram too large. Try viewing individual flows instead of all of them.")); return; }
    mermaidText = many ? detail.map((d) => d.mermaid).join("\n") : detail[0].mermaid;
    const view = renderFlow(proc, { onSelect: (id) => ctx.select(id) });
    fill(tools, flowToolbar(view), h("span", { class: "muted small", style: { marginLeft: "8px" } }, `${view.size} steps · click a step to focus it, drag to pan, scroll to zoom`), mermaidBtn,
      h("button", { class: "btn small", style: { marginLeft: "6px" }, on: { click: () => { m.close(); ctx.highlightFlow(proc); } } }, "Show in graph"));
    tools.className = "row wrap";
    tools.style.marginBottom = "8px";
    fill(host, view.el);
  } catch (e) { fill(host, h("div", { class: "card" }, e.message)); }
}

export function parseErrorsModal(ctx, errors) {
  const m = openModal(`Files with parse errors (${errors.length})`, h("div", {},
    h("p", { class: "muted" }, "These files have syntax errors. Their classes and functions were recovered by a tolerant scan, but they have no call edges until the syntax is fixed."),
    errors.map(([p, e]) => h("div", { class: "list-item", on: { click: () => { m.close(); ctx.select(p, { focus: true }); } } }, h("span", { class: "dot file" }), h("span", { class: "grow" }, p), h("span", { class: "muted small" }, e)))));
}

export function skippedModal(skipped) {
  openModal(`Skipped files (${skipped.length})`, h("div", {},
    h("p", { class: "muted" }, "These files are not in the graph. Large files are skipped to keep indexing fast; add exceptions or ignores in .codeatlasignore."),
    skipped.map(([p, why]) => h("div", { class: "list-item" }, h("span", { class: "dot file" }), h("span", { class: "grow" }, p), h("span", { class: "muted small" }, why)))));
}
