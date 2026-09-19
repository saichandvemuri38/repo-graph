---
name: atlas-debug
description: Graph-guided debugging. Use when the user pastes an error or stack trace, describes wrong behavior, or says "why is X failing / returning the wrong thing". Locates the code, traces the path, ranks hypotheses, and saves an HTML report.
argument-hint: "<error text | symptom | failing test>"
---

# /atlas-debug — find the cause with the graph

Problem statement: `$ARGUMENTS`. If it is empty, ask the user to paste the error, the stack trace or a description of the wrong behavior.

1. Launch the `bug-tracer` agent. Pass the user's text **verbatim**, plus anything you already know in this conversation (the open file, recent edits, the failing test command). The agent starts with the engine (`atlas_locate_error` / `locate`), which maps stack frames and message text to symbols and shows the call path around them. Then it reads the source and reasons.
2. Save the report as HTML. Pipe the agent's Markdown into the engine so rendering is deterministic:
   `sh scripts/codeatlas.sh report --kind debug --title "<one-line problem>"` with the Markdown on stdin (use a heredoc). It prints the path of the saved page.
3. Reply with:
   - the most likely hypothesis and its `file:line` evidence
   - the execution path in one line (`A → B → C`)
   - the suggested fix, described but **not applied**
   - the path of the saved HTML report
4. Ask whether to apply the fix. Only edit source when the user says yes. Afterwards run `/atlas-changes` to see what the edit touched. The graph re-indexes itself through the editor hook.

Never present a hypothesis as confirmed until the source you read proves it.
