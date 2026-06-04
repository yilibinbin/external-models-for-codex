---
name: claude-leases
description: Inspect, claim, or release advisory Claude for Codex review leases.
---

# Claude Leases

Use this skill when the user wants to coordinate reviewer attention across paths without locking files.

List active leases:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" leases list "$ARGUMENTS"
```

Claim an advisory lease:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" leases claim "$ARGUMENTS"
```

Release a lease:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" leases release "$ARGUMENTS"
```

Rules:
- Leases are advisory; they do not lock files and do not block edits.
- Lease conflicts are warnings only and do not change review verdicts or review-gate behavior.
- Lease paths must remain inside the current workspace.
- Lease data lives under repo-external plugin state.
