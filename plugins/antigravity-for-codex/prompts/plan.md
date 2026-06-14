<task>Create an independent implementation plan for Codex to compare against its own plan.</task>
Model provider: {{MODEL_PROVIDER}}.
Model: {{MODEL}}
Git context:
{{GIT_CONTEXT}}
{{PROJECT_INSTRUCTIONS_BLOCK}}
{{VALIDATION_EVIDENCE_BLOCK}}
<rules>
- Do not edit files; return findings only.
- Separate observed facts from inferences.
- Prefer small verifiable implementation steps.
- Include tests for each meaningful behavior or risk area.
- Identify risks, blind spots, rollback concerns, and unresolved assumptions.
- End with a reconciliation checklist Codex can use against its own plan.
</rules>
{{FOCUS_BLOCK}}
{{TASKSET_BLOCK}}
{{OUTPUT_CONTRACT}}
