---
name: claude-plan-review
description: Use Claude for Codex to review an implementation plan file with read-only role agents.
---

# Claude Plan Review

Use when the user asks Claude to review, challenge, audit, or approve a saved implementation plan.

## Command

From the repository root:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" plan-review --plan "<path-to-plan>" --json
```

For explicit Claude SDK native subagents:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" plan-review --plan "<path-to-plan>" --backend sdk --agent-team sdk-subagents --json --native-structured
```

## Policy

- Use this for plan files, not for implemented code diffs.
- Keep ultrareview out unless the user explicitly requests ultrareview by name and accepts cost.
- Prefer `--quality auto`; use stronger quality only when the user asks for a strict or high-risk review.
- The plan file must be inside the current workspace.
- Codex must judge Claude findings before accepting them.

## Natural-Language Claude Routing

Codex should let the user ask for Claude plan review in normal language. Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:

- Use this skill when the user asks Claude to review, challenge, audit, approve, or adversarially inspect a saved implementation plan file.
- Require an explicit plan file path or a clearly identified plan file in the workspace.
- Default to `--quality auto` unless the user explicitly asks for the strongest local Claude pass or the plan is a high-risk release/security/migration plan.
- Use `--backend sdk --agent-team sdk-subagents` only when the user explicitly asks for Claude SDK native subagents or native subagent orchestration.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.
- Keep Codex responsible for reconciling Claude output before edits.

Internal invocation examples, not for users:

- Saved plan review: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" plan-review --plan "$PLAN_FILE" --json`.
- Native SDK subagents: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" plan-review --plan "$PLAN_FILE" --backend sdk --agent-team sdk-subagents --json --native-structured`.

<!--
routing:plan-review
-->

User-facing examples:

- "Ask Claude to review this implementation plan."
- "Use Claude to challenge the saved release plan."
- "Have Claude native subagents review this plan file."

Internal routing procedure:

- Identify the workspace-relative plan file and pass it with `--plan`.
- If the user explicitly asks for native SDK subagents, add `--backend sdk --agent-team sdk-subagents --json --native-structured`.
- Do not add ultrareview unless the claude-ultrareview skill is selected and the user explicitly accepts cost.
- Return Claude findings to Codex for judgment; do not auto-apply changes from the plan review.
