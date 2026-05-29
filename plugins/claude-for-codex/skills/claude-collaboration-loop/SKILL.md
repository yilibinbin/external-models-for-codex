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

6. Codex reports:
   - implemented files,
   - verification commands,
   - Claude findings adopted,
   - Claude findings rejected,
   - residual risk.

Hard boundaries:
- Claude review output is not self-executing.
- Codex must not claim a Claude finding is fixed unless it applied and verified the fix.
- If Claude CLI is unavailable, fall back to a Codex-only workflow and report that the cross-model pass was skipped.
