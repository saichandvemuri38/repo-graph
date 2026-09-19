---
name: atlas-work
description: Start, check, note, or finish a tracked task in the daily work flow. Use when the user says "start working on X", "what am I working on", "note that ...", "we are done", or "finish this task".
argument-hint: "start <title> | status | note <text> | finish"
---

# /atlas-work — the task ledger

Arguments: `$ARGUMENTS`. The first word is the action.

- **start <title>**: run `pwsh -NoProfile -File .claude/scripts/work.ps1 start -Title "<title>" -Description "<what and why>" -Accept "<done when>; <another>"`. Ask the user for the description and the done-when items if they have not given them; keep both short. If a task is already active, ask whether to finish it or replace it (`-Force`).
- **status** (or no argument): run `work.ps1 status` and summarize: the task, what is touched, what is pending.
- **note <text>**: run `work.ps1 note -Text "<text>"`.
- **finish**: make sure the work is committed if the user wants it committed, then run `work.ps1 finish`. Show the paths of the summary and the pull request description it writes. Use `-Abandon` if the user drops the task.

Everything is stored in `.claude/atlas/work/` (see `.claude/instructions/context-and-memory.md`). The graph is refreshed by hooks; this skill only records the work.
