---
name: claude-role-packs
description: Inspect and validate Claude for Codex reviewer role packs, or select a built-in pack for multi-review.
---

# Claude Role Packs

Use this skill when the user wants a named Claude reviewer team instead of spelling out each role.

List built-in packs:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" roles list
```

Inspect one pack:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" roles inspect "$ARGUMENTS"
```

Validate a user-authored JSON pack:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" roles validate "$ARGUMENTS"
```

Run a built-in pack:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" multi-review --role-pack "$ARGUMENTS"
```

## Natural-Language Claude Routing

<!--
routing:role-packs
routing:role-pack-executable-builtins-only
-->

- Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.
- Keep Codex responsible for reconciling Claude output before edits.
- Route named reviewer-team requests to built-in role packs when an existing pack matches the intent.
- Keep user-authored role packs validate/inspect-only; do not execute them.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.

User-facing examples:
- "List the Claude reviewer teams."
- "Use the release reviewer team for this branch."
- "Validate this custom reviewer pack."

Internal routing procedure:
- Classify the user's intent first, then invoke the narrowest Claude for Codex command that satisfies it.
- Use `roles list` or `roles inspect` for discovery, `roles validate` for user-authored files, and `multi-review --role-pack` only for built-in executable packs.
- Translate explicit strength, model, effort, backend, role, or background-job requests into argv tokens outside quoted `$ARGUMENTS`.
- Keep Codex responsible for reading Claude output, judging whether findings are correct, and reconciling the final answer or implementation plan.

Rules:
- Built-in role packs are executable.
- User-authored role packs are validate/inspect-only; do not execute them.
- Role packs are plugin-managed reviewer presets, not native Claude subagents.
- Role packs cannot grant tools, shell commands, hooks, environment variables, MCP servers, backend mode, or write permissions.
- `--role-pack` conflicts with `--roles` and `--role`.
- `review-gate --role-pack default` is rejected because the gate accepts only gate-compatible packs; bare `review-gate` keeps the existing default behavior.

Useful built-ins:
- `minimal`: correctness only.
- `release`: release, tests, correctness, security.
- `security`: security, correctness, adversarial.
- `default`: the current default multi-review role order.
