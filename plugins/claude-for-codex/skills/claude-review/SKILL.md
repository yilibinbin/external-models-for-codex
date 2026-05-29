---
name: claude-review
description: Use Claude Code from Codex for a read-only code review of local git changes or a branch diff.
---

# Claude Review

Use this skill when Codex needs an independent Claude Code review before shipping.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" review "$ARGUMENTS"
```

Background routing:
- Foreground use runs the normal command above.
- If `$ARGUMENTS` contains `--background`, first run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" reserve-job review "$ARGUMENTS"
```

- Parse the returned JSON and dispatch exactly one forwarding subagent or child worker with the returned `workerCommand`.
- The child runs `run-reserved-job` once through `workerCommand`; it must not inspect or reinterpret the repository.
- The parent returns the job id immediately and tells the user to use `claude-result <job-id>`.

Rules:
- Treat the output as review findings, not implementation instructions.
- Do not fix findings in the same turn unless the user explicitly asks.
- Preserve Claude's file paths, line numbers, uncertainty markers, and residual-risk notes.
- If Claude reports no findings, still report any residual risks it listed.
- Use `--background` for long reviews through the background routing contract above so Codex can continue working and retrieve results later with `claude-result`.

Arguments:
- `--base <ref>` reviews `ref...HEAD`.
- `--scope auto|working-tree|branch` is passed to the runtime for prompt context.
- `--model <model>` and `--effort <level>` are passed to Claude CLI.
- `--background` starts a tracked job and returns a job id.
- `--wait` waits for a background job to finish before returning.
