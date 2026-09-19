## CodeAtlas work context

Active task: **{{task}}** (session `{{id}}`, started {{since}}, branch `{{taskBranch}}`)
{{#if description}}{{description}}
{{/if}}{{#if acceptance}}Done when:
{{#each acceptance}}- {{.}}
{{/each}}{{/if}}
Graph: {{graph}}. Since the task started: {{graphDelta}}.
Symbols so far: {{symbolsModified}} modified, {{symbolsAdded}} added, {{symbolsRemoved}} removed.
{{#if files}}Files touched:
{{#each files}}- {{.}}
{{/each}}{{/if}}{{moreFilesLine}}{{#if commits}}Commits:
{{#each commits}}- {{.}}
{{/each}}{{/if}}{{#if notes}}Decisions and notes:
{{#each notes}}- {{.}}
{{/each}}{{/if}}{{#if pending}}Still to do:
{{#each pending}}- {{.}}
{{/each}}{{/if}}{{#if journal}}Recent activity:
{{#each journal}}- {{.}}
{{/each}}{{/if}}
Rules: check impact before editing a symbol (the edit gate enforces it for critical files), let the hooks keep the graph fresh, run `precommit.ps1` before committing, and commit or push only when the user asks.
