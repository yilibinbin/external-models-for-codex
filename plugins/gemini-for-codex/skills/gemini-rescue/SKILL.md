---
name: gemini-rescue
description: Ask Gemini CLI for read-only rescue diagnosis when Codex is stuck, failing tests, or uncertain about recovery.
---

# Gemini Rescue

Use this skill when Codex needs an independent Gemini diagnosis for a stuck implementation, failing validation, confusing git state, or unclear next recovery step.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" rescue "$ARGUMENTS"
```

Background routing:
- Foreground use runs the normal command above.
- If `$ARGUMENTS` contains `--background`, first run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" reserve-job rescue "$ARGUMENTS"
```

- Parse the returned JSON and dispatch exactly one forwarding subagent or child worker with the returned `workerCommand` JSON argv array. The child must execute that array as argv while preserving element boundaries; if forced through a shell, quote every element.
- The child runs `run-reserved-job` once through `workerCommand`; it must not inspect or reinterpret the repository.
- The parent returns the job id immediately and tells the user to use `gemini-result <job-id>`.

Rules:
- Default mode is read-only.
- Gemini must diagnose and propose recovery steps, not edit files.
- Codex remains responsible for applying any fixes after reviewing the diagnosis.
- Gemini native session flags are opt-in and capability-gated by the runtime; unsupported flags fail before Gemini invocation.

Arguments:
- `--base <ref>` includes branch diff context when available.
- `--scope auto|working-tree|branch` controls git context selection.
- `--path <path>` or `--paths <path>` filters git context.
- `--model <model>` is passed to Gemini CLI.
- `--resume [latest|id]` asks Gemini CLI to resume when supported.
- `--fresh` avoids resume routing.
- `--session-id <uuid>` asks Gemini CLI to use an explicit session id when supported.
- `--worktree [name]` asks Gemini CLI to use its native worktree mode when supported.
- `--background` starts a tracked job and returns a job id.
- `--wait` only applies to direct `--background` runtime use. It is not part of the host-forwarded `reserve-job` path, where the parent returns immediately; waiting requires polling or retrieving `gemini-result <job-id>`.
