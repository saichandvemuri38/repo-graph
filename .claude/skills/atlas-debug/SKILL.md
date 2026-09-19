---
name: atlas-debug
description: Graph-guided debugging. Use when the user pastes an error or stack trace, describes wrong behavior, or says "why is X failing / returning the wrong thing". Locates the code, traces the path, and ranks hypotheses.
argument-hint: "<error text | symptom | failing test>"
---

# /atlas-debug — find the cause with the graph

Problem statement: `$ARGUMENTS`. If it is empty, ask the user to paste the error, the stack trace or a description of the wrong behavior.

1. Follow `.claude/instructions/debugging.md`. Start with `atlas_locate_error` (paste the text verbatim), which maps stack frames and message text to symbols and shows the call path around them; then trace, read the source and rank hypotheses. Use anything you already know in this conversation (the open file, recent edits, the failing test command).
2. Reply with:
   - the most likely hypothesis and its `file:line` evidence
   - the execution path in one line (`A → B → C`)
   - the suggested fix, described but **not applied**
3. Ask whether to apply the fix. Only edit source when the user says yes. Afterwards run `/atlas-changes` to see what the edit touched. The graph re-indexes itself through the editor hook.

Never present a hypothesis as confirmed until the source you read proves it.
