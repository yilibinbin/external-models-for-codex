---
name: claude-ultrareview
description: Run explicit Claude cloud ultrareview from Codex only after user consent for remote execution and possible usage-credit billing.
---

# Claude Ultrareview

Use this skill only when the user explicitly asks Codex to run Claude ultrareview and has consented to remote/cloud execution and possible usage-credit billing.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" ultrareview --confirm-cost "$ARGUMENTS"
```

Rules:
- This command uses Claude remote/cloud execution and may bill usage credits.
- Never run ultrareview from hooks, automatic defaults, or a default backend.
- Preserve stdout as the review findings.
- Preserve stderr as progress, diagnostics, and any session URL.
- Use `--json` only when the user asks for machine-readable output.
- Do not remove `--confirm-cost`; the wrapper consumes it and does not forward it to Claude.
