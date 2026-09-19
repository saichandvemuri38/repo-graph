# The daily loop

One task at a time. A task is one change the user wants: a bug fix, a feature, a refactor, an investigation.

| Stage | You do | The scripts do |
|---|---|---|
| Start | `work.ps1 start -Title ... -Description ... -Accept ...` | refresh the graph, record the branch, the base commit and the graph size |
| Understand | `atlas_search`, `atlas_context`, `atlas_flows`, read source | nothing |
| Plan | tell the user; `work.ps1 note` for decisions | nothing |
| Edit | `atlas_impact` first, then small edits | gate check before, graph refresh + journal after |
| Verify | run tests; `work.ps1 test` | nothing |
| Commit | `precommit.ps1`, then commit if asked | graph refresh, snapshot, commit recorded in the task |
| Push | push if asked | graph refresh, snapshot, risk line, push recorded |
| Finish | `work.ps1 finish` | summary and PR description written, task archived |

## Choosing the size of a task

- A one-line fix still gets a task; it costs one command and it is what lets you resume.
- If the user changes topic mid-task, ask: finish or abandon the current one? Then `work.ps1 finish` (or `start -Force`, which marks the old one abandoned) and start the new one.
- Two unrelated changes in one task make a bad commit. Split them.

## When the user only asks a question

No task is needed for reading and explaining. Use the graph, answer, and stop. Start a task the moment they ask for a change.

## Commands you will use (always in this form, from the repo root)

```
pwsh -NoProfile -File .claude/scripts/work.ps1 start|status|note|confirm|test|finish|list ...
pwsh -NoProfile -File .claude/scripts/precommit.ps1
pwsh -NoProfile -File .claude/scripts/atlas.ps1 <engine command>     # for example: impact Foo.bar, taint, unused, web --open
pwsh -NoProfile -File .claude/scripts/doctor.ps1
```
