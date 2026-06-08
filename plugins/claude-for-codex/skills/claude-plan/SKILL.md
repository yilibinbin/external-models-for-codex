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

## Natural-Language Claude Routing

<!--
routing:plan
routing:codex-owns-final-plan
-->

- Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.
- Default to `--quality auto` for manual Claude review, plan, rescue, and multi-review unless the command documents a stricter default.
- Use `--quality strong` for deep, strict, high-risk, migration, release, or difficult diagnosis/planning requests.
- Use `--quality max` only when the user explicitly asks for the strongest local Claude pass.
- If the user names a concrete Claude model or effort, pass it as explicit argv tokens outside quoted `$ARGUMENTS`.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.

User-facing examples:
- "Ask Claude to make an independent implementation plan."
- "Use Claude for a strict migration plan review."
- "Use the strongest local Claude planning pass before we edit."

Internal routing procedure:
- Classify the user's intent first, then invoke the narrowest Claude for Codex command that satisfies it.
- Route implementation decomposition, test ordering, migration planning, and risk sequencing to `plan`.
- Translate explicit strength, model, effort, backend, role, or background-job requests into argv tokens outside quoted `$ARGUMENTS`.
- Keep Codex responsible for reading Claude output, judging whether findings are correct, and reconciling the final answer or implementation plan.

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
