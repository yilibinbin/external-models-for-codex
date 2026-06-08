---
name: antigravity-rescue
description: Ask Antigravity CLI from Codex for read-only diagnosis when an implementation or validation run is stuck.
---

# Antigravity Rescue

Use this skill when Codex is blocked by failing tests, confusing runtime output, or an implementation dead end.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" rescue "$ARGUMENTS"
```

Rules:
- Keep the rescue pass read-only.
- Provide the observed error, commands already tried, and current git context.
- Treat the response as diagnosis and possible next steps; Codex decides what to change.

Examples:
- `pytest fails after the hook change`
- `--model-provider gemini explain this release-check failure`
