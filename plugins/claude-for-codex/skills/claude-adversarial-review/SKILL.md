---
name: claude-adversarial-review
description: Use Claude Code to challenge Codex's implementation approach, assumptions, tradeoffs, failure modes, rollback paths, and removable complexity.
---

# Claude Adversarial Review

Use this skill for high-risk changes, architecture decisions, reliability-sensitive code, security-sensitive code, migrations, rollback-sensitive changes, or when Codex may be overconfident.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" adversarial-review "$ARGUMENTS"
```

## Natural-Language Claude Routing

Codex should let the user ask for Claude adversarial review in normal language. Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:
- Default to `--quality auto` for manual Claude review, plan, rescue, and multi-review unless the command documents a stricter default.
- Use this skill when the user asks Claude to challenge assumptions, rollback paths, failure modes, architecture, hidden security risk, or removable complexity.
- Use `--parallel` only when the user asks for independent adversarial lenses or parallel skeptical agents.
- Use `--quality strong` for strict, deep, high-risk, security-sensitive, or release-sensitive adversarial review.
- Use `--quality max` only when the user explicitly asks for the strongest local adversarial Claude review.
- Before choosing foreground for an unforced review, run `recommend-execution-mode --json` or apply the same threshold: foreground only for tiny one-to-two-file work; use background for untracked directories, more than two files, more than roughly fifty changed lines, multi-role/adversarial/rescue work, or unclear scope.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.

Internal invocation examples, not for users:
- Strict adversarial pass: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" adversarial-review --quality strong "$ARGUMENTS"`.
- Parallel lenses: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" adversarial-review --parallel "$ARGUMENTS"`.
- Concrete lenses: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" adversarial-review --adversarial-lenses skeptic,architect "$ARGUMENTS"`.
- Model, effort, quality, lenses, parallel, and background flags are added outside quoted `$ARGUMENTS`; `$ARGUMENTS` carries only natural-language focus text.

<!--
routing:adversarial-review
routing:parallel-lenses-explicit
-->

User-facing examples:
- "Use Claude to challenge this migration plan."
- "Use Claude for a strict adversarial release review."
- "Use independent Claude adversarial lenses on the rollback design."

Internal routing procedure:
- Classify the request as adversarial review when the user asks for challenge, skepticism, hidden risks, rollback concerns, simpler alternatives, or failure modes.
- Use parallel lenses only when the user asks for independent lenses or parallel adversarial agents.
- Preserve the skeptical focus as natural-language text.
- Add model, effort, quality, lens, parallel, and background choices only as explicit argv tokens when the request calls for them.

Background routing:
- Foreground use runs the normal command above.
- If `$ARGUMENTS` contains `--background`, first run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" reserve-job adversarial-review "$ARGUMENTS"
```

- Parse the returned JSON and dispatch exactly one forwarding subagent or child worker with the returned `workerCommand` JSON argv array. The child must execute that array as argv while preserving element boundaries; if forced through a shell, quote every element.
- The child runs `run-reserved-job` once through `workerCommand`; it must not inspect or reinterpret the repository.
- The parent returns the job id immediately and tells the user to use `claude-result <job-id>`.

Codex subagent delegation:
- Use `claude-subagent-review` when a Codex parent wants a general-purpose Codex subagent to run the review/rescue.
- Parent calls concrete `subagent-command adversarial-review "$ARGUMENTS"` before starting the child.
- Pass the returned `workerCommand` JSON argv to exactly one child.
- Pass the returned `cwd`; the child runs from that exact working directory.
- Do not replace Claude for Codex with raw `claude -p`; it bypasses plugin read-only isolation, Git MCP, reports, and compatibility handling.

Rules:
- This is read-only.
- Claude must first infer and state the author's intent.
- Claude reviews through adversarial lenses: `skeptic`, `architect`, and `minimalist`.
- Use `--parallel` when each adversarial lens should run as an independent Claude reviewer process.
- Claude must output `PASS`, `CONTESTED`, or `REJECT`.
- Claude must include `Findings`, `What Went Well`, and `Lead Judgment`.
- Use `--json` when Codex needs a machine-checked adversarial verdict object.
- Codex must accept or reject Claude findings with its own judgment before making follow-up edits.
- Do not apply fixes until the user chooses which findings to adopt.
- Preserve file paths, line numbers, uncertainty markers, residual-risk notes, and evidence boundaries exactly.
- If Claude fails or returns malformed structured output, report that failure instead of replacing it with Codex guesses.
- Do not rerun an adversarial review just because a previous `--wait` observation window expired; use `claude-status` or `claude-result <job-id>`.

Arguments:
- `--adversarial-lenses skeptic,architect,minimalist` selects an ordered lens subset.
- `--adversarial-lens skeptic` adds one lens; repeat it to build an ordered lens list.
- `--base <ref>` reviews `ref...HEAD`.
- `--scope auto|working-tree|branch` controls git context.
- `--path <path>` filters review context; repeat for multiple paths.
- `--model <model>` and `--effort <level>` are passed to Claude CLI.
- `--parallel` runs selected adversarial lenses as independent Claude reviewer processes and aggregates their outputs.
- `--sequential` keeps the single-call adversarial review path.
- `--json` validates Claude output as `{verdict, summary, findings, next_steps}`.
- `--json` keeps the adversarial verdict vocabulary `PASS|CONTESTED|REJECT`; it is intentionally separate from normal `review --json`.
- `--semantic-context <provider>` is optional and off by default. Treat semantic context as advisory; adversarial findings still need concrete changed-file or git evidence.
- `--json` is only supported on the single-call path; do not combine it with `--parallel`.
- `--background` starts a tracked job and returns a job id.
- `--wait` only applies to direct `--background` runtime use. It is not part of the host-forwarded `reserve-job` path, where the parent returns immediately; waiting requires polling or retrieving `claude-result <job-id>`.

Useful examples:
- `--base main challenge the retry and rollback design`
- `--adversarial-lenses skeptic,minimalist look for correctness gaps and removable complexity`
- `--adversarial-lens architect --adversarial-lens skeptic review the migration boundary`
