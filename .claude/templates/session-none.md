## CodeAtlas work context

No active task. Graph: {{graph}}. Branch: `{{branch}}`.
When the user describes a change to make, start a task first so the work is tracked and survives a restart:
`pwsh -NoProfile -File .claude/scripts/work.ps1 start -Title "<what you are doing>"`

Rules: check impact before editing a symbol (the edit gate enforces it for critical files), let the hooks keep the graph fresh, run `precommit.ps1` before committing, and commit or push only when the user asks.
