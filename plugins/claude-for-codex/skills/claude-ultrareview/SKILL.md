---
name: claude-ultrareview
description: Run explicit Claude cloud ultrareview from Codex only after user consent for remote execution and possible usage-credit billing.
---

# Claude Ultrareview

Use this skill only when the user explicitly asks Codex to run Claude ultrareview and has consented to remote/cloud execution and possible usage-credit billing.

Run:

```bash
CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1 node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" ultrareview "$ARGUMENTS"
```

Rules:
- This command uses Claude remote/cloud execution and may bill usage credits.
- Never run ultrareview from hooks, automatic defaults, or a default backend.
- Preserve stdout as the review findings.
- Preserve stderr as progress, diagnostics, and any session URL.
- Use `--json` only when the user asks for machine-readable output.
- Do not remove `CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1`; it is explicit consent for remote/cloud execution and possible usage-credit billing, and the wrapper does not forward it to Claude.
