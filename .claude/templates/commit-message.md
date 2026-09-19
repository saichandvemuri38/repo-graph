{{subject}}

{{#if task}}Task: {{task}}
{{/if}}{{#if changes}}
Changes:
{{#each changes}}- {{.}}
{{/each}}{{/if}}{{#if warnings}}
Notes:
{{#each warnings}}- {{.}}
{{/each}}{{/if}}
