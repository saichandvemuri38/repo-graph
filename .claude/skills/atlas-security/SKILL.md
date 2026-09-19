---
name: atlas-security
description: Trace untrusted input to dangerous calls in Python (command, SQL, code, path, deserialization, request). Use when the user asks about injection, "is this safe", "where does this input go", or after changing input handling.
argument-hint: "[file or symbol]"
---

# /atlas-security — can untrusted input reach something dangerous?

Arguments: `$ARGUMENTS` (a file or symbol; empty means the whole project). Python only.

1. Run `pwsh -NoProfile -File .claude/scripts/atlas.ps1 taint $ARGUMENTS` (or the MCP tool `atlas_taint`). Each finding shows the source, every step, the sink and a fix.
2. For each finding, open the source lines on the path and decide: real, guarded elsewhere (for example an `if x.isdigit():` check, which the analysis cannot see), or a false lead. Say which and why.
3. Note the source category. `cli`, `env` and `input` are local: they matter only if untrusted people can run the program or set the values. `http` and `network` usually matter.
4. Confidence `med` means a library call was assumed to pass the value through. Verify that hop.
5. To answer "where does this value come from", use `atlas_defs <function> <variable>`.
6. Propose fixes but do not edit source in this skill.

Not covered: other languages, globals and closures, path-sensitive guards. Absence of findings is not proof of safety.
