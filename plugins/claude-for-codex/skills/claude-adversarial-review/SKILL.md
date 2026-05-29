---
name: claude-adversarial-review
description: Use Claude Code to challenge Codex's implementation approach, assumptions, tradeoffs, and failure modes.
---

# Claude Adversarial Review

Use this skill for high-risk changes, architecture decisions, reliability-sensitive code, security-sensitive code, migrations, rollback-sensitive changes, or when Codex may be overconfident.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" adversarial-review "$ARGUMENTS"
```

Rules:
- This is read-only.
- Claude must first infer and state the author's intent.
- Claude reviews through adversarial lenses: `skeptic`, `architect`, and `minimalist`.
- Claude must output `PASS`, `CONTESTED`, or `REJECT`.
- Claude must include `Findings`, `What Went Well`, and `Lead Judgment`.
- Use `--json` when Codex needs a machine-checked adversarial verdict object.
- Codex must accept or reject Claude findings with its own judgment before making follow-up edits.
- Do not apply fixes until the user chooses which findings to adopt.

Arguments:
- `--adversarial-lenses skeptic,architect,minimalist` selects an ordered lens subset.
- `--adversarial-lens skeptic` adds one lens; repeat it to build an ordered lens list.
- `--base <ref>` reviews `ref...HEAD`.
- `--scope auto|working-tree|branch` controls git context.
- `--path <path>` filters review context; repeat for multiple paths.
- `--model <model>` and `--effort <level>` are passed to Claude CLI.
- `--json` validates Claude output as `{verdict, summary, findings, next_steps}`.
- `--background` starts a tracked job and returns a job id.
- `--wait` waits for a background job to finish before returning.

Useful examples:
- `--base main challenge the retry and rollback design`
- `--adversarial-lenses skeptic,minimalist look for correctness gaps and removable complexity`
- `--adversarial-lens architect --adversarial-lens skeptic review the migration boundary`
