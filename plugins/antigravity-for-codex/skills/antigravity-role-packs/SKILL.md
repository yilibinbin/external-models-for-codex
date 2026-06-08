---
name: antigravity-role-packs
description: List and use Antigravity for Codex role packs for focused multi-review teams.
---

# Antigravity Role Packs

Use this skill when the user asks which Antigravity reviewer teams are available or wants a focused multi-review.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" roles --json
```

For release review:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" multi-review --role-pack release "$ARGUMENTS"
```
