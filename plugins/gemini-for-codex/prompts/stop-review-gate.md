<task>Run a stop-gate review of the current git changes.</task>
<role_name>{{ROLE_NAME}}</role_name>
<role_directive>{{ROLE_DIRECTIVE}}</role_directive>
{{GIT_CONTEXT}}
{{GEMINI_CONTEXT}}
<rules>
- Do not edit files.
- Do not suggest that you are about to apply fixes.
- Review only the current git working-tree changes shown in the git context.
- Use BLOCK only for concrete issues that should prevent stopping now.
- Use ALLOW if you do not see a blocking issue for this role.
- Ground every BLOCK claim in concrete changed-file evidence when possible.
</rules>
<output_contract>
Your first line must be exactly one of:
ALLOW: <short reason>
BLOCK: <short reason>
Do not put anything before that first line.
After the first line, include concise evidence for BLOCK results.
</output_contract>
