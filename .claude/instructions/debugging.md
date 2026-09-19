# Debugging

The graph tells you where to look. The source tells you what is true.

1. **Get the evidence.** The exact error text, stack trace, failing test or wrong output. If the user gave only a symptom, ask for the smallest reproduction.
2. **Locate it.** With an error or trace: `atlas_locate_error` (paste the text). It maps frames to symbols (innermost first), finds where the message text appears in source, and shows callers and callees around the most relevant symbol. With only a symptom: `atlas_search "<symptom words>"`, which also returns connected code.
3. **Trace the path.** `atlas_trace <from> <to>` shows how execution gets from an entry point to the failing symbol. `atlas_context` shows callers and callees. `atlas_defs <function> <variable>` shows which assignments can supply a wrong value.
4. **Read the code** on that path. Form two or three hypotheses, ranked, each with the evidence for and against.
5. **Test the top hypothesis** before changing anything: add a print, run a narrower test, check the input. Report what you found.
6. **Fix it** through the normal loop (impact, small edit, tests). Write the failing case as a test first when the repo has tests.
7. **Say what was wrong** in one or two sentences and record it: `work.ps1 note -Text "Cause: ..."`.

## Traps

- A file with a syntax error has symbols but no call edges: the graph cannot trace through it. Fix the syntax first.
- Unresolved calls (`?name`) and dynamic dispatch (`getattr`, decorators, string tables) are invisible to the graph. If a call "cannot happen" according to the graph but happens, look there.
- Do not stop at the first plausible cause. Confirm it reproduces the symptom.
