# Remembering what you are doing

Your chat can end, be compacted, or restart. The task ledger survives.

## Where it lives (`.claude/atlas/work/`, generated, never edit by hand)

- `current.json`: the active task: title, description, done-when, branch, files touched with edit counts, symbols modified/added/removed, commits, pushes, tests run, notes, graph size at the start and now.
- `journal.jsonl`: every event with a time: task start, gate checks, edits, confirmations, notes, pre-commit checks, commits, pushes, tests, finish.
- `gates.json`: which files were impact-checked and which the user confirmed.
- `sessions/`: finished tasks. `summary-<id>.md` and `pr-<id>.md`: the write-ups.
- `graph-snapshots.jsonl`: graph size at every commit and push.

## When to read it

- At the start of every session: it was injected for you. If you do not see it, run `work.ps1 status`.
- After the chat is compacted or you feel unsure what is done: run `work.ps1 status`.
- Before you tell the user what has been done: check the journal instead of relying on memory.

## What to write down (`work.ps1 note -Text "..."`)

Only things you cannot recover from the code or the git history:
- a decision and the reason ("kept the old signature because 3 callers in other packages use it"),
- something you ruled out ("not the cache: reproduced with it disabled"),
- something you are waiting for ("waiting for the user to decide on the migration").
Keep each note to one or two sentences. Never put secrets in a note.

## After a restart

Say what the task is and what is left, in two lines, from the context. Do not redo finished steps. If the working tree has changes that are not in the journal, tell the user; someone edited outside the agent.
