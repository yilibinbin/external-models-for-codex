# Gemini Extension And MCP Evaluation

Status: evaluation only

Gemini CLI supports extensions through `gemini-extension.json` and supports MCP configuration through `mcpServers`, `mcp.allowed`, and `mcp.excluded`. Gemini for Codex does not enable these paths by default because Codex review workflows must stay read-only, repo-bounded, and predictable.

## Boundaries

- Do not enable Gemini MCP or Gemini extensions in Stop hooks.
- Do not enable Gemini MCP or Gemini extensions in generated default GitHub Actions workflows.
- Do not load workspace-local MCP servers for review by default.
- Do not pass raw source, prompts, provider output, or hidden tool output into sanitized reports.
- Any future MCP or extension helper must be opt-in, repo-external, read-only, and capability-gated.

## Future Acceptance Criteria

- Config lives outside the reviewed workspace or is selected explicitly by the user.
- Allowed tools are narrowed with `mcp.allowed` or equivalent Gemini CLI controls.
- The runtime reports enabled servers/extensions in sanitized metadata.
- Failure to initialize extension or MCP support fails closed for that optional command and does not affect normal review.
- Stop gate and default CI remain free of extension and MCP execution.
