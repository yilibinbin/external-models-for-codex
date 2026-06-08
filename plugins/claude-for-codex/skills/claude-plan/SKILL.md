---
name: claude-plan
description: Ask Claude Code for an independent implementation plan that Codex can reconcile before editing.
---

# Claude Plan

Use this skill before substantial implementation work when a second model's decomposition could expose missed tests, hidden constraints, or a safer order of operations.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" plan "$ARGUMENTS"
```

Rules:
- Treat Claude's plan as a competing design artifact, not an authority.
- Reconcile Claude's plan with Codex's local repo evidence before editing.
- Keep the final Codex plan in local planning files when the task uses file-backed planning.
- Do not let Claude's plan override explicit user instructions.
- Use `--quality strong` for a deeper local Claude planning pass without naming a concrete model. Use `--quality max` only when the user explicitly asks for the strongest local Claude pass.
- Do not substitute `--quality strong` or `--quality max` with `claude ultrareview`; ultrareview requires the `claude-ultrareview` skill and explicit cost confirmation.

Output usage:
- Extract observed facts.
- Compare task order against Codex's task plan.
- Add missing tests or risk checks when Claude found real gaps.
- Reject unsupported suggestions with a short reason.
