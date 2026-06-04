---
name: claude-multi-review
description: Opt in to plugin-managed role fan-out Claude review from Codex for high-risk changes that need multiple read-only perspectives.
---

# Claude Multi Review

Use this skill when Codex needs several independent Claude review passes with different role directives. This is an opt-in fan-out review workflow, not automatic repair. Foreground role reviewers run in parallel by default.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" multi-review "$ARGUMENTS"
```

Background routing:
- Foreground use runs the normal command above.
- If `$ARGUMENTS` contains `--background`, first run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" reserve-job multi-review "$ARGUMENTS"
```

- Parse the returned JSON and dispatch exactly one forwarding subagent or child worker with the returned `workerCommand` JSON argv array. The child must execute that array as argv while preserving element boundaries; if forced through a shell, quote every element.
- The child runs `run-reserved-job` once through `workerCommand`; it must not inspect or reinterpret the repository.
- The parent returns the job id immediately and tells the user to use `claude-result <job-id>`.

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
- `--base <ref>` reviews `ref...HEAD`.
- `--scope auto|working-tree|branch` is passed to the runtime for prompt context.
- `--path <path>` or `--paths <path>` filters git context to one path; repeat it for multiple paths.
- `--model <model>` and `--effort <level>` are passed to each Claude CLI invocation.
- `--json` asks every role for a normalized review object and returns one role-tagged aggregate object.
- `--semantic-context <provider>` is optional and off by default. The provider context is fetched once per command and shared across role prompts as advisory context.
- `--parallel` is the default execution mode for foreground role fan-out.
- `--sequential` runs roles one at a time for debugging or rate-limit-sensitive environments.
- `--background` starts a tracked job and returns a job id.
- `--wait` only applies to direct `--background` runtime use. It is not part of the host-forwarded `reserve-job` path, where the parent returns immediately; waiting requires polling or retrieving `claude-result <job-id>`.

Examples:
- `--base main`
- `--roles correctness,security --scope branch --base main`
- `--role release --role adversarial check upgrade and rollback risk`
- `--roles skeptic,architect,minimalist --base main`
