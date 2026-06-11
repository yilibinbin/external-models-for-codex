---
name: claude-multi-review
description: Run plugin-managed Claude role fan-out review when the user asks for multiple perspectives, named review roles, multi-agent or multi-role review, SDK subagents, or high-risk multi-role review; do not use this skill for strict-only single review.
---

# Claude Multi Review

Use this skill when Codex needs several independent Claude review passes with different role directives. This is an opt-in fan-out review workflow, not automatic repair. Foreground role reviewers run in parallel by default.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" multi-review "$ARGUMENTS"
```

## Natural-Language Claude Routing

Codex should let the user ask for Claude multi-role review in normal language. Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:
- Default to `--quality auto` for manual Claude review, plan, rescue, and multi-review unless the command documents a stricter default.
- Use this skill when the user asks for multiple perspectives, named review roles, multi-agent review, role fan-out, SDK subagents, or a named reviewer team.
- Treat "strict" alone as review strength, not role fan-out. Strict-only single review belongs to `claude-review`.
- Use `--quality strong` for high-risk, security, release, migration, or adversarial multi-role review when the user does not name a concrete model.
- Use `--quality max` only when the user explicitly asks for the strongest local multi-role Claude review.
- Use `--agent-team sdk-subagents --backend sdk` only when the user explicitly asks for native Claude SDK subagents or native subagent orchestration.
- Use `--background` for more than three roles, large diffs, slow providers, or broad scope.
- Before choosing foreground for an unforced review, run `recommend-execution-mode --json` or apply the same threshold: foreground only for tiny one-to-two-file work; use background for untracked directories, more than two files, more than roughly fifty changed lines, multi-role/adversarial/rescue work, or unclear scope.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.

Internal invocation examples, not for users:
- Role fan-out: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" multi-review --roles correctness,security "$ARGUMENTS"`.
- Native SDK subagents: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" multi-review --backend sdk --agent-team sdk-subagents "$ARGUMENTS"`.
- Background role fan-out: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" reserve-job multi-review --background "$ARGUMENTS"`.
- Strongest local quality: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" multi-review --quality max "$ARGUMENTS"`.
- Model, effort, quality, backend, roles, role-pack, agent-team, and background flags are added outside quoted `$ARGUMENTS`; `$ARGUMENTS` carries only natural-language focus text.

<!--
routing:multi-review
routing:strict-only-to-single-review
routing:role-fanout
routing:sdk-subagents-explicit
routing:background-for-broad-review
-->

User-facing examples:
- "Use Claude for a multi-role release and security review."
- "Use Claude native SDK subagents to review this change."
- "Use the strongest local Claude multi-agent review for this migration."

Internal routing procedure:
- Classify the request as multi-review when the user asks for multiple perspectives, named roles, role fan-out, multi-agent review, native SDK subagents, or reviewer teams.
- If the user explicitly names Fable, Fable 5, or `claude-fable-5`, route with `--quality max` by default so the runtime can use capabilities detection (`best`, then `fable`, then `opus`). Use exact `--model fable` only when the user explicitly asks for an exact model flag or the local capabilities/status output advertises Fable support.
- If the user asks for the strongest, top, best, max, 顶级, 最强, or 不要省成本 local Claude pass without naming Fable, route with `--quality max`; the runtime selects the strongest supported local model and uses Claude Code's native fallback-model only for supported availability failures.
- Do not escalate routine "strict" or "deep" language directly to Fable; use `--quality strong` unless release, security, migration, multi-agent, SDK-subagent, large-diff, or explicit max signals justify `--quality max`.
- Installed Stop hook / review-gate paths stay conservative: they do not auto-select Fable from env or auto scoring, and only manual `review-gate --quality max` can use top-model routing.
- If the request is only strict review with no multi-role signal, use `claude-review`.
- Select roles from requested dimensions when named; otherwise use the documented default role set.
- Use SDK subagents only for explicit native subagent requests, and keep plugin-managed parallel CLI fan-out as the default.
- Add `--quality`, `--model`, `--effort`, `--roles`, `--role-pack`, `--backend`, `--agent-team`, and `--background` only as explicit argv tokens when the request calls for them.

