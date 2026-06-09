---
name: claude-cancel
description: Cancel a tracked Claude for Codex background job when the runtime can safely validate it.
---

# Claude Cancel

Use this skill when Codex needs to cancel a tracked Claude for Codex job.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" cancel "$ARGUMENTS"
```

Rules:
- Require a job id.
- Do not claim a running process was stopped unless the runtime reports `cancelled`.
- If the runtime reports `cancel_failed`, tell the user that the plugin could not validate a process identity for safe cancellation.
- `cancel_failed`, `suspect`, and `lost` are diagnostic states, not proof of termination.
