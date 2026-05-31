---
name: gemini-status
description: List Gemini for Codex tracked background jobs for the current workspace.
---

# Gemini Status

Use this skill when Codex needs to see Gemini for Codex job lifecycle state without running a new review.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" jobs "$ARGUMENTS"
```

Rules:
- Treat this as job status only.
- Do not confuse it with the runtime `status` diagnostic command, which checks live Gemini agents and setup health.
- If no jobs are listed, say there are no tracked Gemini jobs for this workspace.
