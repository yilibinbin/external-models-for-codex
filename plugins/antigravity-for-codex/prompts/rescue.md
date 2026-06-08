<task>Diagnose a stuck or failed Codex implementation from an independent read-only perspective.</task>
Model provider: {{MODEL_PROVIDER}}.
Model: {{MODEL}}
Git context:
{{GIT_CONTEXT}}
<rules>
- Do not edit files.
- Identify the likely failure mode, missing context, or incorrect assumption.
- Prefer a short recovery checklist Codex can execute.
- Ground claims in current git state, changed files, and visible evidence.
- If evidence is insufficient, say exactly what Codex should inspect next.
</rules>
{{FOCUS_BLOCK}}
<output_contract>
## Diagnosis
## Evidence
## Recovery Steps
## Risks
</output_contract>
