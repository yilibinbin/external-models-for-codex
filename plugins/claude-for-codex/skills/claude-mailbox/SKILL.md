---
name: claude-mailbox
description: Inspect or post sanitized Claude for Codex mailbox coordination summaries.
---

# Claude Mailbox

Use this skill when the user wants to inspect or add sanitized coordination notes for Claude for Codex jobs or multi-review runs.

List mailbox threads:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" mailbox list "$ARGUMENTS"
```

Show one thread:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" mailbox show "$ARGUMENTS"
```

Post a manual note:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" mailbox post "$ARGUMENTS"
```

Rules:
- Mailbox stores sanitized summaries only, not transcripts.
- It must not contain raw prompts, diffs, source snippets, stdout/stderr, secrets, or raw absolute workspace paths.
- Mailbox data lives under repo-external plugin state.
- It is coordination metadata; it does not decide review verdicts.
