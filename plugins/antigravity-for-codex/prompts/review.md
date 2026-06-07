<task>Run a read-only code review for Codex.</task>
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
- Focus on concrete bugs, regressions, missing tests, and maintainability risks.
</rules>
{{FOCUS_BLOCK}}
