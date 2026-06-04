# Gemini for Codex

Codex plugin that invokes the local Gemini CLI for independent read-only review, reviewer role packs, advisory mailbox/leases, GitHub Actions PR review templates, adversarial review, implementation planning, rescue diagnosis, tracked background jobs, optional bounded context-provider enrichment, and an opt-in Stop hook gate.

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

- `setup`: report Gemini, git, hook, review-gate, and capability status; supports `--enable-review-gate` and `--disable-review-gate`.
- `capabilities`: print Gemini CLI flag support as detected from the current `gemini --help`.
- `report`: print the latest sanitized operation metadata report.
- `release-check`: run offline manifest, hook, docs, context-provider, mailbox/lease, and CI-template safety checks.
- `review`: read-only review of current git changes or a branch diff. Add `--structured` for schema-validated rendered output or `--json` for machine-readable normalized JSON.
- `github-actions`: render, initialize, validate, and consume fork-safe GitHub Actions PR review workflows.
- `roles`: list, inspect, and validate Gemini reviewer role packs.
- `mailbox`: list, show, and post sanitized coordination summaries.
- `leases`: list, claim, and release advisory path-attention leases.
- `adversarial-review`: skeptical multi-lens review.
- `multi-review`: parallel role fan-out across correctness, security, tests, release, and adversarial review. Add `--role-pack <pack>` to select a built-in reviewer team, or `--native-agents` to use Gemini CLI native subagents through temporary `gfc_*` agent definitions.
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

Both modes are read-only and use bounded git context. Native subagent mode passes the repository through `--include-directories` only when the installed Gemini CLI reports support for that flag.

## Reviewer Role Packs

Built-in role packs are plugin-managed Gemini reviewer presets:

```bash
node plugins/gemini-for-codex/scripts/gemini-companion.mjs roles list
node plugins/gemini-for-codex/scripts/gemini-companion.mjs roles inspect release
node plugins/gemini-for-codex/scripts/gemini-companion.mjs multi-review --role-pack release
node plugins/gemini-for-codex/scripts/gemini-companion.mjs multi-review --native-agents --role-pack minimal
```

Available built-in packs are `default`, `security`, `release`, `frontend`, `backend`, `testing`, `docs`, and `minimal`. `frontend` and `docs` are presets over existing Gemini roles in this release, not separate dedicated lenses.

User-authored role packs are validate/inspect-only:

```bash
node plugins/gemini-for-codex/scripts/gemini-companion.mjs roles validate ~/.codex/gemini-for-codex/roles/custom.json
```

User packs cannot be executed with `multi-review` or `review-gate` yet. Validation rejects workspace-local files, symlink escapes, unknown fields, and any attempt to define tools, shell commands, hooks, environment variables, MCP servers, providers, extensions, permissions, backend behavior, or write behavior.

## Mailbox And Advisory Leases

Mailbox and leases are opt-in coordination metadata:

```bash
node plugins/gemini-for-codex/scripts/gemini-companion.mjs multi-review --role-pack minimal --use-mailbox --advisory-leases --path plugins/gemini-for-codex/README.md
node plugins/gemini-for-codex/scripts/gemini-companion.mjs mailbox list --json
node plugins/gemini-for-codex/scripts/gemini-companion.mjs leases list --json
```

Mailbox messages are sanitized summaries, not transcripts. Reports store counts and hashes only, not mailbox text. Leases are advisory path-attention hints; they do not lock files, do not block review, and do not affect Stop gate verdicts.

In plugin-managed `multi-review`, the mailbox records per-role start and finish summaries. In `--native-agents` mode, Gemini for Codex records aggregate native-agent orchestration start and finish summaries only; it does not claim visibility into individual Gemini subagent lifecycle events.

## Optional Context Providers

Context providers are opt-in and disabled by default:

```bash
node plugins/gemini-for-codex/scripts/gemini-companion.mjs review --context-provider auto
node plugins/gemini-for-codex/scripts/gemini-companion.mjs multi-review --context-provider my-provider
```

Provider config is loaded from `GEMINI_FOR_CODEX_CONTEXT_CONFIG`, then `GEMINI_FOR_CODEX_DATA/context/providers.json`, then `~/.codex/gemini-for-codex/context/providers.json`.

```json
{
  "providers": {
    "my-provider": {
      "command": ["/absolute/path/to/provider", "--json"],
      "env": {
        "GEMINI_CONTEXT_PROVIDER_MODE": "summary"
      },
      "timeoutMs": 5000,
      "maxOutputBytes": 32768
    }
  },
  "defaultProvider": "my-provider"
}
```

Provider executables must resolve outside the reviewed workspace, run without a shell, receive only an allowlisted environment, and return bounded JSON. Provider output is XML-escaped before entering prompts and is advisory only; Gemini findings must still be grounded in changed files or git context. Reports store only sanitized context metadata, not provider output, prompt text, source, diffs, config, request JSON, or raw workspace paths.

## GitHub Actions

Render a workflow without writing files:

```bash
node plugins/gemini-for-codex/scripts/gemini-companion.mjs github-actions render
```

Write the default workflow only when requested:

```bash
node plugins/gemini-for-codex/scripts/gemini-companion.mjs github-actions init --write
```

The generated workflow uses `pull_request`, skips Gemini execution on fork PRs by default, installs the Codex CLI before plugin installation, pins `gemini-for-codex-v0.8.0`, uploads the structured review artifact, and can optionally publish Checks annotations with `--annotations`.

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
node plugins/gemini-for-codex/scripts/gemini-companion.mjs capabilities
node plugins/gemini-for-codex/scripts/gemini-companion.mjs release-check
node plugins/gemini-for-codex/scripts/gemini-companion.mjs release-check --ci-simulate
```
