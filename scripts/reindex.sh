#!/bin/sh
# Refresh the graph quietly. Always exits 0, so it is safe inside git hooks and editor hooks.
# Only changed files are re-parsed; when nothing changed this finishes in a fraction of a second.
# The last run's output is kept in CodeAtlas/graph/.index.log so a failure is never silent.
DIR="$(cd "$(dirname "$0")/.." && pwd)" || exit 0
ROOT="${CODEATLAS_ROOT:-$DIR}"          # set by the wrapper that `attach.sh` puts in another repo
mkdir -p "$ROOT/CodeAtlas/graph" 2>/dev/null
CODEATLAS_ROOT="$ROOT" sh "$DIR/scripts/codeatlas.sh" index --quiet >"$ROOT/CodeAtlas/graph/.index.log" 2>&1 || true
exit 0
