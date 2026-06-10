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
- Report `lifecycle.state`, `phase`, and the latest `progressPreview` when present.
- Treat `suspect` as "worker heartbeat is stale but the plugin has not proven failure"; do not rerun the same job automatically.
- Treat `lost` as "the plugin can no longer prove a live worker"; ask the user whether to inspect, cancel, or rerun.
- Treat phase `unsafe-child-identity` as a PID-reuse safety hold: inspect the live child process and use `cancel <job-id>` or manual cleanup before resubmitting.
