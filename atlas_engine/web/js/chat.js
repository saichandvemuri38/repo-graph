// Nexus AI chat panel: streams tool calls and answers from /api/p/<project>/chat.

import { fill, h, esc, streamChat, api, toast } from "./util.js";

const SUGGESTIONS = [
  "Give me an overview of this codebase",
  "Which functions are called the most?",
  "What breaks if I change the most-connected function?",
  "Which files have parse errors, and why?",
  "Trace how execution reaches the deepest function",
];

const TOOL_LABELS = {
  codebase_overview: "🗺 Codebase overview", search_symbols: "🔍 Search symbols", get_symbol: "🔬 Symbol context", impact: "💥 Impact analysis",
  trace_path: "🧭 Trace path", read_source: "📄 Read source", run_sql: "🔗 SQL query", locate_error: "🐞 Locate error", changes: "✏️ Changes",
};

/** Small, safe Markdown renderer: paragraphs, lists, code fences, inline code, bold, and clickable code references. */
export function renderMarkdown(text, knownFiles, onCite) {
  const root = h("div", { class: "bubble" });
  const lines = text.split("\n");
  let i = 0, list = null;
  const inline = (s) => {
    const frag = document.createDocumentFragment();
    const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|([\w./-]+\.\w+(?:::[\w.<>#]+|:\d+(?:-\d+)?))/g;
    let last = 0, m;
    while ((m = re.exec(s))) {
      frag.append(document.createTextNode(s.slice(last, m.index)));
      const tok = m[0];
      const cite = (t) => {
        const c = t.replace(/^`|`$/g, "");
        const mm = c.match(/^([\w./-]+\.\w+)(?:::(.+)|:(\d+)(?:-(\d+))?)$/);
        return mm && knownFiles.has(mm[1]) ? { file: mm[1], sym: mm[2], start: mm[3] ? +mm[3] : null, end: mm[4] ? +mm[4] : null, id: mm[2] ? c : null } : null;
      };
      if (m[1]) {
        const c = cite(tok);
        frag.append(c ? h("span", { class: "cite", on: { click: () => onCite(c) }, title: "Open in the code inspector" }, tok.slice(1, -1)) : h("code", {}, tok.slice(1, -1)));
      } else if (m[2]) frag.append(h("strong", {}, tok.slice(2, -2)));
      else { const c = cite(tok); frag.append(c ? h("span", { class: "cite", on: { click: () => onCite(c) } }, tok) : document.createTextNode(tok)); }
      last = m.index + tok.length;
    }
    frag.append(document.createTextNode(s.slice(last)));
    return frag;
  };
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const code = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      root.append(h("pre", {}, h("code", {}, code.join("\n"))));
      list = null;
    } else if (/^\s*[-*] /.test(line)) {
      if (!list) { list = h("ul"); root.append(list); }
      list.append(h("li", {}, inline(line.replace(/^\s*[-*] /, ""))));
    } else if (/^#{1,4} /.test(line)) {
      root.append(h("p", {}, h("strong", {}, inline(line.replace(/^#+ /, "")))));
      list = null;
    } else if (line.trim()) {
      list = null;
      root.append(h("p", {}, inline(line)));
    } else list = null;
    i++;
  }
  return root;
}

export class ChatPanel {
  constructor(host, ctx) {
    this.host = host;
    this.ctx = ctx;               // {project(), files(), onCitations(list), openCitation(c), settings()}
    this.history = [];
    this.abort = null;
    this.build();
  }

  build() {
    this.log = h("div", { class: "chat-log", "aria-live": "polite" });
    this.input = h("textarea", { placeholder: "Ask about this codebase…  (Enter to send, Shift+Enter for a new line)", rows: "1", "aria-label": "Message Nexus AI" });
    this.send = h("button", { class: "btn primary", on: { click: () => this.submit() } }, "Send");
    this.stop = h("button", { class: "btn", hidden: true, on: { click: () => this.abort && this.abort.abort() } }, "Stop");
    this.input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.submit(); } });
    this.input.addEventListener("input", () => { this.input.style.height = "auto"; this.input.style.height = Math.min(140, this.input.scrollHeight) + "px"; });
    fill(this.host, h("div", { class: "chat" }, this.log, h("div", { class: "chat-input" }, this.input, this.send, this.stop)));
    this.welcome();
  }

  async welcome() {
    fill(this.log, );
    let s = null;
    try { s = await api("/settings"); } catch { /* shown below */ }
    const intro = h("div", { class: "card" },
      h("h3", {}, "✦ Nexus AI"),
      h("div", { class: "small muted" }, "Ask in plain language. Nexus searches the code graph, reads source, runs impact analysis and SQL, and cites the code it used."));
    this.log.append(intro);
    if (s && !s.has_key) {
      this.log.append(h("div", { class: "empty" }, "No API key yet. ", h("a", { href: "#", on: { click: (e) => { e.preventDefault(); this.ctx.openSettings(); } } }, "Open Settings"), " and add an Anthropic API key, or start the server with ", h("code", {}, "ANTHROPIC_API_KEY"), " set."));
    }
    this.log.append(h("div", { class: "muted small", style: { marginTop: "8px" } }, "Try asking:"),
      h("div", { class: "suggest" }, SUGGESTIONS.map((q) => h("button", { on: { click: () => { this.input.value = q; this.submit(); } } }, q))));
  }

  reset() { this.history = []; this.welcome(); }

  async submit() {
    const text = this.input.value.trim();
    if (!text || this.abort) return;
    this.input.value = "";
    this.input.style.height = "auto";
    if (!this.history.length) fill(this.log, );
    this.history.push({ role: "user", content: text });
    this.log.append(h("div", { class: "msg user" }, h("div", { class: "who" }, "You"), h("div", { class: "bubble" }, text)));
    const bubble = h("div", { class: "msg ai" }, h("div", { class: "who" }, "Nexus"));
    const body = h("div");
    const status = h("div", { class: "muted small" }, h("span", { class: "spin" }), " Thinking…");
    bubble.append(body, status);
    this.log.append(bubble);
    this.scroll();
    this.abort = new AbortController();
    this.send.hidden = true;
    this.stop.hidden = false;
    const cards = new Map();
    let answer = "";
    try {
      await streamChat(this.ctx.project(), this.history, (ev) => {
        if (ev.type === "text") {
          answer += (answer ? "\n\n" : "") + ev.text;
          const rendered = renderMarkdown(ev.text, this.ctx.files(), (c) => this.ctx.openCitation(c));
          body.append(rendered);
        } else if (ev.type === "tool_start") {
          const card = h("details", { class: "tool-card" },
            h("summary", {}, h("span", { class: "spin" }), h("strong", {}, TOOL_LABELS[ev.name] || ev.name), h("span", { class: "muted small grow" }, JSON.stringify(ev.input).slice(0, 80))),
            h("pre", {}, JSON.stringify(ev.input, null, 2)));
          cards.set(ev.id, card);
          body.append(card);
        } else if (ev.type === "tool_result") {
          const card = cards.get(ev.id);
          if (card) {
            card.querySelector(".spin")?.replaceWith(h("span", { class: ev.ok ? "badge ok" : "badge bad" }, ev.ok ? "done" : "error"));
            card.append(h("pre", {}, ev.preview));
          }
        } else if (ev.type === "error") {
          body.append(h("div", { class: "card", style: { borderColor: "var(--bad)" } }, ev.message));
          if (ev.code === "no_key") this.ctx.openSettings();
        } else if (ev.type === "done") {
          this.ctx.onCitations(ev.citations || []);
          if (ev.citations?.length) body.append(h("div", { class: "muted small" }, `${ev.citations.length} code reference(s) added to the Code tab.`));
        }
        this.scroll();
      }, this.abort.signal);
      if (answer) this.history.push({ role: "assistant", content: answer });
    } catch (e) {
      if (e.name === "AbortError") body.append(h("div", { class: "muted small" }, "Stopped."));
      else { body.append(h("div", { class: "card", style: { borderColor: "var(--bad)" } }, e.message)); toast(e.message, "bad"); }
    } finally {
      status.remove();
      this.abort = null;
      this.send.hidden = false;
      this.stop.hidden = true;
      this.scroll();
    }
  }

  scroll() { this.log.scrollTop = this.log.scrollHeight; }
}
