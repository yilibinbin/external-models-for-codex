<task>Run a native Gemini subagent multi-review.</task>
<subagents>
{{SUBAGENTS}}
</subagents>
{{GIT_CONTEXT}}
<rules>
- Dispatch these Gemini subagents in parallel when possible: {{AGENT_CALLS}}.
- Each subagent must review independently through its own role directive.
- Do not edit files.
- Do not suggest that you are about to apply fixes.
- Ground every finding in concrete changed-file evidence or explicit git context.
- Preserve each subagent's findings under a role header.
- After subagent results, write one orchestration summary with roles requested, roles with findings, and residual risks.
</rules>
{{FOCUS}}
<output_contract>
# Gemini Native Subagent Review
## Role: <role name>
## Orchestration Summary
</output_contract>
