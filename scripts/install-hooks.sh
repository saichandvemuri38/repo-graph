#!/bin/sh
# Install (or remove) git hooks that keep the CodeAtlas graph current.
#
#   post-commit, post-merge, post-checkout, post-rewrite   re-index after HEAD or the working tree moves
#   pre-push                                               re-index and print a one-line risk summary (never blocks)
#
#   sh scripts/install-hooks.sh              install
#   sh scripts/install-hooks.sh uninstall    remove only the hooks this script installed
set -eu

ENGINE="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${CODEATLAS_ROOT:-$ENGINE}"       # the repo whose hooks to install (attach.sh sets this)
MODE="${1:-install}"
MARK="# codeatlas-hook"

if ! GIT_DIR="$(git -C "$DIR" rev-parse --git-dir 2>/dev/null)"; then
  echo "Not a git repository. Run 'git init' in $DIR first, then re-run this script." >&2
  exit 1
fi
case "$GIT_DIR" in /*) ;; *) GIT_DIR="$DIR/$GIT_DIR" ;; esac
HOOKS="$GIT_DIR/hooks"
mkdir -p "$HOOKS"

for name in post-commit post-merge post-checkout post-rewrite pre-push; do
  file="$HOOKS/$name"
  if [ "$MODE" = "uninstall" ]; then
    if [ -f "$file" ] && grep -q "$MARK" "$file"; then
      rm "$file"
      echo "removed  $name"
    fi
    continue
  fi
  if [ -f "$file" ] && ! grep -q "$MARK" "$file"; then
    echo "skipped  $name: a hook already exists at $file" >&2
    echo "         add this line to it to keep the graph current: sh \"$DIR/scripts/reindex.sh\"" >&2
    continue
  fi
  if [ "$name" = "pre-push" ]; then
    body="sh \"$DIR/scripts/codeatlas.sh\" check || true"
  else
    body="sh \"$DIR/scripts/reindex.sh\""
  fi
  printf '#!/bin/sh\n%s\n%s\nexit 0\n' "$MARK" "$body" > "$file"
  chmod +x "$file"
  echo "installed $name"
done
