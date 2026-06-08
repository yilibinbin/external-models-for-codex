---
name: antigravity-leases
description: Use Antigravity for Codex advisory leases to coordinate review roles without writing into the repository.
---

# Antigravity Leases

Use this skill when multiple Antigravity review roles need lightweight advisory coordination.

Claim a lease:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" leases claim --role "$ROLE" --ttl-seconds 900
```

List active leases:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" leases list
```

Release a lease:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" leases release --id "$LEASE_ID"
```
