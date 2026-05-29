---
name: claude-multi-review
description: Opt in to plugin-managed role fan-out Claude review from Codex for high-risk changes that need multiple read-only perspectives.
---

# Claude Multi Review

Use this skill when Codex needs several independent Claude review passes with different role directives. This is an opt-in fan-out review workflow, not automatic repair.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" multi-review "$ARGUMENTS"
```

Rules:
- This is read-only.
- Claude must not edit files or apply fixes.
- Treat each role output as review findings for Codex to reconcile.
- Codex remains responsible for deciding which findings to adopt, reject, or report as residual risk.
- Preserve role headers, file paths, line numbers, uncertainty markers, failed-role diagnostics, and the orchestration summary.

Default roles:
- `correctness`: bugs, regressions, edge cases, and contract breaks.
- `security`: read-only safety, secrets exposure, injection risks, and unsafe command or path handling.
- `tests`: missing, brittle, or overfit tests and validation gaps.
- `release`: install, marketplace, versioning, documentation, and upgrade risks.
- `adversarial`: assumptions, simpler alternatives, hidden costs, and failure modes.

Arguments:
- `--roles <a,b,c>` runs a comma-separated role list in order instead of the defaults.
- `--role <name>` adds one role; repeat it to build an ordered role list.
- `--base <ref>` reviews `ref...HEAD`.
- `--scope auto|working-tree|branch` is passed to the runtime for prompt context.
- `--model <model>` and `--effort <level>` are passed to each Claude CLI invocation.

Examples:
- `--base main`
- `--roles correctness,security --scope branch --base main`
- `--role release --role adversarial check upgrade and rollback risk`
