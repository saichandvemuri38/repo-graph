# {{task}}

Session `{{id}}` · {{status}} · {{startedAt}} to {{finishedAt}} · branch `{{branch}}`
{{#if description}}

{{description}}
{{/if}}

## Result

- Graph: {{graphDelta}}
- Symbols: {{symbolsModified}} modified, {{symbolsAdded}} added, {{symbolsRemoved}} removed
- Files touched: {{fileCount}}
{{#if acceptance}}

## Done when

{{#each acceptance}}- {{.}}
{{/each}}{{/if}}{{#if files}}

## Files

{{#each files}}- {{.}}
{{/each}}{{/if}}{{#if commits}}

## Commits

{{#each commits}}- {{.}}
{{/each}}{{/if}}{{#if pushes}}

## Pushes

{{#each pushes}}- {{.}}
{{/each}}{{/if}}{{#if tests}}

## Tests run

{{#each tests}}- {{.}}
{{/each}}{{/if}}{{#if notes}}

## Decisions and notes

{{#each notes}}- {{.}}
{{/each}}{{/if}}
