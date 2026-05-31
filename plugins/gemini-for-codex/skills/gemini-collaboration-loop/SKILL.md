---
name: gemini-collaboration-loop
description: "Run a full Codex-Gemini collaboration workflow: Codex plans, Gemini plans, Codex reconciles, Codex implements, Gemini reviews, Codex reports."
---

# Gemini Collaboration Loop

Use this skill for complex, high-stakes, or ambiguous tasks where Codex and Gemini should cover each other's blind spots.

Workflow:
1. Codex reads repo state and writes or updates `task_plan.md`, `findings.md`, and `progress.md` when file-backed planning applies.
2. Codex runs:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" plan "$ARGUMENTS"
```

3. Codex reconciles Gemini's plan against local evidence:
   - adopt concrete missing tests,
   - adopt safer task ordering when justified,
   - reject unsupported speculation,
   - record the reconciliation in `findings.md`.
4. Codex implements the reconciled plan.
5. Codex runs:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" adversarial-review "$ARGUMENTS"
```

6. Codex reports:
   - implemented files,
   - verification commands,
   - Gemini findings adopted,
   - Gemini findings rejected,
   - residual risk.

Hard boundaries:
- Gemini review output is not self-executing.
- Codex must not claim a Gemini finding is fixed unless it applied and verified the fix.
- If Gemini CLI is unavailable, fall back to a Codex-only workflow and report that the cross-model pass was skipped.
