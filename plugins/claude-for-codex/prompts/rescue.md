<task>Diagnose a stuck or failed Codex implementation from Claude's independent read-only perspective.</task>
{{GIT_CONTEXT}}
{{PROJECT_INSTRUCTIONS_BLOCK}}
<rules>
{{EDIT_RULE}}
{{REPORT_RULE}}
- Identify the likely failure mode, missing context, or incorrect assumption.
- Prefer a short recovery checklist Codex can execute.
- Ground claims in current git state, changed files, and visible evidence.
- If evidence is insufficient, say exactly what Codex should inspect next.
</rules>
{{RESCUE_REQUEST_BLOCK}}
<output_contract>
## Diagnosis
## Evidence
## Recovery Steps
## Risks
</output_contract>
