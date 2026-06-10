---
name: claude-result
description: Retrieve a tracked Claude for Codex job result by job id.
---

# Claude Result

Use this skill when Codex needs to fetch the stored output for a Claude for Codex background job.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" result "$ARGUMENTS"
```

Rules:
- Require a job id from the user or from a previous `claude-status`/`jobs` output.
- Preserve the job status, result text, and any recorded failure diagnostics.
- Treat stored `stdout`/`stderr` as sanitized and capped. If `stdoutTruncated` or `stderrTruncated` is true, tell the user the stored output is shortened and include the byte metadata rather than implying the visible text is complete.
- If the job is still `running`, preserve `waitTimedOut`, `lifecycle.state`, and progress fields; do not claim the review finished and do not start a replacement review.
- If the job is not found, report that no tracked job exists for that id in the current workspace.
