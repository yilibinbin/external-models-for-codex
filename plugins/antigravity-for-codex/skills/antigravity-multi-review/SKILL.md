---
name: antigravity-multi-review
description: Run plugin-managed Antigravity role fan-out review from Codex for higher-risk changes.
---

# Antigravity Multi Review

Use this skill when Codex needs several independent Antigravity review passes with different role directives.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" multi-review "$ARGUMENTS"
```

Rules:
- This is read-only.
- Antigravity must not edit files or apply fixes.
- Treat each role output as review findings for Codex to reconcile.
- Codex remains responsible for deciding which findings to adopt, reject, or report as residual risk.

Default roles:
- `correctness`
- `security`
- `tests`
- `release`
- `adversarial`

Examples:
- `--roles correctness,security --model-provider gemini`
- `--roles release,adversarial check install and rollback risk`
