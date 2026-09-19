## Summary

{{task}}
{{#if description}}

{{description}}
{{/if}}

## What changed

{{#each files}}- {{.}}
{{/each}}
Symbols: {{symbolsModified}} modified, {{symbolsAdded}} added, {{symbolsRemoved}} removed. Risk from the code graph: **{{risk}}** ({{affected}} dependants, {{flows}} flows).
{{#if tests}}

## Testing

{{#each tests}}- {{.}}
{{/each}}{{/if}}{{#if notes}}

## Notes for reviewers

{{#each notes}}- {{.}}
{{/each}}{{/if}}
