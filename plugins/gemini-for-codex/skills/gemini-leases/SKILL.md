---
name: gemini-leases
description: Inspect and manage advisory Gemini for Codex path leases.
---

# Gemini Leases

Use this skill when Codex needs to inspect or clean advisory path-attention leases created by Gemini review workflows.

List active leases:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" leases list "$ARGUMENTS"
```

Claim an advisory lease:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" leases claim "$ARGUMENTS"
```

Release a lease:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" leases release "$ARGUMENTS"
```

Rules:
- Leases are advisory only; they do not lock files and do not change review verdicts.
- Lease conflicts are warnings for coordination, not blockers.
- Lease paths must remain inside the current workspace.
- Use `leases release <lease-id>` for stale local cleanup.
