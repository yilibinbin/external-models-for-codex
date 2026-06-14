---
name: claude-collaboration-loop
description: "Run a full Codex-Claude collaboration workflow: Codex plans, Claude plans, Codex reconciles, Codex implements, Claude reviews, Codex reports."
---

# Claude Collaboration Loop

Use this skill for complex, high-stakes, or ambiguous tasks where Codex and Claude should cover each other's blind spots.

Workflow:
1. Codex reads repo state and writes or updates `task_plan.md`, `findings.md`, and `progress.md` when file-backed planning applies.
2. Codex runs:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" plan "$ARGUMENTS"
```

3. Codex reconciles Claude's plan against local evidence:
   - adopt concrete missing tests,
   - adopt safer task ordering when justified,
   - reject unsupported speculation,
   - record the reconciliation in `findings.md`.
4. Codex implements the reconciled plan.
5. Codex runs:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" adversarial-review "$ARGUMENTS"
```

6. If the user explicitly asks for a bounded quality-feedback loop, Codex runs:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" assisted-review --scorecard --max-review-rounds 2 "$ARGUMENTS"
```

7. Codex reports:
   - implemented files,
   - verification commands,
   - Claude findings adopted,
   - Claude findings rejected,
   - residual risk.

## Natural-Language Claude Routing

<!--
routing:collaboration-loop
routing:codex-reconciles-claude
-->

- Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.
- Keep Codex responsible for reconciling Claude output before edits.
- Use this workflow when the user wants Codex and Claude to plan, implement, review, and reconcile together.
- Keep Claude findings advisory until Codex verifies them against local evidence.
- Use `assisted-review` only for explicit bounded quality-feedback-loop requests; it does not edit files or run project commands.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.

User-facing examples:
- "Run a Codex-Claude collaboration loop on this feature."
- "Have Claude plan first, then Codex reconcile and implement."
- "Use Claude to review after Codex completes the plan."

Internal routing procedure:
- Classify the user's intent first, then invoke the narrowest Claude for Codex command that satisfies it.
- Use this skill only when the user asks for a full collaboration loop, not for a single review or plan.
- Translate explicit strength, model, effort, backend, role, or background-job requests into argv tokens outside quoted `$ARGUMENTS`.
- Keep Codex responsible for reading Claude output, judging whether findings are correct, and reconciling the final answer or implementation plan.

Hard boundaries:
- Claude review output is not self-executing.
- Codex must not claim a Claude finding is fixed unless it applied and verified the fix.
- If Claude CLI is unavailable, fall back to a Codex-only workflow and report that the cross-model pass was skipped.
