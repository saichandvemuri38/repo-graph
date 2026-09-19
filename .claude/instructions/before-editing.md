# Before you edit

1. **Find the target.** Use `atlas_search` or `atlas_context` to name the exact symbol.
2. **Check the blast radius.** Run `atlas_impact <symbol>` (or a file path). Read the risk, the direct dependants (WILL BREAK), the flows touched and the tests listed.
3. **Read the warnings in the result.**
   - "Possible use the graph cannot see": decorators, overrides, `getattr`, string dispatch. Check each lead in the source before you change a signature.
   - "Unresolved calls with a matching name": possible dependants the graph could not link. Grep for them.
   - Low-confidence dependants are guesses: verify, do not trust or dismiss.
4. **Say the risk out loud** in your reply: LOW and MEDIUM in one line; HIGH or CRITICAL with the reason, then **stop and ask** before editing.
5. **The edit gate.** Editing a file whose dependants reach the block level in `.claude/config/policy.json` (default CRITICAL) is refused with a message. That is expected. Tell the user what depends on it and ask. When they agree, run:
   `pwsh -NoProfile -File .claude/scripts/work.ps1 confirm -Target <file>`
   and retry. The confirmation lasts eight hours or until the task finishes. Never confirm on your own.
6. **Keep changes small** and keep signatures stable when there are dependants outside the files you are changing. If a signature must change, update every dependant `atlas_impact` listed, including `REFERENCES`.
7. **Renames and deletions.** Do not use find-and-replace. Run `atlas_impact`, update the dependants, then check `atlas_changes`. Before deleting a symbol run `atlas_unused` and read the leads for it.

## After each edit

A hook refreshes the graph and may add a line to what you see: "this edit removed X, still used by Y". Act on it now, not at commit time.

## Deleting and renaming

The hooks watch the Edit, Write and MultiEdit tools. Files you delete or move with a shell command are not seen until the next graph refresh, so run `pwsh -NoProfile -File .claude/scripts/atlas.ps1 index` afterwards if you are going to ask the graph about them (the MCP tools and `precommit.ps1` refresh first anyway).
