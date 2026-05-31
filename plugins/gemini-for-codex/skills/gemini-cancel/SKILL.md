---
name: gemini-cancel
description: Cancel a tracked Gemini for Codex background job when the runtime can safely validate it.
---

# Gemini Cancel

Use this skill when Codex needs to cancel a tracked Gemini for Codex job.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" cancel "$ARGUMENTS"
```

Rules:
- Require a job id.
- Do not claim a running process was stopped unless the runtime reports `cancelled`.
- If the runtime reports `cancel_failed`, tell the user that the plugin could not validate a process identity for safe cancellation.
