# Gemini for Codex

Codex plugin that invokes the local Gemini CLI for independent read-only review, adversarial review, implementation planning, rescue diagnosis, tracked background jobs, and an opt-in Stop hook gate.

## Requirements

- Codex with plugin support
- Gemini CLI available as `gemini`, or set `GEMINI_CLI_PATH`
- Node.js 20 or newer
- Git repository for review context collection

Gemini CLI resolution order:

1. `GEMINI_CLI_PATH` when it points to an executable file.
2. `gemini` from the current `PATH`.
3. Common user and JavaScript toolchain locations, including `~/.local/bin`, `~/bin`, npm global prefix, pnpm, Volta, asdf, bun, deno, nvm, and fnm paths.
4. Common package-manager/system locations such as the configured Homebrew prefix, `/opt/homebrew/bin`, `/usr/local/bin`, and `/usr/bin`.

## Install

```bash
codex plugin marketplace add .
codex plugin add gemini-for-codex@external-models-for-codex
```

`external-models-for-codex` is this repository's Codex marketplace for plugins that connect Codex to external model CLIs. It currently publishes both Claude for Codex and Gemini for Codex.

## Runtime Safety

Gemini review runs in headless JSON mode with:

```bash
gemini --approval-mode=plan --output-format=json --prompt
```

The plugin sends bounded inline git context and does not depend on Gemini MCP or a Gemini extension. Gemini extension and MCP support are deferred until their current CLI configuration path is validated.

## Commands

- `setup`: report Gemini, git, hook, and review-gate status; supports `--enable-review-gate` and `--disable-review-gate`.
- `review`: read-only review of current git changes or a branch diff. Add `--structured` for schema-validated review output.
- `adversarial-review`: skeptical multi-lens review.
- `multi-review`: parallel role fan-out across correctness, security, tests, release, and adversarial review. Add `--native-agents` to use Gemini CLI native subagents through temporary `gfc_*` agent definitions.
- `plan`: independent implementation plan for Codex to reconcile.
- `rescue`: read-only diagnosis for stuck implementation work. Explicit `--resume`, `--session-id`, and `--worktree` are forwarded only when the installed Gemini CLI reports support.
- `recommend-execution-mode`: return JSON guidance for foreground versus background review sizing.
- `sessions`: list Gemini CLI sessions when the installed Gemini CLI reports `--list-sessions`.
- `jobs`, `result`, `cancel`: tracked job lifecycle.
- `review-gate`: internal Stop hook runner.

## Background Jobs

Use `--background` on long reviews. The skill reserves a job and dispatches exactly one forwarding child. Retrieve the result with:

```bash
gemini-result <job-id>
```

## Multi-Agent Review

`multi-review` has two modes:

- Default mode runs one Gemini CLI process per selected role in parallel and aggregates the completed role outputs.
- `--native-agents` creates temporary Gemini subagent definitions for the selected roles and asks Gemini CLI to dispatch `@gfc_<role>` native subagents. The temporary agent workspace is outside the repository and is removed after the run.

Both modes are read-only and use bounded git context. Native subagent mode also passes the repository through `--include-directories` so Gemini can resolve project context while remaining in plan mode.

## Stop Hook

Hooks are installed but conservative. `SessionStart`, `SessionEnd`, and `UserPromptSubmit` track the active session, record turn baselines, and remind about unread Gemini job results. Session cleanup cancels only jobs with an explicit matching session id.

The Stop hook is installed but disabled by default. Enable it per repository with:

```bash
node plugins/gemini-for-codex/scripts/gemini-companion.mjs setup --enable-review-gate
```

Only explicit `BLOCK:` verdicts from Gemini emit Codex hook block JSON. Gemini CLI failures, auth failures, rate limits, timeouts, parse errors, and invalid gate output fail open with stderr diagnostics.

## Structured Review And Sessions

`review --structured` asks Gemini for a JSON review object, extracts fenced or embedded JSON, validates it, and renders normalized findings. Invalid or malformed structured output exits non-zero instead of being treated as approval.

Gemini native session flags are capability-gated. Run `setup` to see whether the current CLI reports `--resume`, `--session-id`, `--session-file`, `--list-sessions`, and `--worktree`. Unsupported requested flags fail before Gemini invocation.

## Verification

```bash
python3 -m pytest tests/test_gemini_for_codex_plugin.py -q
node --check plugins/gemini-for-codex/scripts/gemini-companion.mjs
node --check plugins/gemini-for-codex/hooks/gemini-review-gate.mjs
node --check plugins/gemini-for-codex/hooks/session-lifecycle.mjs
node --check plugins/gemini-for-codex/hooks/unread-result.mjs
```
