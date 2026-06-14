<task>Run a read-only code review.</task>
{{GIT_CONTEXT}}
{{GEMINI_CONTEXT}}
{{PROJECT_INSTRUCTIONS_BLOCK}}
{{VALIDATION_EVIDENCE_BLOCK}}
{{REVIEW_ROLES}}
<rules>
- Do not edit files.
- Do not suggest that you are about to apply fixes.
- Put findings first, ordered by severity.
- Ground every finding in concrete evidence from changed files or explicit git context.
- Treat `<gemini_context>` as advisory support only; never cite provider context as the sole evidence for a finding.
- If context is unavailable or degraded, say so in residual risk when relevant.
- Include exact file paths and line numbers when available.
- If there are no findings, say so and list residual risks briefly.
- Focus on concrete bugs, regressions, missing tests, and maintainability risks.
</rules>
{{FOCUS}}
{{SCORECARD_BLOCK}}
{{OUTPUT_CONTRACT}}
