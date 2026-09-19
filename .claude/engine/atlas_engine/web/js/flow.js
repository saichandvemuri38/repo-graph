// Execution-flow diagram as SVG: layered layout (columns = call depth), pan/zoom, click-to-focus.
// Drawn here rather than with Mermaid so it works offline and can talk back to the graph.

import { h, short } from "./util.js";

const NS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}, ...kids) => {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  kids.forEach((k) => k && el.append(k instanceof Node ? k : document.createTextNode(String(k))));
  return el;
};

/** proc: {steps_ids, edges:[{src,dst,conf}], symbols, depth}. Returns {el, focus(id), reset()}. */
export function renderFlow(proc, { onSelect } = {}) {
  const ids = proc.steps_ids;
  const depth = proc.depth || {};
  const cols = new Map();
  ids.forEach((id) => { const d = depth[id] ?? 0; (cols.get(d) || cols.set(d, []).get(d)).push(id); });
  const W = 210, H = 30, GX = 70, GY = 16;
  const pos = new Map();
  [...cols.keys()].sort((a, b) => a - b).forEach((d) => {
    cols.get(d).forEach((id, i) => pos.set(id, { x: 20 + d * (W + GX), y: 20 + i * (H + GY) }));
  });
  const maxX = Math.max(...[...pos.values()].map((p) => p.x)) + W + 20;
  const maxY = Math.max(...[...pos.values()].map((p) => p.y)) + H + 20;

  const root = svg("svg", { class: "flow-svg", viewBox: `0 0 ${maxX} ${maxY}`, role: "img", "aria-label": "Execution flow diagram" });
  root.append(svg("defs", {}, svg("marker", { id: "arr", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" }, svg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "currentColor", style: "color: var(--muted)" }))));
  const g = svg("g");
  root.append(g);

  const edgeEls = [];
  proc.edges.forEach((e) => {
    const a = pos.get(e.src), b = pos.get(e.dst);
    if (!a || !b) return;
    const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
    const path = svg("path", { class: "flow-edge" + (e.conf === "low" ? " low" : ""), d: `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2 - 2} ${y2}`, "marker-end": "url(#arr)" });
    g.append(path);
    edgeEls.push({ path, src: e.src, dst: e.dst });
  });
  const nodeEls = new Map();
  ids.forEach((id, i) => {
    const p = pos.get(id);
    const label = (proc.symbols[id]?.qname || short(id)).replace("<module>", "(module)");
    const node = svg("g", { class: "flow-node" + (i === 0 ? " entry" : ""), transform: `translate(${p.x},${p.y})`, tabindex: "0", role: "button", "aria-label": label },
      svg("title", {}, `${id}`), svg("rect", { width: W, height: H }), svg("text", { x: 9, y: 19 }, label.length > 27 ? label.slice(0, 26) + "…" : label));
    node.addEventListener("click", (ev) => { ev.stopPropagation(); focus(id); onSelect && onSelect(id); });
    node.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { focus(id); onSelect && onSelect(id); } });
    g.append(node);
    nodeEls.set(id, node);
  });

  // pan / zoom on the viewBox
  let vb = { x: 0, y: 0, w: maxX, h: maxY };
  const apply = () => root.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
  const zoom = (f, cx = 0.5, cy = 0.5) => { const nw = vb.w * f, nh = vb.h * f; vb = { x: vb.x + (vb.w - nw) * cx, y: vb.y + (vb.h - nh) * cy, w: nw, h: nh }; apply(); };
  root.addEventListener("wheel", (ev) => { ev.preventDefault(); const r = root.getBoundingClientRect(); zoom(ev.deltaY > 0 ? 1.15 : 0.87, (ev.clientX - r.left) / r.width, (ev.clientY - r.top) / r.height); }, { passive: false });
  let drag = null;
  root.addEventListener("pointerdown", (ev) => { drag = { x: ev.clientX, y: ev.clientY, vb: { ...vb } }; root.style.cursor = "grabbing"; });
  window.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const r = root.getBoundingClientRect();
    vb.x = drag.vb.x - (ev.clientX - drag.x) * (vb.w / r.width);
    vb.y = drag.vb.y - (ev.clientY - drag.y) * (vb.h / r.height);
    apply();
  });
  window.addEventListener("pointerup", () => { drag = null; root.style.cursor = ""; });

  let focused = null;
  function focus(id) {
    focused = focused === id ? null : id;
    const keep = new Set();
    if (focused) {
      keep.add(focused);
      edgeEls.forEach((e) => { if (e.src === focused || e.dst === focused) { keep.add(e.src); keep.add(e.dst); } });
    }
    nodeEls.forEach((el, nid) => { el.classList.toggle("dim", !!focused && !keep.has(nid)); el.classList.toggle("focus", nid === focused); });
    edgeEls.forEach((e) => e.path.classList.toggle("dim", !!focused && !(e.src === focused || e.dst === focused)));
  }
  const reset = () => { vb = { x: 0, y: 0, w: maxX, h: maxY }; apply(); focused = null; focus(null); };
  return { el: root, zoomIn: () => zoom(0.8), zoomOut: () => zoom(1.25), reset, focus, size: ids.length };
}

/** A combined map of several processes as one proc-like object. Returns null when it would be unreadable. */
export function combineProcesses(list, limit = 160) {
  const symbols = {}, edges = [], depth = {}, ids = [], seen = new Set();
  list.forEach((p) => {
    Object.assign(symbols, p.symbols || {});
    (p.steps_ids || []).forEach((id) => { if (!seen.has(id)) { seen.add(id); ids.push(id); depth[id] = p.depth?.[id] ?? 0; } else depth[id] = Math.min(depth[id], p.depth?.[id] ?? 0); });
    (p.edges || []).forEach((e) => edges.push(e));
  });
  if (ids.length > limit) return null;
  return { steps_ids: ids, edges, symbols, depth };
}

export function flowToolbar(view) {
  return h("div", { class: "row wrap", style: { marginBottom: "8px" } },
    h("button", { class: "btn small", on: { click: view.zoomIn }, title: "Zoom in (+)" }, "＋"),
    h("button", { class: "btn small", on: { click: view.zoomOut }, title: "Zoom out (−)" }, "－"),
    h("button", { class: "btn small", on: { click: view.reset }, title: "Reset zoom and pan" }, "Reset view"));
}
