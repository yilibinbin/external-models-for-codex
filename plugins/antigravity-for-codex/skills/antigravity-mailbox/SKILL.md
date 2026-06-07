---
name: antigravity-mailbox
description: Use Antigravity for Codex advisory mailbox threads to leave or inspect collaboration notes.
---

# Antigravity Mailbox

Use this skill when Codex needs repo-external advisory notes for Antigravity review collaboration.

List threads:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" mailbox list
```

Post a note:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" mailbox post --thread "$THREAD" --message "$MESSAGE"
```

Show a thread:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" mailbox show --thread "$THREAD"
```
