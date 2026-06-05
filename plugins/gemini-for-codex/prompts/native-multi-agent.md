<task>Run a native Gemini subagent multi-review.</task>
<subagents>
{{SUBAGENTS}}
</subagents>
{{GIT_CONTEXT}}
{{GEMINI_CONTEXT}}
<rules>
- Dispatch these Gemini subagents in parallel when possible: {{AGENT_CALLS}}.
- Each subagent must review independently through its own role directive.
- Do not edit files.
- Do not suggest that you are about to apply fixes.
- Ground every finding in concrete changed-file evidence or explicit git context.
- Treat `<gemini_context>` as advisory support only; never cite provider context as the sole evidence for a finding.
- If context is unavailable or degraded, say so in residual risk when relevant.
- Preserve each subagent's findings under a role header.
- After subagent results, write one orchestration summary with roles requested, roles with findings, and residual risks.
</rules>
{{FOCUS}}
{{OUTPUT_CONTRACT}}
