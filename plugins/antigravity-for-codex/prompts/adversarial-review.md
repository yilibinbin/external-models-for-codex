<task>Run an adversarial read-only code and design review for Codex.</task>
Model provider: {{MODEL_PROVIDER}}.
Model: {{MODEL}}
Git context:
{{GIT_CONTEXT}}
{{PROJECT_INSTRUCTIONS_BLOCK}}
{{VALIDATION_EVIDENCE_BLOCK}}
<rules>
- Do not edit files; return findings only.
- First infer the author's intent from the request, focus text, git context, and changed files.
- Challenge whether the work achieves that intent well.
- Find real problems, not validation or style preferences.
- Ground every finding in concrete evidence from changed files or explicit git context.
- Include exact file paths and line numbers when available.
- Deduplicate overlapping concerns.
- Use PASS only when there are no high-severity findings.
- Use CONTESTED when high-severity findings exist but evidence is mixed.
- Use REJECT when high-severity findings have strong evidence.
</rules>
{{FOCUS_BLOCK}}
{{SCORECARD_BLOCK}}
{{OUTPUT_CONTRACT}}
