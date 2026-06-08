---
name: claude-rescue
description: Ask Claude Code for rescue diagnosis or explicit write-mode repair when Codex is stuck, failing tests, or uncertain about recovery.
---

# Claude Rescue

Use this skill when Codex needs an independent Claude diagnosis for a stuck implementation, failing validation, confusing git state, or unclear next recovery step.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" rescue "$ARGUMENTS"
```

## Natural-Language Claude Routing

<!--
routing:rescue
routing:write-mode-explicit
-->

- Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.
- Default to `--quality auto` for manual Claude review, plan, rescue, and multi-review unless the command documents a stricter default.
- Use `--quality strong` for deep, strict, high-risk, migration, release, or difficult diagnosis/planning requests.
- Use `--quality max` only when the user explicitly asks for the strongest local Claude pass.
- If the user names a concrete Claude model or effort, pass it as explicit argv tokens outside quoted `$ARGUMENTS`.
- Keep rescue read-only unless the user explicitly asks Claude to write or repair files.
- Route explicit repair requests to `rescue --write`; inspect the resulting git diff before reporting success.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.

User-facing examples:
- "Ask Claude to diagnose why this test keeps failing."
- "Use Claude for a strict rescue diagnosis of the release blocker."
- "Ask Claude to repair the stuck test, then inspect the diff."

Internal routing procedure:
- Classify the user's intent first, then invoke the narrowest Claude for Codex command that satisfies it.
- Route stuck implementation, repeated test failure, confusing git state, or recovery diagnosis to `rescue`.
- Keep the command read-only unless the user explicitly requests Claude-side repair.
- Translate explicit strength, model, effort, backend, role, or background-job requests into argv tokens outside quoted `$ARGUMENTS`.
- Keep Codex responsible for reading Claude output, judging whether findings are correct, and reconciling the final answer or implementation plan.

Background routing:
- Foreground use runs the normal command above.
- If `$ARGUMENTS` contains `--background`, first run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" reserve-job rescue "$ARGUMENTS"
```

- Parse the returned JSON and dispatch exactly one forwarding subagent or child worker with the returned `workerCommand` JSON argv array. The child must execute that array as argv while preserving element boundaries; if forced through a shell, quote every element.
- The child runs `run-reserved-job` once through `workerCommand`; it must not inspect or reinterpret the repository.
- The parent returns the job id immediately and tells the user to use `claude-result <job-id>`.

Codex subagent delegation:
- Use `claude-subagent-review` when a Codex parent wants a general-purpose Codex subagent to run the review/rescue.
- Parent calls concrete `subagent-command rescue "$ARGUMENTS"` before starting the child.
- Pass the returned `workerCommand` JSON argv to exactly one child.
- Pass the returned `cwd`; the child runs from that exact working directory.
- Do not replace Claude for Codex with raw `claude -p`; it bypasses plugin read-only isolation, Git MCP, reports, and compatibility handling.

Rules:
- Default mode is read-only.
- Claude must diagnose and propose recovery steps, not edit files, unless `--write` is explicitly present.
- Codex remains responsible for applying any fixes after reviewing the diagnosis.
- Use `--write` only when the user explicitly asks Claude to modify files.
- In write mode, report Claude's output and inspect the resulting git diff before claiming the task is fixed.
- Preserve Claude's evidence, uncertainty markers, file paths, and recovery assumptions.
- If Claude fails, reports setup/auth problems, or cannot inspect enough evidence, report that directly instead of inventing a substitute diagnosis.

Arguments:
- `--base <ref>` includes branch diff context when available.
- `--scope auto|working-tree|branch` controls git context selection.
- `--path <path>` or `--paths <path>` filters git context.
- `--quality auto|fast|standard|strong|max` selects adaptive Claude Code aliases and effort. Use `--quality strong` for deeper local Claude diagnosis without naming a concrete model. Use `--quality max` only when the user explicitly asks for the strongest local Claude rescue pass.
- `--model <model>` and `--effort <level>` are passed to Claude CLI.
- `--write` allows Claude Code write permissions after recording git working-tree fingerprints.
- `--background` starts a tracked job and returns a job id.
- `--wait` only applies to direct `--background` runtime use. It is not part of the host-forwarded `reserve-job` path, where the parent returns immediately; waiting requires polling or retrieving `claude-result <job-id>`.

Do not substitute `--quality strong` or `--quality max` with `claude ultrareview`; ultrareview requires the `claude-ultrareview` skill and explicit cost confirmation.
