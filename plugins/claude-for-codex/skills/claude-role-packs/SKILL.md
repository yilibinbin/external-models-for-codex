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

Rules:
- Built-in role packs are executable.
- User-authored role packs are validate/inspect-only in `0.12.0`; do not execute them.
- Role packs are plugin-managed reviewer presets, not native Claude subagents.
- Role packs cannot grant tools, shell commands, hooks, environment variables, MCP servers, backend mode, or write permissions.
- `--role-pack` conflicts with `--roles` and `--role`.
- `review-gate --role-pack default` is rejected in `0.12.0`; bare `review-gate` keeps the existing default behavior.

Useful built-ins:
- `minimal`: correctness only.
- `release`: release, tests, correctness, security.
- `security`: security, correctness, adversarial.
- `default`: the current default multi-review role order.
