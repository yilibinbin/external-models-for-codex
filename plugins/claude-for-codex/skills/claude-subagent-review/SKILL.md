---
name: claude-subagent-review
description: Delegate Claude for Codex read-only review to a Codex subagent without using raw Claude CLI.
---

# Claude Subagent Review

Use this skill when a parent Codex turn needs to delegate Claude for Codex read-only review work to exactly one Codex subagent while preserving the plugin runtime contract.

Parent workflow:
- The parent runs the plugin runtime with `subagent-command` for one of the delegatable review commands:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command review "$ARGUMENTS"
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command adversarial-review "$ARGUMENTS"
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command multi-review "$ARGUMENTS"
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command rescue "$ARGUMENTS"
```

- Parse the returned JSON.
- Dispatch exactly one Codex subagent with the returned `workerCommand` and returned `cwd`.
- Tell the subagent to run `workerCommand` exactly once as argv from the returned `cwd`.
- Preserve `workerCommand` element boundaries. If a transport forces shell execution, quote every element rather than flattening or re-tokenizing the command.
- The parent may include `--quality strong` for deeper local Claude review without naming a concrete model. Use `--quality max` only when the user explicitly asks for the strongest local Claude review.

Child rules:
- The child must run `workerCommand` exactly once as argv from the returned `cwd`.
- The child must not inspect or reinterpret the repository before execution.
- The child must not replace it with raw claude, `claude -p`, or any hand-built Claude CLI command.
- The child reports exit status, stdout, and stderr to the parent.
- The child does not apply fixes, edit files, stage changes, or reinterpret Claude output as implementation instructions.

Background jobs:
- For background work, the parent uses `reserve-job` through the normal review skill background route, then delegates the returned `workerCommand` to one Codex subagent.
- Use `reserve-job` for `--background` work so the job is tracked by the plugin runtime.
- The parent returns the job id and tells the user to retrieve results with `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" result <job-id>`.

Safety boundaries:
- This skill is only for read-only review delegation through the plugin runtime.
- `claude-ultrareview` is not delegatable through this skill.
- `--write` is not delegatable through this skill.
- Stop hooks do not use this skill.
- If the runtime returns malformed JSON, no `workerCommand`, no returned `cwd`, or an unsupported command, report the failure instead of inventing a command.
- Do not substitute `--quality strong` or `--quality max` with `claude ultrareview`; ultrareview remains explicit-cost only.
