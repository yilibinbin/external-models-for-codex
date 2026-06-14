<task>Run an adversarial read-only code and design review.</task>
{{GIT_CONTEXT}}
{{GEMINI_CONTEXT}}
{{PROJECT_INSTRUCTIONS_BLOCK}}
{{VALIDATION_EVIDENCE_BLOCK}}
{{ADVERSARIAL_LENSES}}
<scale_guidance>
If the diff is small, emphasize Skeptic findings.
If the diff is medium, weigh Skeptic and Architect findings.
If the diff is large or spans many files, use Skeptic, Architect, and Minimalist lenses.
When explicit lenses are provided, use only those lenses.
Small means fewer than 50 changed lines across one or two files; medium means roughly 50 to 200 changed lines or three to five files; large means more than 200 changed lines or more than five files.
</scale_guidance>
<rules>
- Do not edit files.
- Do not suggest that you are about to apply fixes.
- First infer the author's intent from the request, focus text, git context, and changed files.
- Challenge whether the work achieves that intent well.
- Find real problems, not validation or style preferences.
- Ground every finding in concrete evidence from changed files or explicit git context.
- Include exact file paths and line numbers when available.
- Deduplicate overlapping lens findings.
- Apply lead judgment: accept strong findings and reject false positives or overreach.
- Use PASS only when there are no high-severity findings.
- Use CONTESTED when high-severity findings exist but lens evidence or agreement is mixed.
- Use REJECT when high-severity findings have strong evidence or consensus across lenses.
</rules>
{{FOCUS}}
{{SCORECARD_BLOCK}}
{{OUTPUT_CONTRACT}}
