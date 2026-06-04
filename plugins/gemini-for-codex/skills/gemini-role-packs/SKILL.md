---
name: gemini-role-packs
description: Inspect Gemini for Codex reviewer role packs and run built-in Gemini review teams.
---

# Gemini Role Packs

Use this skill when Codex needs a named Gemini reviewer team instead of manually listing roles.

List packs:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" roles list "$ARGUMENTS"
```

Inspect a built-in pack:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" roles inspect "$ARGUMENTS"
```

Run a built-in pack:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" multi-review --role-pack "$ARGUMENTS"
```

Rules:
- Role packs are Gemini for Codex reviewer presets, not Gemini extensions.
- Built-in packs can run through normal parallel Gemini CLI fan-out or `multi-review --native-agents --role-pack <pack>`.
- User-authored role packs are validate/inspect-only in this release; do not execute `--role-pack-file`.
- Treat role-pack output as review findings, not implementation instructions.
- Preserve the selected pack name, role list, and residual risks when reporting results.
- `review-gate --role-pack <pack>` is manual-only and rejects gate-incompatible packs before Gemini is called.

Common packs:
- `minimal`: correctness only.
- `release`: release, tests, correctness, and security.
- `security`: security, correctness, and adversarial.
- `default`: the existing default multi-review role set.
