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

## Natural-Language Claude Routing

<!--
routing:codex-subagent-delegation
routing:worker-command-exactly-once
-->

- Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.
- Use `subagent-command` to create the exact `workerCommand` JSON argv.
- The child Codex subagent runs that argv exactly once from the returned `cwd`.
- The child must not replace the plugin call with raw `claude -p`.
- Model, effort, quality, backend, role, and background flags must be chosen by the parent before dispatch and passed through the returned worker command.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.

User-facing examples:
- "Delegate the Claude review to a Codex subagent."
- "Have a child Codex worker run the Claude multi-role review."
- "Use a Codex subagent to run Claude rescue diagnosis."

Internal routing procedure:
- Classify the user's intent first, then invoke the narrowest Claude for Codex command that satisfies it.
- If the user explicitly names Fable, Fable 5, or `claude-fable-5`, route with `--quality max` by default so the runtime can use capabilities detection (`best`, then `fable`, then `opus`). Use exact `--model fable` only when the user explicitly asks for an exact model flag or the local capabilities/status output advertises Fable support.
- If the user asks for the strongest, top, best, max, 顶级, 最强, or 不要省成本 local Claude pass without naming Fable, route with `--quality max`; the runtime selects the strongest supported local model and uses Claude Code's native fallback-model only for supported availability failures.
- Do not escalate routine "strict" or "deep" language directly to Fable; use `--quality strong` unless release, security, migration, multi-agent, SDK-subagent, large-diff, or explicit max signals justify `--quality max`.
- Installed Stop hook / review-gate paths stay conservative: they do not auto-select Fable from env or auto scoring, and only manual `review-gate --quality max` can use top-model routing.
- Use this skill only when a parent Codex turn delegates a Claude for Codex review or rescue command to exactly one Codex child.
- Translate explicit strength, model, effort, backend, role, or background-job requests into argv tokens outside quoted `$ARGUMENTS`.
- Keep Codex responsible for reading Claude output, judging whether findings are correct, and reconciling the final answer or implementation plan.

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
