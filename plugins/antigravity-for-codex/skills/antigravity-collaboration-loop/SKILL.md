---
name: antigravity-collaboration-loop
description: Run Antigravity for Codex multi-review with advisory mailbox and leases for coordinated external-model review.
---

# Antigravity Collaboration Loop

Use this skill when Codex needs a coordinated Antigravity review pass with advisory state.

Run a coordinated review:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" multi-review --use-mailbox --advisory-leases
```

Inspect unread background results:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" jobs
```
