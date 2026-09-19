# Pre-commit check: {{verdict}}

Task: {{task}}
Graph risk of these changes: **{{risk}}** ({{affected}} dependant symbol(s), {{flows}} flow(s)) versus {{baseline}}.
{{#if blockers}}

## Fix before committing

{{#each blockers}}- {{.}}
{{/each}}{{/if}}{{#if warnings}}

## Look at these

{{#each warnings}}- {{.}}
{{/each}}{{/if}}

## What changed

{{#each files}}- {{.}}
{{/each}}{{#if symbols}}
Symbols:
{{#each symbols}}- {{.}}
{{/each}}{{/if}}{{#if tests}}

## Run these tests

{{#each tests}}- {{.}}
{{/each}}{{/if}}{{#if security}}

## Security leads (Python)

{{#each security}}- {{.}}
{{/each}}{{/if}}
Commit message draft: `{{draftPath}}`
