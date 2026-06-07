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
- `--model <model>` and `--effort <level>` are passed to Claude CLI.
- `--write` allows Claude Code write permissions after recording git working-tree fingerprints.
- `--background` starts a tracked job and returns a job id.
- `--wait` only applies to direct `--background` runtime use. It is not part of the host-forwarded `reserve-job` path, where the parent returns immediately; waiting requires polling or retrieving `claude-result <job-id>`.
