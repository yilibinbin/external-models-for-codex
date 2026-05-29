---
name: claude-status
description: List Claude for Codex tracked background jobs for the current workspace.
---

# Claude Status

Use this skill when Codex needs to see Claude for Codex job lifecycle state without running a new review.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" jobs "$ARGUMENTS"
```

Rules:
- Treat this as job status only.
- Do not confuse it with the runtime `status` diagnostic command, which checks live Claude agents and setup health.
- If no jobs are listed, say there are no tracked Claude jobs for this workspace.
