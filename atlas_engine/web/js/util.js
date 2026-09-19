// Small helpers shared by every module: DOM builder, API client, toasts, colours, code highlighting.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** h("div", {class: "x", on: {click}}, "text", child, [more]) -> HTMLElement. Text is never parsed as HTML. */
export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "hidden" || k === "open") el[k] = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  const add = (kid) => {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) return kid.forEach(add);
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  };
  kids.forEach(add);
  return el;
}

/** Replace an element's children. Unlike replaceChildren it accepts arrays, skips null/false, and never prints "null". */
export function fill(el, ...kids) {
  el.replaceChildren();
  const add = (k) => {
    if (k === null || k === undefined || k === false) return;
    if (Array.isArray(k)) return k.forEach(add);
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  };
  kids.forEach(add);
  return el;
}

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const TOKEN = document.querySelector('meta[name="codeatlas-token"]')?.content || "";

export async function api(path, { method = "GET", body, signal } = {}) {
  const res = await fetch("/api" + path, {
    method, signal,
    headers: { "X-CodeAtlas-Token": TOKEN, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error page */ }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

export async function streamChat(project, messages, onEvent, signal) {
  const res = await fetch(`/api/p/${encodeURIComponent(project)}/chat`, {
    method: "POST", signal,
    headers: { "X-CodeAtlas-Token": TOKEN, "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  if (!res.ok) throw new Error(`Chat failed (${res.status})`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

export function toast(message, kind = "") {
  const t = h("div", { class: "toast " + kind, role: "status" }, message);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 6000 : 3200);
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export const KIND_COLORS = { folder: "#8b93a7", file: "#3b82f6", class: "#f59e0b", function: "#10b981", method: "#14b8a6", module: "#ec4899" };
export const KIND_LABELS = { folder: "Folder", file: "File", class: "Class", function: "Function", method: "Method", module: "Module" };
export const EDGE_COLORS = { CALLS: "#6366f1", IMPORTS: "#0ea5e9", EXTENDS: "#f97316", REFERENCES: "#a855f7", DEFINES: "#94a3b8", CONTAINS: "#94a3b8" };
export const EDGE_LABELS = { CALLS: "Calls", IMPORTS: "Imports", EXTENDS: "Extends", REFERENCES: "References", DEFINES: "Defines", CONTAINS: "Contains" };
export const CLUSTER_PALETTE = ["#6366f1", "#10b981", "#f59e0b", "#ec4899", "#0ea5e9", "#84cc16", "#f97316", "#a855f7", "#14b8a6", "#ef4444", "#eab308", "#22d3ee"];
export const RISK_COLORS = { 1: "#ef4444", 2: "#f97316", 3: "#eab308" };

export const short = (id) => String(id).split("::").pop().replace("<module>", "(module)");
export const fileOf = (id) => String(id).startsWith("dir:") ? id.slice(4) : String(id).split("::")[0];

export function fmtAge(iso) {
  if (!iso || iso === "never") return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!isFinite(s)) return iso;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

// Tiny syntax highlighter (keywords, strings, comments, numbers) for the code inspector. Output is escaped HTML.
const KEYWORDS = {
  python: "def class return if elif else for while import from as with try except finally raise yield lambda pass break continue in is not and or None True False self async await global nonlocal assert del",
  javascript: "function class return if else for while import from export default const let var new this async await try catch finally throw switch case break continue typeof instanceof in of null undefined true false extends static super yield interface type enum implements",
  java: "class interface enum public private protected static final abstract void int long double float boolean char byte short return if else for while do switch case break continue new this super try catch finally throw throws import package extends implements instanceof null true false synchronized",
  go: "func package import type struct interface map chan go defer return if else for range switch case default break continue var const nil true false select fallthrough",
};
KEYWORDS.typescript = KEYWORDS.tsx = KEYWORDS.javascript;

export function highlightLine(line, lang) {
  const kw = new Set((KEYWORDS[lang] || "").split(" "));
  const hash = lang === "python";
  const re = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(\/\/.*|\/\*.*?\*\/|#.*)|(\b\d[\d_.]*\b)|([A-Za-z_$][\w$]*)/g;
  let out = "", last = 0, m;
  while ((m = re.exec(line))) {
    out += esc(line.slice(last, m.index));
    const [tok, str, com, num, word] = m;
    if (str) out += `<span class="tok-s">${esc(tok)}</span>`;
    else if (com) { if ((tok.startsWith("#") && !hash)) out += esc(tok); else out += `<span class="tok-c">${esc(tok)}</span>`; }
    else if (num) out += `<span class="tok-n">${esc(tok)}</span>`;
    else if (word && kw.has(word)) out += `<span class="tok-k">${esc(tok)}</span>`;
    else out += esc(tok);
    last = m.index + tok.length;
  }
  return out + esc(line.slice(last));
}
