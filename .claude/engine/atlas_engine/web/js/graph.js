// Graph canvas: a Sigma (WebGL) renderer over a graphology graph, with three layouts, filters, depth focus,
// highlighting, dragging and camera controls. It knows nothing about the rest of the app; app.js drives it.

import { KIND_COLORS, EDGE_COLORS, CLUSTER_PALETTE } from "./util.js";

const SigmaCtor = window.Sigma && (window.Sigma.Sigma || window.Sigma);
const GraphCtor = window.graphology && (window.graphology.MultiDirectedGraph || window.graphology.Graph || window.graphology);
const EDGE_WEIGHT = { CONTAINS: 2, DEFINES: 2, IMPORTS: 1, CALLS: 1, EXTENDS: 1.5, REFERENCES: 0.5 };
const STRUCTURE = new Set(["CONTAINS", "DEFINES"]);

export class GraphView {
  constructor(container, handlers = {}) {
    this.container = container;
    this.on = handlers;
    this.graph = null;
    this.sigma = null;
    this.viewMode = "force";
    this.colorBy = "kind";
    this.filters = { kinds: null, edges: null, hideLeaves: false, hideLow: false, depth: 0 };
    this.selected = null;
    this.hovered = null;
    this.highlight = new Map();      // node id -> colour (or true)
    this.edgeHighlight = new Set();  // "src|dst" pairs drawn emphasised
    this.hidden = new Set();
    this.hiddenEdges = new Set();
    this.layoutRunning = false;
    this._raf = 0;
    this.theme = "light";
  }

  get ready() { return !!this.sigma; }

  // ---------------------------------------------------------------- loading

  load(data, keepView = false) {
    this.stopLayout();
    const cam = this.sigma && keepView ? this.sigma.getCamera().getState() : null;
    if (this.sigma) { this.sigma.kill(); this.sigma = null; }
    this.container.innerHTML = "";
    this.data = data;
    const g = new GraphCtor({ multi: true, type: "directed" });
    const n = data.nodes.length;
    data.nodes.forEach((node, i) => {
      const a = (i / Math.max(1, n)) * Math.PI * 2, r = 30 + Math.sqrt(n) * 6 * (0.4 + ((i * 7919) % 100) / 100);
      g.addNode(node.id, {
        label: node.l, kind: node.k, file: node.f, cluster: node.c ?? null, degree: node.d || 0, err: node.err || 0,
        x: Math.cos(a) * r, y: Math.sin(a) * r, size: this._size(node), color: this._color(node), start: node.s, end: node.e,
      });
    });
    data.edges.forEach(([s, t, type, conf], i) => {
      if (g.hasNode(s) && g.hasNode(t)) g.addEdgeWithKey(`e${i}`, s, t, {
        etype: type, conf, weight: EDGE_WEIGHT[type] || 1, size: STRUCTURE.has(type) ? 0.5 : 1.2, color: this._edgeColor(type, conf), type: "arrow",
      });
    });
    this.graph = g;
    this.filters.kinds = this.filters.kinds || new Set(["folder", "file", "class", "function", "method", "module"]);
    this.filters.edges = this.filters.edges || new Set(Object.keys(EDGE_COLORS));
    this.sigma = new SigmaCtor(g, this.container, {
      renderEdgeLabels: false, defaultEdgeType: "arrow", zIndex: true, allowInvalidContainer: true,
      labelDensity: 0.9, labelGridCellSize: 90, labelRenderedSizeThreshold: 6, labelFont: "system-ui, sans-serif", labelSize: 12,
      labelColor: { color: this.theme === "dark" ? "#d5dae6" : "#2a3142" },
      minCameraRatio: 0.01, maxCameraRatio: 30,
      nodeReducer: (id, d) => this._nodeReducer(id, d), edgeReducer: (id, d) => this._edgeReducer(id, d),
      defaultDrawNodeHover: (ctx, d, st) => this._drawHover(ctx, d, st),
    });
    this._wireEvents();
    this.recompute();
    if (cam) this.sigma.getCamera().setState(cam);
    if (!keepView) this.setViewMode(this.viewMode, true);
    else this.sigma.refresh();
  }

