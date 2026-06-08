<task>Create an independent implementation plan for Codex to compare against its own plan.</task>
Model provider: {{MODEL_PROVIDER}}.
Model: {{MODEL}}
Git context:
{{GIT_CONTEXT}}
<rules>
- Do not edit files; return findings only.
- Separate observed facts from inferences.
- Prefer small verifiable implementation steps.
- Include tests for each meaningful behavior or risk area.
- Identify risks, blind spots, rollback concerns, and unresolved assumptions.
- End with a reconciliation checklist Codex can use against its own plan.
</rules>
{{FOCUS_BLOCK}}
<output_contract>
## Observed Facts
## Inferences
## Independent Implementation Plan
## Tests
## Risks And Blind Spots
## Reconciliation Checklist
</output_contract>
