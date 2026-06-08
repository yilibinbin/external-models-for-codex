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
- If Claude reports no findings, still report any residual risks it listed.
- Use `--background` for long reviews through the background routing contract above so Codex can continue working and retrieve results later with `claude-result`.
- Tiny one-to-two file reviews can run foreground; broader or unclear reviews should use background.

Arguments:
- `--base <ref>` reviews `ref...HEAD`.
- `--scope auto|working-tree|branch` is passed to the runtime for prompt context.
- `--quality auto|fast|standard|strong|max` selects adaptive Claude Code aliases and effort. Use `--quality strong` when the user asks for a deeper local Claude pass without naming a concrete model. Use `--quality max` only when the user explicitly asks for the strongest local Claude review.
- `--model <model>` and `--effort <level>` are passed to Claude CLI.
- `--json` asks Claude for a normalized `{verdict, summary, findings, next_steps}` review object using `approve|needs-attention`.
- `--semantic-context <provider>` is optional and off by default. Use it only when a repo-external provider is configured; semantic context is advisory and cannot replace changed-file or git evidence.
- `--background` starts a tracked job and returns a job id.
- `--wait` only applies to direct `--background` runtime use. It is not part of the host-forwarded `reserve-job` path, where the parent returns immediately; waiting requires polling or retrieving `claude-result <job-id>`.
- Do not substitute `--quality strong` or `--quality max` with `claude ultrareview`; ultrareview requires the `claude-ultrareview` skill and explicit cost confirmation.
