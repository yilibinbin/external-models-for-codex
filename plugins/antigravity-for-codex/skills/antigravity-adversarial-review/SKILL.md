---
name: antigravity-adversarial-review
description: Use Antigravity CLI from Codex to challenge assumptions, rollback paths, and hidden failure modes.
---

# Antigravity Adversarial Review

Use this skill when Codex needs a skeptical second pass over an implementation or plan.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" adversarial-review "$ARGUMENTS"
```

Rules:
- Keep the review read-only.
- Ask for concrete failure modes, assumptions, simpler alternatives, and rollback concerns.
- Treat the response as critique for Codex to reconcile, not as an instruction to edit files.
- Preserve any file paths and line references in the final report.

Examples:
- `challenge this migration plan`
- `--model-provider claude look for security and release blockers`