  _size(n) {
    const base = { folder: 6, file: 5, class: 4.5, module: 3.5, function: 3.2, method: 2.8 }[n.k] || 3;
    return Math.min(22, base + Math.sqrt(n.d || 0) * 1.4);
  }
  _color(n) {
    if (this.colorBy === "cluster" && n.c) return CLUSTER_PALETTE[(parseInt(String(n.c).slice(1), 10) - 1) % CLUSTER_PALETTE.length];
    return KIND_COLORS[n.k] || "#888";
  }
  _edgeColor(type, conf) {
    const c = EDGE_COLORS[type] || "#999";
    const light = this.theme !== "dark";
    if (STRUCTURE.has(type)) return light ? "#cfd5df" : "#3a4356";
    return conf === "low" ? (light ? "#c7c9d9" : "#454a62") : c + (light ? "aa" : "bb");
  }

  setColorBy(mode) {
    this.colorBy = mode;
    if (!this.graph) return;
    this.data.nodes.forEach((n) => this.graph.setNodeAttribute(n.id, "color", this._color(n)));
    this.sigma.refresh();
  }

  setTheme(theme) {
    this.theme = theme;
    if (!this.sigma) return;
    this.sigma.setSetting("labelColor", { color: theme === "dark" ? "#d5dae6" : "#2a3142" });
    this.graph.forEachEdge((e, a) => this.graph.setEdgeAttribute(e, "color", this._edgeColor(a.etype, a.conf)));
    this.sigma.refresh();
  }

