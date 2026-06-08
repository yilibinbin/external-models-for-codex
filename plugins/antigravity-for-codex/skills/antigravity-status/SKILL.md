---
name: antigravity-status
description: List Antigravity for Codex background jobs and inspect their current status.
---

# Antigravity Status

Use this skill when Codex needs to list Antigravity background jobs or check whether a queued job has finished.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" jobs
```

For one job:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" status "$JOB_ID"
```
