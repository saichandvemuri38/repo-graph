---
name: atlas-commit
description: Run the pre-commit check and prepare a commit message. Use when the user says "commit this", "is this ready to commit", or "check my changes before I commit".
argument-hint: "[subject line]"
---

# /atlas-commit — check, then commit

1. Run `pwsh -NoProfile -File .claude/scripts/precommit.ps1 -Subject "$ARGUMENTS"` (leave `-Subject` out when there are no arguments).
2. Follow `.claude/instructions/before-commit.md`: fix every BLOCK item, read every REVIEW item, run and record the tests it lists.
3. Show the user the verdict in a few lines and the message draft from `.claude/atlas/work/commit-draft.txt` (edit it to match the repository's style).
4. **Commit only if the user asked to commit.** Stage specific files, not everything. The post-commit hook refreshes the graph and records the commit in the task.
5. Do not push. Pushing is a separate request; see `.claude/instructions/on-push.md`.