Background routing:
- Foreground use runs the normal command above.
- If `$ARGUMENTS` contains `--background`, first run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" reserve-job multi-review "$ARGUMENTS"
```

- Parse the returned JSON and dispatch exactly one forwarding subagent or child worker with the returned `workerCommand` JSON argv array. The child must execute that array as argv while preserving element boundaries; if forced through a shell, quote every element.
- The child runs `run-reserved-job` once through `workerCommand`; it must not inspect or reinterpret the repository.
- The parent returns the job id immediately and tells the user to use `claude-result <job-id>`.

Codex subagent delegation:
- Use `claude-subagent-review` when a Codex parent wants a general-purpose Codex subagent to run the review/rescue.
- Parent calls concrete `subagent-command multi-review "$ARGUMENTS"` before starting the child.
- Pass the returned `workerCommand` JSON argv to exactly one child.
- Pass the returned `cwd`; the child runs from that exact working directory.
- Do not replace Claude for Codex with raw `claude -p`; it bypasses plugin read-only isolation, Git MCP, reports, and compatibility handling.

Rules:
- This is read-only.
- Claude must not edit files or apply fixes.
- Treat each role output as review findings for Codex to reconcile.
- Role reviewers run in parallel unless `--sequential` is explicitly supplied.
- Codex remains responsible for deciding which findings to adopt, reject, or report as residual risk.
- Preserve role headers, file paths, line numbers, uncertainty markers, failed-role diagnostics, and the orchestration summary.
- Preserve evidence boundaries; do not collapse role-specific uncertainty into a stronger claim.
- Do not fix review findings in the same turn unless the user explicitly asks which findings to adopt.
- Use `--background` for long multi-role reviews through the background routing contract above and retrieve the job with `claude-result`.
- Tiny one-to-two file reviews can run foreground; broader or unclear reviews should use background.
- Do not rerun a multi-review just because a previous `--wait` observation window expired; use `claude-status` or `claude-result <job-id>`.

Default roles:
- `correctness`: bugs, regressions, edge cases, and contract breaks.
- `security`: read-only safety, secrets exposure, injection risks, and unsafe command or path handling.
- `tests`: missing, brittle, or overfit tests and validation gaps.
- `release`: install, marketplace, versioning, documentation, and upgrade risks.
- `adversarial`: assumptions, simpler alternatives, hidden costs, and failure modes.

Additional opt-in adversarial lens roles:
- `skeptic`: correctness, completeness, unproven assumptions, and breakable states.
- `architect`: structure, boundaries, coupling, and design fitness.
- `minimalist`: necessity, complexity, speculative abstraction, and deletable work.

Arguments:
- `--roles <a,b,c>` runs a comma-separated role list in order instead of the defaults.
- `--role <name>` adds one role; repeat it to build an ordered role list.
- `--role-pack <pack>` selects a built-in reviewer preset such as `minimal`, `release`, `security`, or `default`. It conflicts with `--roles` and `--role`.
- `--base <ref>` reviews `ref...HEAD`.
- `--scope auto|working-tree|branch` is passed to the runtime for prompt context.
- `--path <path>` or `--paths <path>` filters git context to one path; repeat it for multiple paths.
- `--quality auto|fast|standard|strong|max` selects adaptive Claude Code aliases and effort. Use `--quality strong` when the user asks for a deeper local Claude pass without naming a concrete model. Use `--quality max` only when the user explicitly asks for the strongest local Claude review.
- `--model <model>` and `--effort <level>` are passed to each Claude CLI invocation.
- `--json` asks every role for a normalized review object and returns one role-tagged aggregate object.
- `--semantic-context <provider>` is optional and off by default. The provider context is fetched once per command and shared across role prompts as advisory context.
- `--parallel` is the default execution mode for foreground role fan-out.
- `--sequential` runs roles one at a time for debugging or rate-limit-sensitive environments.
- `--background` starts a tracked job and returns a job id.
- `--wait` only applies to direct `--background` runtime use. It is not part of the host-forwarded `reserve-job` path, where the parent returns immediately; waiting requires polling or retrieving `claude-result <job-id>`.
- Do not substitute `--quality strong` or `--quality max` with `claude ultrareview`; ultrareview requires the `claude-ultrareview` skill and explicit cost confirmation.

Examples:
- `--base main`
- `--role-pack minimal --scope working-tree`
- `--role-pack release --base main`
- `--roles correctness,security --scope branch --base main`
- `--role release --role adversarial check upgrade and rollback risk`
- `--roles skeptic,architect,minimalist --base main`
