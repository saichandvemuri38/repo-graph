#!/bin/sh
# Use CodeAtlas on another repository. Nothing is copied except small config; the engine stays here.
#
#   sh scripts/attach.sh /path/to/other/repo            set up graph, MCP, Claude agents/skills, hooks
#   sh scripts/attach.sh /path/to/other/repo --no-hooks skip the git hooks
#
# In the target repo it adds: .claude/{agents,skills,prompts,CLAUDE.md,settings.json}, .mcp.json,
# scripts/{codeatlas,reindex}.sh (thin wrappers that point at this engine) and a CodeAtlas/ data folder.
# Existing files are never overwritten; JSON files are merged. Everything added is listed at the end.
set -eu

ENGINE="$(cd "$(dirname "$0")/.." && pwd)"
[ $# -ge 1 ] || { echo "usage: sh scripts/attach.sh /path/to/repo [--no-hooks]" >&2; exit 2; }
[ -d "$1" ] || { echo "not a folder: $1" >&2; exit 2; }
T="$(cd "$1" && pwd)"
[ "$T" != "$ENGINE" ] || { echo "that is the engine folder itself; nothing to attach" >&2; exit 2; }
HOOKS=1; [ "${2:-}" = "--no-hooks" ] && HOOKS=0
MARK="# codeatlas-wrapper"

mkdir -p "$T/.claude" "$T/scripts" "$T/CodeAtlas/graph"
for d in agents skills prompts; do
  mkdir -p "$T/.claude/$d"
  cp -Rn "$ENGINE/.claude/$d/." "$T/.claude/$d/" 2>/dev/null || true
done
if [ -e "$T/.claude/CLAUDE.md" ] && ! grep -q "CodeAtlas" "$T/.claude/CLAUDE.md"; then
  { printf '\n'; sed '1,/^## Always do/{/^## Always do/!d}' "$ENGINE/.claude/CLAUDE.md" | sed '1i ## CodeAtlas'; } >> "$T/.claude/CLAUDE.md"
  echo "appended CodeAtlas rules to existing .claude/CLAUDE.md"
elif [ ! -e "$T/.claude/CLAUDE.md" ]; then
  cp "$ENGINE/.claude/CLAUDE.md" "$T/.claude/CLAUDE.md"
fi

wrapper() {   # name, engine script
  f="$T/scripts/$1"
  if [ -e "$f" ] && ! grep -q "$MARK" "$f"; then
    echo "skipped scripts/$1: a different file already exists" >&2; return
  fi
  printf '#!/bin/sh\n%s\nROOT="$(cd "$(dirname "$0")/.." && pwd)"\nCODEATLAS_ROOT="$ROOT" exec sh "%s/scripts/%s" "$@"\n' "$MARK" "$ENGINE" "$2" > "$f"
}
wrapper codeatlas.sh codeatlas.sh
wrapper reindex.sh reindex.sh

python3 - "$T" "$ENGINE" <<'PY'
import json, sys, pathlib
t, engine = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
def load(p):
    try: return json.loads(p.read_text())
    except (OSError, ValueError): return {}
# .mcp.json
p = t / ".mcp.json"; d = load(p)
d.setdefault("mcpServers", {}).setdefault("codeatlas", {"command": "sh", "args": ["scripts/codeatlas.sh", "serve"]})
p.write_text(json.dumps(d, indent=2) + "\n")
# .claude/settings.json
p = t / ".claude" / "settings.json"; d = load(p); src = load(engine / ".claude" / "settings.json")
allow = d.setdefault("permissions", {}).setdefault("allow", [])
for rule in src["permissions"]["allow"]:
    if rule not in allow: allow.append(rule)
hooks = d.setdefault("hooks", {})
for event, groups in src["hooks"].items():
    have = json.dumps(hooks.get(event, []))
    for g in groups:
        if "reindex.sh" not in have: hooks.setdefault(event, []).append(g)
p.write_text(json.dumps(d, indent=2) + "\n")
PY

# keep generated data and machine-specific wrappers out of the target's git history
if git -C "$T" rev-parse --git-dir >/dev/null 2>&1; then
  EXC="$(git -C "$T" rev-parse --git-path info/exclude)"; case "$EXC" in /*) ;; *) EXC="$T/$EXC" ;; esac
  mkdir -p "$(dirname "$EXC")"; touch "$EXC"
  for pat in "CodeAtlas/" "scripts/codeatlas.sh" "scripts/reindex.sh" ".mcp.json"; do
    grep -qxF "$pat" "$EXC" || echo "$pat" >> "$EXC"
  done
  [ "$HOOKS" = 1 ] && CODEATLAS_ROOT="$T" sh "$ENGINE/scripts/install-hooks.sh" || true
else
  echo "note: $T is not a git repo, so no git hooks were installed (run 'git init', then re-run this script)"
fi

echo "indexing $T ..."
sh "$T/scripts/codeatlas.sh" index 2>&1 | head -3 || true
cat <<DONE

Attached: $T
  Claude Code : open that folder in Claude Code; the 'codeatlas' MCP server and /atlas-* skills are there (approve the MCP server once).
  Browser     : sh $ENGINE/scripts/codeatlas.sh web --open     then choose the project from the menu
  Refresh     : automatic on Claude edits, and on git commit/merge/checkout/push
DONE
