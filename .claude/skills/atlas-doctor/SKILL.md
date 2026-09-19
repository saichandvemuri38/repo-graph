---
name: atlas-doctor
description: Check that the code graph, hooks, agent and settings are set up and working. Use when a hook or graph result looks wrong, or after installing.
---

# /atlas-doctor — is CodeAtlas healthy?

1. Run `pwsh -NoProfile -File .claude/scripts/doctor.ps1`.
2. Report each `WARN` and `FAIL` with the fix it prints. If everything passes, say so in one line.
3. If `pwsh` itself is missing, tell the user to install PowerShell 7 (https://aka.ms/powershell); the hooks and scripts need it.
4. To repair a missing piece, run `install.ps1 -Update` from the engine folder (do this only if the user agrees; it rewrites the managed `.claude` folders).
