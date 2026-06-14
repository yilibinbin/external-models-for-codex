---
name: claude-review
description: Use Claude Code from Codex for a read-only single review of local git changes or a branch diff; strict-only review stays here unless the user asks for multiple roles, subagents, or perspectives.
---

# Claude Review

Use this skill when Codex needs an independent Claude Code review before shipping.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" review "$ARGUMENTS"
```

## Natural-Language Claude Routing

Codex should let the user ask for Claude review in normal language. Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:
- Default to `--quality auto` for manual Claude review, plan, rescue, and multi-review unless the command documents a stricter default.
- Keep strict-only, focused, or "double check this" requests in `claude-review`; do not fan out unless the user asks for multiple roles, multiple perspectives, subagents, or a named team.
- Use `--quality strong` for "deep", "strict", "advanced", "high-confidence", "strong Claude", or "harder local Claude" review when the user does not name a concrete model.
- Use `--quality max` only when the user explicitly asks for the strongest local Claude review or max local effort.
- If the user names a concrete Claude model or effort, pass it as explicit argv tokens outside quoted `$ARGUMENTS`.
- Use `--background` for broad diffs, unclear scope, or long reviews instead of blocking the main Codex turn.
- Before choosing foreground for an unforced review, run `recommend-execution-mode --json` or apply the same threshold: foreground only for tiny one-to-two-file work; use background for untracked directories, more than two files, more than roughly fifty changed lines, multi-role/adversarial/rescue work, or unclear scope.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.

Internal invocation examples, not for users:
- Strict local quality: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" review --quality strong "$ARGUMENTS"`.
- Strongest local quality: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" review --quality max "$ARGUMENTS"`.
- Concrete model and effort: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" review --model opus --effort xhigh "$ARGUMENTS"`.
- Background path: `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" reserve-job review --background "$ARGUMENTS"`.
- Model, effort, quality, backend, and background flags are added outside quoted `$ARGUMENTS`; `$ARGUMENTS` carries only natural-language focus text.

<!--
routing:single-review
routing:strict-stays-single
routing:multi-agent-to-multi-review
routing:background-for-broad-review
-->

User-facing examples:
- "Use Claude to review the current changes."
- "Use Claude for a strict release-risk review."
- "Use the strongest local Claude review for this patch."

Internal routing procedure:
- Classify the request as normal read-only review when the user asks for one Claude pass, focused review, strict review, or a second opinion.
- If the user explicitly names Fable, Fable 5, or `claude-fable-5`, route with `--quality max` by default so the runtime can use capabilities detection (`best`, then `fable`, then `opus`). Use exact `--model fable` only when the user explicitly asks for an exact model flag or the local capabilities/status output advertises Fable support.
- If the user asks for the strongest, top, best, max, 顶级, 最强, or 不要省成本 local Claude pass without naming Fable, route with `--quality max`; the runtime selects the strongest supported local model and uses Claude Code's native fallback-model only for supported availability failures.
- Do not escalate routine "strict" or "deep" language directly to Fable; use `--quality strong` unless release, security, migration, multi-agent, SDK-subagent, large-diff, or explicit max signals justify `--quality max`.
- Installed Stop hook / review-gate paths stay conservative: they do not auto-select Fable from env or auto scoring, and only manual `review-gate --quality max` can use top-model routing.
- If the user asks for multi-agent review, multiple perspectives, named review roles, SDK subagents, or role fan-out, use `claude-multi-review` instead.
- Preserve the user's focus as natural-language text.
- Add `--quality`, `--model`, `--effort`, and `--background` only as explicit argv tokens when the request calls for them.

Background routing:
- Foreground use runs the normal command above.
- If `$ARGUMENTS` contains `--background`, first run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" reserve-job review "$ARGUMENTS"
```

- Parse the returned JSON and dispatch exactly one forwarding subagent or child worker with the returned `workerCommand` JSON argv array. The child must execute that array as argv while preserving element boundaries; if forced through a shell, quote every element.
- The child runs `run-reserved-job` once through `workerCommand`; it must not inspect or reinterpret the repository.
- The parent returns the job id immediately and tells the user to use `claude-result <job-id>`.

Codex subagent delegation:
- Use `claude-subagent-review` when a Codex parent wants a general-purpose Codex subagent to run the review/rescue.
- Parent calls concrete `subagent-command review "$ARGUMENTS"` before starting the child.
- Pass the returned `workerCommand` JSON argv to exactly one child.
- Pass the returned `cwd`; the child runs from that exact working directory.
- Do not replace Claude for Codex with raw `claude -p`; it bypasses plugin read-only isolation, Git MCP, reports, and compatibility handling.

Rules:
- Treat the output as review findings, not implementation instructions.
- Do not fix findings in the same turn unless the user explicitly asks.
- Preserve Claude's file paths, line numbers, uncertainty markers, and residual-risk notes.
- Preserve evidence boundaries; if Claude marks a claim as inference or uncertainty, keep that distinction.
- If Claude fails, returns malformed structured output, or reports setup/auth problems, report that failure instead of replacing it with Codex guesses.
- For setup, PATH, hook, SDK, review-gate, or state-file health questions, run `node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" doctor --json`; it is a cheap no-prompt diagnostic.
- Preserve compact outcome classification metadata from JSON reports when present, including refusal, fallback, timeout, and permission-compatibility categories.
- If Claude reports no findings, still report any residual risks it listed.
- Use `--background` for long reviews through the background routing contract above so Codex can continue working and retrieve results later with `claude-result`.
- Tiny one-to-two file reviews can run foreground; broader or unclear reviews should use background.
- Do not rerun a review just because a previous `--wait` observation window expired; use `claude-status` or `claude-result <job-id>`.

Arguments:
- `--base <ref>` reviews `ref...HEAD`.
- `--scope auto|working-tree|branch` is passed to the runtime for prompt context.
- `--quality auto|fast|standard|strong|max` selects adaptive Claude Code aliases and effort. Use `--quality strong` when the user asks for a deeper local Claude pass without naming a concrete model. Use `--quality max` only when the user explicitly asks for the strongest local Claude review.
- `--model <model>` and `--effort <level>` are passed to Claude CLI.
- `--json` asks Claude for a normalized `{verdict, summary, findings, next_steps}` review object using `approve|needs-attention`.
- `--scorecard --json` asks Claude for the separate scorecard schema with total, threshold, weighted dimensions, blocking findings, residual risks, and next steps. This does not change plain `--json` unless `--scorecard` is present.
- `--validation-log <file>`, `--test-summary <file>`, `--ci-summary <file>`, and `--screenshot-summary <file>` include user/Codex-provided evidence as untrusted, redacted, workspace-bound prompt context. The plugin does not run project commands for these inputs.
- `--rules <file>` explicitly loads an additional workspace-bound advisory rule file. Default advisory rules also include `.codex/program.md` and `.codex/review.md`.
- `--semantic-context <provider>` is optional and off by default. Use it only when a repo-external provider is configured; semantic context is advisory and cannot replace changed-file or git evidence.
- `--background` starts a tracked job and returns a job id.
- `--wait` only applies to direct `--background` runtime use. It is not part of the host-forwarded `reserve-job` path, where the parent returns immediately; waiting requires polling or retrieving `claude-result <job-id>`.
- Do not substitute `--quality strong` or `--quality max` with `claude ultrareview`; ultrareview requires the `claude-ultrareview` skill and explicit cost confirmation.
