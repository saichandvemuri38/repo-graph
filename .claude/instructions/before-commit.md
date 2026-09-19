# Before you commit

Commit only when the user asks. Then:

1. **Run the check:** `pwsh -NoProfile -File .claude/scripts/precommit.ps1` (add `-Subject "..."` to set the subject line).
2. **Read the verdict.**
   - `BLOCK`: fix it. The usual cause is a removed symbol that other code still uses.
   - `REVIEW`: read each item. Security leads are Python data flows from untrusted input to a dangerous call; open the path in the source and decide whether it is real. High risk means many dependants: re-read the direct ones.
   - `OK`: go on.
3. **Run the tests it lists,** then record them: `work.ps1 test -Command "<cmd>" -Result pass|fail`. If the check warns that no passing run is recorded since the last edit, run them.
4. **Write the message.** The draft is in `.claude/atlas/work/commit-draft.txt`; edit it. Follow the repository's own style if it has one (look at `git log`). Subject in the imperative, under 72 characters, saying what and why; no ticket noise unless the repo uses it. Add the co-author trailer your instructions require, if any.
5. **Commit** with `git add <specific files>` and `git commit`. Do not use `git add -A` unless every changed file belongs to the task. Do not amend or rebase unless asked.
6. The post-commit hook refreshes the graph and records the commit in the task. Nothing more to do.

## If the check finds nothing wrong but you are unsure

Run `atlas_changes` and read the "removed symbols still referenced" and "tests that exercise the changes" sections yourself.
