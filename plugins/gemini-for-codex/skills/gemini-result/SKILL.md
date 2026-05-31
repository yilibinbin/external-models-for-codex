---
name: gemini-result
description: Retrieve a tracked Gemini for Codex job result by job id.
---

# Gemini Result

Use this skill when Codex needs to fetch the stored output for a Gemini for Codex background job.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" result "$ARGUMENTS"
```

Rules:
- Require a job id from the user or from a previous `gemini-status`/`jobs` output.
- Preserve the job status, result text, and any recorded failure diagnostics.
- If the job is not found, report that no tracked job exists for that id in the current workspace.
