// Modal dialogs: help, settings, changes, checks, process flow, parse errors, skipped files.

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
      h("h4", {}, "Two ways to explore"),
      h("ul", {}, h("li", {}, "Click nodes to inspect them: callers, callees, source, flows."), h("li", {}, "Search by name, path or type (Ctrl/⌘ K).")),
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

export function settingsModal(ctx) {
  const colorBy = h("select", {}, [["kind", "Node type"], ["cluster", "Cluster"]].map(([v, l]) => h("option", { value: v, selected: ctx.prefs.colorBy === v }, l)));
  const labels = h("select", {}, [["0.4", "Fewer labels"], ["0.9", "Normal"], ["2", "More labels"]].map(([v, l]) => h("option", { value: v, selected: String(ctx.prefs.labelDensity) === v }, l)));
  const theme = h("select", {}, [["auto", "System"], ["light", "Light"], ["dark", "Dark"]].map(([v, l]) => h("option", { value: v, selected: ctx.prefs.theme === v }, l)));
  const save = h("button", { class: "btn primary", on: { click: async () => {
    try {
      ctx.setPrefs({ colorBy: colorBy.value, labelDensity: parseFloat(labels.value), theme: theme.value });
      toast("Settings saved");
      m.close();
    } catch (e) { toast(e.message, "bad"); }
  } } }, "Save");
  const m = openModal("Settings", h("div", {},
    h("h4", {}, "Display"),
    h("label", {}, "Theme", theme), h("div", { style: { height: "8px" } }),
    h("label", {}, "Colour nodes by", colorBy), h("div", { style: { height: "8px" } }),
    h("label", {}, "Label density", labels)),
    { footer: [h("button", { class: "btn", on: { click: () => m.close() } }, "Cancel"), save] });
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

// ------------------------------------------------------------------------------------ checks: security flows and unused code

export async function checksModal(ctx, start = "security") {
  const project = encodeURIComponent(ctx.project());
  const body = h("div", {});
  const m = openModal("Checks", body, { wide: true });
  let tab = start;
  const cache = {};
  const load = async (what) => (cache[what] ||= await api(`/p/${project}/${what}`));
  const tabs = () => h("div", { class: "tabs", role: "tablist", style: { marginBottom: "8px" } },
    h("button", { class: "tab" + (tab === "security" ? " active" : ""), role: "tab", on: { click: () => { tab = "security"; draw(); } } }, "Security data flow"),
    h("button", { class: "tab" + (tab === "unused" ? " active" : ""), role: "tab", on: { click: () => { tab = "unused"; draw(); } } }, "Code nothing calls"));
  async function draw() {
    fill(body, tabs(), h("span", { class: "spin" }), " Analysing…");
    try { fill(body, tabs(), tab === "security" ? securityView(await load("taint")) : unusedView(await load("unused"))); }
    catch (e) { fill(body, tabs(), h("div", { class: "card" }, e.message)); }
  }
  const open = (id) => { m.close(); ctx.select(id, { focus: true }); };
  function securityView(r) {
    const local = (c) => ["cli", "env", "input"].includes(c);
    return h("div", {},
      h("p", { class: "muted small" }, `Python only. ${r.functions} functions in ${r.files} files were followed from untrusted sources (input(), sys.argv, request data, HTTP handler parameters, environment) to dangerous calls. These are leads to verify, not proof: the analysis is not path-sensitive.`),
      r.findings.length ? null : h("div", { class: "empty" }, "No source-to-sink paths found."),
      r.findings.map((f) => h("details", { class: "finding" },
        h("summary", {}, h("span", { class: "badge " + (f.severity === "HIGH" ? "high" : "medium") }, f.severity), h("b", {}, f.title), h("span", { class: "muted small" }, f.cwe),
          h("span", { class: "grow" }), h("span", { class: "muted small" }, `${f.file}:${f.line}`), h("span", { class: "badge " + (f.confidence === "high" ? "ok" : "warn") }, f.confidence)),
        h("div", { class: "body" },
          h("div", {}, "Data from ", h("b", {}, f.source), " reaches ", h("code", {}, f.sink), f.source_function !== f.function ? [" after crossing a call, entering in ", h("a", { href: "#", on: { click: (e) => { e.preventDefault(); open(f.source_function); } } }, short(f.source_function))] : ""),
          local(f.category) ? h("div", { class: "muted small", style: { margin: "4px 0" } }, "The source is local (command line, environment or stdin): this matters only if untrusted people can run the program or set these values.") : null,
          h("table", { class: "res", style: { margin: "8px 0", width: "100%" } }, h("tbody", {}, f.steps.map((s, i) => h("tr", { class: i === f.steps.length - 1 ? "hit" : "" }, h("td", {}, s[0]), h("td", {}, s[1]), h("td", { style: { fontFamily: "inherit" } }, s[2]))))),
          f.callers.length ? h("div", { class: "small muted" }, "Reachable from: ", f.callers.map((c, i) => [i ? ", " : "", h("a", { href: "#", on: { click: (e) => { e.preventDefault(); open(c.src); } } }, short(c.src))])) : null,
          h("div", { style: { marginTop: "6px" } }, h("b", {}, "Fix: "), f.fix),
          h("div", { style: { marginTop: "8px" } }, h("button", { class: "btn small", on: { click: () => open(f.function) } }, "Open the sink in the graph"))))));
  }
  function unusedView(r) {
    const c = r.counts;
    const list = (title, note, rows, key) => rows.length ? h("div", {}, h("h4", {}, `${title} (${c[key]})`), h("p", { class: "muted small" }, note),
      rows.map((x) => h("div", { class: "list-item", on: { click: () => open(x.symbol.id) } }, h("span", { class: "dot " + x.symbol.kind }), h("span", { class: "grow" }, short(x.symbol.id)),
        h("span", { class: "muted small" }, x.evidence.length ? x.evidence[0].text : `${x.symbol.file}:${x.symbol.start}`)))) : null;
    return h("div", {},
      h("p", { class: "muted small" }, `${r.total} functions, methods and classes have no caller, reference or subclass in the graph (tests excluded). The graph only sees calls it can read, so each is sorted by whether anything hints at hidden use.`),
      c.unassessed ? h("p", { class: "muted small" }, `${c.unassessed} more symbols are in JS/TS/Java/Go, which have no dynamic-use analysis, so they are not judged here. Open one and search the text before concluding.`) : null,
      list("No caller and no sign of hidden use", "Likely unused. Search the text for the name before deleting.", r.unreferenced, "unreferenced"),
      list("No caller, but check these", "A string, a run-time lookup or an unresolved call may reach them.", r.possible, "possible"),
      list("Reached without a visible call", "Entry points, decorators, overrides, properties and special methods: not dead code.", r.implicit, "implicit"));
  }
  draw();
}