  /** Label box for hovered/selected nodes, drawn in the current theme (Sigma's default is always white). */
  _drawHover(ctx, data, settings) {
    const dark = this.theme === "dark", size = settings.labelSize, pad = 3;
    ctx.font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;
    ctx.fillStyle = dark ? "#1c2230" : "#ffffff";
    ctx.shadowOffsetX = 0; ctx.shadowOffsetY = 0; ctx.shadowBlur = 8; ctx.shadowColor = dark ? "rgba(0,0,0,.7)" : "rgba(0,0,0,.25)";
    const r = data.size + pad;
    if (typeof data.label === "string" && data.label) {
      const w = Math.round(ctx.measureText(data.label).width + 8), h = Math.round(size + 2 * pad);
      ctx.beginPath();
      ctx.roundRect ? ctx.roundRect(data.x + r - 2, data.y - h / 2, w, h, 5) : ctx.rect(data.x + r - 2, data.y - h / 2, w, h);
      ctx.fill();
      ctx.beginPath(); ctx.arc(data.x, data.y, r, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
      ctx.fillStyle = dark ? "#e6e9f0" : "#1a2030";
      ctx.fillText(data.label, data.x + r + 2, data.y + size / 3);
    } else { ctx.beginPath(); ctx.arc(data.x, data.y, r, 0, Math.PI * 2); ctx.fill(); }
    ctx.shadowBlur = 0;
  }

  // ---------------------------------------------------------------- reducers

  _nodeReducer(id, d) {
    const res = { ...d };
    if (this.hidden.has(id)) { res.hidden = true; return res; }
    const hl = this.highlight;
    const dim = this.theme === "dark" ? "#2b3142" : "#dfe3ec";
    if (hl.size) {
      if (hl.has(id)) { const c = hl.get(id); if (typeof c === "string") res.color = c; res.zIndex = 3; res.forceLabel = true; res.size = d.size * 1.35; }
      else { res.color = dim; res.label = ""; res.zIndex = 0; }
    }
    if (this.hovered && this.hovered !== id && !this.graph.areNeighbors(this.hovered, id) && !hl.has(id)) { res.color = dim; res.label = ""; }
    if (id === this.selected) { res.highlighted = true; res.zIndex = 4; res.forceLabel = true; res.size = d.size * 1.5; }
    if (id === this.hovered) { res.highlighted = true; res.zIndex = 4; res.forceLabel = true; }
    if (d.err && !hl.size) res.borderColor = "#ef4444";
    return res;
  }

  _edgeReducer(id, d) {
    const res = { ...d };
    if (this.hiddenEdges.has(id)) { res.hidden = true; return res; }
    const [s, t] = this.graph.extremities(id);
    if (this.edgeHighlight.size) {
      if (this.edgeHighlight.has(s + "|" + t)) { res.color = "#f59e0b"; res.size = 2.6; res.zIndex = 3; }
      else if (this.highlight.size) res.hidden = true;
    } else if (this.highlight.size && !(this.highlight.has(s) && this.highlight.has(t))) res.color = this.theme === "dark" ? "#252b3b" : "#eceff5";
    if (this.hovered && s !== this.hovered && t !== this.hovered) res.color = this.theme === "dark" ? "#232838" : "#eef0f5";
    else if (this.hovered) { res.size = Math.max(res.size, 2); res.zIndex = 2; }
    return res;
  }

  // ---------------------------------------------------------------- filters

  setFilters(patch) {
    Object.assign(this.filters, patch);
    this.recompute();
  }

  recompute() {
    if (!this.graph) return;
    const f = this.filters, g = this.graph;
    this.hidden = new Set();
    this.hiddenEdges = new Set();
    g.forEachNode((id, a) => { if (!f.kinds.has(a.kind)) this.hidden.add(id); });
    g.forEachEdge((e, a, s, t) => {
      if (!f.edges.has(a.etype) || this.hidden.has(s) || this.hidden.has(t) || (f.hideLow && a.conf === "low")) this.hiddenEdges.add(e);
    });
    if (f.hideLeaves) {
      const deg = new Map();
      g.forEachEdge((e, a, s, t) => { if (!this.hiddenEdges.has(e)) { deg.set(s, (deg.get(s) || 0) + 1); deg.set(t, (deg.get(t) || 0) + 1); } });
      g.forEachNode((id) => { if (!this.hidden.has(id) && (deg.get(id) || 0) <= 1 && id !== this.selected) this.hidden.add(id); });
      g.forEachEdge((e, a, s, t) => { if (this.hidden.has(s) || this.hidden.has(t)) this.hiddenEdges.add(e); });
    }
    if (f.depth > 0 && this.selected && g.hasNode(this.selected)) {
      const keep = this.neighbourhood(this.selected, f.depth);
      g.forEachNode((id) => { if (!keep.has(id)) this.hidden.add(id); });
      g.forEachEdge((e, a, s, t) => { if (this.hidden.has(s) || this.hidden.has(t)) this.hiddenEdges.add(e); });
    }
    this.sigma.refresh();
    this.on.onStats && this.on.onStats(this.visibleCounts());
  }

  visibleCounts() {
    return { nodes: this.graph.order - this.hidden.size, edges: this.graph.size - this.hiddenEdges.size };
  }

  /** Nodes within `depth` hops of `start`, ignoring edge direction and hidden edges. */
  neighbourhood(start, depth, respectFilters = true) {
    const seen = new Map([[start, 0]]);
    let frontier = [start];
    for (let d = 1; d <= depth; d++) {
      const next = [];
      for (const n of frontier) {
        this.graph.forEachEdge(n, (e, a, s, t) => {
          if (respectFilters && this.hiddenEdges.has(e) && !(this.filters.depth > 0 && false)) { /* hidden by type */ }
          const other = s === n ? t : s;
          if (!seen.has(other) && !(respectFilters && !this.filters.edges.has(a.etype))) { seen.set(other, d); next.push(other); }
        });
      }
      frontier = next;
    }
    return new Set(seen.keys());
  }

  // ---------------------------------------------------------------- selection and highlights

  select(id, { focus = false } = {}) {
    this.selected = id && this.graph && this.graph.hasNode(id) ? id : null;
    if (this.filters.depth > 0) this.recompute(); else this.sigma && this.sigma.refresh();
    if (this.selected && focus) this.focusNode(this.selected);
  }

  setHighlights(map, edges = new Set()) {
    this.highlight = map instanceof Map ? map : new Map([...(map || [])].map((id) => [id, true]));
    this.edgeHighlight = edges;
    this.sigma && this.sigma.refresh();
  }
  clearHighlights() { this.setHighlights(new Map()); }

  /** Frame a node together with its direct neighbours, so you see what it connects to, not just a big dot. */
  focusNode(id) {
    if (!this.sigma || !this.graph.hasNode(id)) return;
    const around = this.neighbourhood(id, 1);
    if (around.size > 1) return this.fitTo(around);
    const d = this.sigma.getNodeDisplayData(id);
    if (d) this.sigma.getCamera().animate({ x: d.x, y: d.y, ratio: Math.min(0.4, this.sigma.getCamera().ratio) }, { duration: 450 });
  }
  fitTo(ids) {
    if (!ids || !ids.size || !this.sigma) return this.fit();
    if (this.layoutRunning) { this._pendingFit = ids; return; }      // positions are still moving; fit when they settle
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    ids.forEach((id) => { const d = this.graph.hasNode(id) && this.sigma.getNodeDisplayData(id); if (d) { x0 = Math.min(x0, d.x); y0 = Math.min(y0, d.y); x1 = Math.max(x1, d.x); y1 = Math.max(y1, d.y); } });
    if (!isFinite(x0)) return;
    const span = Math.max(x1 - x0, y1 - y0, 0.02);
    this.sigma.getCamera().animate({ x: (x0 + x1) / 2, y: (y0 + y1) / 2, ratio: Math.max(0.12, Math.min(1, span * 1.6)) }, { duration: 500 });
  }
  fit() { this.sigma && this.sigma.getCamera().animatedReset({ duration: 400 }); }
  zoomIn() { this.sigma && this.sigma.getCamera().animatedZoom({ duration: 250 }); }
  zoomOut() { this.sigma && this.sigma.getCamera().animatedUnzoom({ duration: 250 }); }

  // ---------------------------------------------------------------- events

  _wireEvents() {
    const s = this.sigma;
    let dragged = null, moved = false;
    s.on("clickNode", ({ node }) => { if (!moved) this.on.onSelect && this.on.onSelect(node); });
    s.on("doubleClickNode", (e) => { e.preventSigmaDefault(); this.on.onFocus && this.on.onFocus(e.node); });
    s.on("clickStage", () => { this.on.onSelect && this.on.onSelect(null); });
    s.on("enterNode", ({ node }) => { this.hovered = node; this.container.style.cursor = "pointer"; s.refresh(); this.on.onHover && this.on.onHover(node); });
    s.on("leaveNode", () => { this.hovered = null; this.container.style.cursor = ""; s.refresh(); this.on.onHover && this.on.onHover(null); });
    s.on("downNode", (e) => { dragged = e.node; moved = false; if (!s.getCustomBBox()) s.setCustomBBox(s.getBBox()); });
    s.getMouseCaptor().on("mousemovebody", (e) => {
      if (!dragged) return;
      moved = true;
      const p = s.viewportToGraph(e);
      this.graph.setNodeAttribute(dragged, "x", p.x);
      this.graph.setNodeAttribute(dragged, "y", p.y);
      e.preventSigmaDefault(); e.original.preventDefault(); e.original.stopPropagation();
    });
    const up = () => { dragged = null; setTimeout(() => { moved = false; }, 0); };
    s.getMouseCaptor().on("mouseup", up);
    s.getMouseCaptor().on("mousedown", () => { if (!s.getCustomBBox()) s.setCustomBBox(s.getBBox()); });
  }

  // ---------------------------------------------------------------- layouts

  setViewMode(mode, initial = false) {
    this.viewMode = mode;
    if (!this.graph) return;
    this.stopLayout();
    if (mode === "force") { this.on.onLayoutState && this.on.onLayoutState("running"); this.runForce(initial); }
    else this._animateTo(mode === "tree" ? this._treeLayout() : this._radialLayout());
  }

  runForce(fresh = false) {
    if (!window.forceAtlas2 || !this.graph) return;
    const g = this.graph, n = g.order;
    if (!fresh) this._scatter();
    this.sigma.setCustomBBox(null);
    const settings = { ...window.forceAtlas2.inferSettings(g), barnesHutOptimize: n > 400, gravity: 0.9, scalingRatio: n > 2000 ? 12 : 6, slowDown: 3 };
    const total = n < 300 ? 500 : n < 2000 ? 260 : 120;
    let done = 0;
    this.layoutRunning = true;
    this.on.onLayoutState && this.on.onLayoutState("running");
    const step = () => {
      if (!this.layoutRunning) return;
      window.forceAtlas2.assign(g, { iterations: 6, settings, getEdgeWeight: "weight" });
      this.sigma.refresh();
      done += 6;
      if (done < total) this._raf = requestAnimationFrame(step);
      else { this.layoutRunning = false; this.sigma.setCustomBBox(null); this._settled(); }
    };
    this._raf = requestAnimationFrame(step);
  }

  _settled() {
    const pending = this._pendingFit;
    this._pendingFit = null;
    this.on.onLayoutState && this.on.onLayoutState("done");
    pending ? this.fitTo(pending) : this.fit();
  }

  _scatter() {
    const n = this.graph.order;
    let i = 0;
    this.graph.forEachNode((id, a) => {
      const ang = (i++ / n) * Math.PI * 2, r = 30 + Math.sqrt(n) * 6 * (0.4 + ((i * 7919) % 100) / 100);
      this.graph.mergeNodeAttributes(id, { x: a.x + Math.cos(ang) * r * 0.1, y: a.y + Math.sin(ang) * r * 0.1 });
    });
  }

  stopLayout() {
    this.layoutRunning = false;
    cancelAnimationFrame(this._raf);
    this.on.onLayoutState && this.on.onLayoutState("idle");
  }

  /** Containment forest (folder > file > class > method) from CONTAINS/DEFINES edges. */
  _forest() {
    const kids = new Map(), hasParent = new Set();
    this.graph.forEachEdge((e, a, s, t) => {
      if (STRUCTURE.has(a.etype) && !hasParent.has(t)) { (kids.get(s) || kids.set(s, []).get(s)).push(t); hasParent.add(t); }
    });
    const rank = { folder: 0, file: 1, class: 2, module: 3, function: 4, method: 5 };
    const cmp = (a, b) => (rank[this.graph.getNodeAttribute(a, "kind")] - rank[this.graph.getNodeAttribute(b, "kind")]) || String(this.graph.getNodeAttribute(a, "label")).localeCompare(this.graph.getNodeAttribute(b, "label"));
    const roots = [];
    this.graph.forEachNode((id) => { if (!hasParent.has(id)) roots.push(id); });
    roots.sort(cmp);
    kids.forEach((list) => list.sort(cmp));
    return { kids, roots };
  }

  _treeLayout() {
    const { kids, roots } = this._forest(), pos = new Map();
    let row = 0;
    const place = (id, depth) => {
      const list = kids.get(id) || [];
      if (!list.length) { pos.set(id, { x: depth * 40, y: row * 6 }); row++; return; }
      list.forEach((c) => place(c, depth + 1));
      const ys = list.map((c) => pos.get(c).y);
      pos.set(id, { x: depth * 40, y: (Math.min(...ys) + Math.max(...ys)) / 2 });
    };
    roots.forEach((r) => place(r, 0));
    return pos;
  }

  _radialLayout() {
    const { kids, roots } = this._forest(), pos = new Map();
    const leaves = new Map();
    const count = (id) => { const l = kids.get(id) || []; const c = l.length ? l.reduce((a, k) => a + count(k), 0) : 1; leaves.set(id, c); return c; };
    const total = roots.reduce((a, r) => a + count(r), 0) || 1;
    const place = (id, depth, a0, a1) => {
      const mid = (a0 + a1) / 2, r = depth * 34;
      pos.set(id, { x: Math.cos(mid) * r, y: Math.sin(mid) * r });
      let cur = a0;
      (kids.get(id) || []).forEach((c) => { const span = (a1 - a0) * (leaves.get(c) / leaves.get(id)); place(c, depth + 1, cur, cur + span); cur += span; });
    };
    let cur = 0;
    roots.forEach((r) => { const span = Math.PI * 2 * (leaves.get(r) / total); place(r, 1, cur, cur + span); cur += span; });
    return pos;
  }

  _animateTo(target) {
    const g = this.graph, start = new Map();
    g.forEachNode((id, a) => start.set(id, { x: a.x, y: a.y }));
    let f = 0;
    const frames = g.order > 3000 ? 1 : 24;
    this.layoutRunning = true;
    const step = () => {
      if (!this.layoutRunning) return;
      f++;
      const t = f / frames, e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      g.forEachNode((id) => {
        const s = start.get(id), to = target.get(id);
        if (to) g.mergeNodeAttributes(id, { x: s.x + (to.x - s.x) * e, y: s.y + (to.y - s.y) * e });
      });
      this.sigma.refresh();
      if (f < frames) this._raf = requestAnimationFrame(step);
      else { this.layoutRunning = false; this.sigma.setCustomBBox(null); this._settled(); }
    };
    this._raf = requestAnimationFrame(step);
  }

  destroy() { this.stopLayout(); this.sigma && this.sigma.kill(); this.sigma = null; }
}
