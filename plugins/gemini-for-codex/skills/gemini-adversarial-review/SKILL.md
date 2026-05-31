---
name: gemini-adversarial-review
description: Use Gemini CLI to challenge Codex's implementation approach, assumptions, tradeoffs, and failure modes.
---

# Gemini Adversarial Review

Use this skill for high-risk changes, architecture decisions, reliability-sensitive code, security-sensitive code, migrations, rollback-sensitive changes, or when Codex may be overconfident.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" adversarial-review "$ARGUMENTS"
```

Background routing:
- Foreground use runs the normal command above.
- If `$ARGUMENTS` contains `--background`, first run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" reserve-job adversarial-review "$ARGUMENTS"
```

- Parse the returned JSON and dispatch exactly one forwarding subagent or child worker with the returned `workerCommand` JSON argv array. The child must execute that array as argv while preserving element boundaries; if forced through a shell, quote every element.
- The child runs `run-reserved-job` once through `workerCommand`; it must not inspect or reinterpret the repository.
- The parent returns the job id immediately and tells the user to use `gemini-result <job-id>`.

Rules:
- This is read-only.
- Gemini must first infer and state the author's intent.
- Gemini reviews through adversarial lenses: `skeptic`, `architect`, and `minimalist`.
- Gemini must output `PASS`, `CONTESTED`, or `REJECT`.
- Gemini must include `Findings`, `What Went Well`, and `Lead Judgment`.
- Use `--json` when Codex needs a machine-checked adversarial verdict object.
- Codex must accept or reject Gemini findings with its own judgment before making follow-up edits.
- Do not apply fixes until the user chooses which findings to adopt.

Arguments:
- `--adversarial-lenses skeptic,architect,minimalist` selects an ordered lens subset.
- `--adversarial-lens skeptic` adds one lens; repeat it to build an ordered lens list.
- `--base <ref>` reviews `ref...HEAD`.
- `--scope auto|working-tree|branch` controls git context.
- `--path <path>` filters review context; repeat for multiple paths.
- `--model <model>` is passed to Gemini CLI.
- `--json` validates Gemini output as `{verdict, summary, findings, next_steps}`.
- `--background` starts a tracked job and returns a job id.
- `--wait` only applies to direct `--background` runtime use. It is not part of the host-forwarded `reserve-job` path, where the parent returns immediately; waiting requires polling or retrieving `gemini-result <job-id>`.

Useful examples:
- `--base main challenge the retry and rollback design`
- `--adversarial-lenses skeptic,minimalist look for correctness gaps and removable complexity`
- `--adversarial-lens architect --adversarial-lens skeptic review the migration boundary`
