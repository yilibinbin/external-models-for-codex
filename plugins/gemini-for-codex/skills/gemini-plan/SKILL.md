---
name: gemini-plan
description: Ask Gemini CLI for an independent implementation plan that Codex can reconcile before editing.
---

# Gemini Plan

Use this skill before substantial implementation work when a second model's decomposition could expose missed tests, hidden constraints, or a safer order of operations.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" plan "$ARGUMENTS"
```

Rules:
- Treat Gemini's plan as a competing design artifact, not an authority.
- Reconcile Gemini's plan with Codex's local repo evidence before editing.
- Keep the final Codex plan in local planning files when the task uses file-backed planning.
- Do not let Gemini's plan override explicit user instructions.

Output usage:
- Extract observed facts.
- Compare task order against Codex's task plan.
- Add missing tests or risk checks when Gemini found real gaps.
- Reject unsupported suggestions with a short reason.
