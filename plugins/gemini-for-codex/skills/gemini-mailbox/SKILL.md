---
name: gemini-mailbox
description: Inspect and post sanitized Gemini for Codex mailbox coordination messages.
---

# Gemini Mailbox

Use this skill when Codex needs to inspect sanitized Gemini coordination messages for review jobs or multi-review runs.

List mailbox threads:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" mailbox list "$ARGUMENTS"
```

Show a thread or job mailbox:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" mailbox show "$ARGUMENTS"
```

Post a manual sanitized note:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" mailbox post "$ARGUMENTS"
```

Rules:
- Mailbox messages are sanitized summaries, not transcripts.
- Do not treat mailbox messages as source of truth for code state; use git and review output for that.
- Mailbox storage is repo-external under Gemini for Codex plugin state.
- Mailbox content does not affect review or Stop gate verdicts.
