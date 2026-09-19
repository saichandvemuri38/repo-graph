---
name: atlas-changes
description: Show which symbols your uncommitted work changed, what depends on them, and the risk - before committing or handing work over. Use when the user asks "what did I change", "is this safe to commit", or "review my changes".
argument-hint: "[--base <git ref>]"
---

# /atlas-changes — pre-commit safety check

Arguments: `$ARGUMENTS` (for example `--base origin/main` to review a whole branch).

1. Run `pwsh -NoProfile -File .claude/scripts/atlas.ps1 changes $ARGUMENTS` (or MCP tool `atlas_changes`). The engine compares each symbol's body exactly. With git it compares against `HEAD` (or the ref you pass); without git it compares against the last index. It lists modified, added and removed symbols, their direct dependants, and the flows touched.
2. Reply with:
   - the overall risk and the reason
   - a short table of changed symbols and their direct dependants
   - **removed symbols that are still referenced**. These are the most likely breakages
3. Add judgment: for the top two or three modified symbols, read the diff (`git diff` if there is a repo) and say whether the change alters a signature, return value or side effect that the listed dependants rely on.
4. If the risk is HIGH or CRITICAL, say which tests or code paths to run before committing.

Read-only. Nothing is committed or edited.
