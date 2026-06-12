<task>Run a read-only code review.</task>
{{GIT_CONTEXT}}
{{SEMANTIC_CONTEXT_BLOCK}}
{{PROJECT_INSTRUCTIONS_BLOCK}}
{{REVIEW_ROLES_BLOCK}}
<rules>
- Do not edit files.
- Do not suggest that you are about to apply fixes.
- Put findings first, ordered by severity.
- Ground every finding in concrete evidence from changed files or explicit git context.
- Include exact file paths and line numbers when available.
- If there are no findings, say so and list residual risks briefly.
- Focus on concrete bugs, regressions, missing tests, and maintainability risks.
</rules>
{{FOCUS_BLOCK}}
{{OUTPUT_CONTRACT}}
