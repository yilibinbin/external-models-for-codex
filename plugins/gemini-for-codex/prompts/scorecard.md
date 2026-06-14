<scorecard_contract>
Return only JSON matching the scorecard schema.
This scorecard contract overrides any earlier Markdown, "findings first", progress-update, or narrative output instructions.
Do not say you are starting, running, or completing a background review; perform the review inside this response and emit only the final JSON object.
Use verdict "approve" only when there are no blocking findings.
Recompute evidence from changed files and explicit git context; do not invent test results.
Use dimensions correctness, tests, code_quality, security, and performance with weights 0.35, 0.25, 0.20, 0.10, and 0.10.
Always include tests.exempt and tests.exemption_reason. If tests are applicable, set tests.exempt=false and tests.exemption_reason="". If tests are not applicable, set tests.exempt=true, tests.score=100, and provide exemption_reason.
Every blocking finding must include concrete evidence.

<scorecard_schema_json>
{{SCORECARD_OUTPUT_SCHEMA_JSON}}
</scorecard_schema_json>
</scorecard_contract>
