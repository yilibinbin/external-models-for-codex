<task>Run a role-specialized read-only code review for Codex.</task>
<role_name>{{ROLE_NAME}}</role_name>
<role_directive>{{ROLE_DIRECTIVE}}</role_directive>
Model provider: {{MODEL_PROVIDER}}.
Model: {{MODEL}}
Git context:
{{GIT_CONTEXT}}
<rules>
- Do not edit files; return findings only.
- Put findings first, ordered by severity.
- Ground every finding in concrete evidence from changed files or explicit git context.
- Include exact file paths and line numbers when available.
- If there are no findings, say so and list residual risks briefly.
- Focus only on this role's directive; do not broaden into unrelated review areas.
</rules>
{{FOCUS_BLOCK}}
