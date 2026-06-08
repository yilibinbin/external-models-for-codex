# External Models for Codex Documentation

External Models for Codex is a Codex plugin marketplace for external model CLI workflows. It keeps provider-specific plugins in one installable marketplace while preserving separate plugin IDs, skills, hooks, and safety models for each model provider.

Included plugins:

- Claude for Codex lets Codex call the local Claude Code CLI for independent review, planning, multi-role critique, native SDK subagent teams, rescue diagnosis, structured review output, explicit-cost ultrareview, and optional Stop hook gates.
- Gemini for Codex lets Codex call the legacy Gemini CLI (`gemini`) for Gemini-only read-only review, planning, rescue diagnosis, structured review output, and Gemini CLI-native session capability checks.
- Antigravity for Codex lets Codex call Google Antigravity CLI (`agy`) for mature plugin-managed review workflows: read-only review, adversarial critique, planning, rescue diagnosis, multi-role review, structured reports, role packs, background jobs, mailbox/leases, lifecycle hooks, GitHub Actions workflow rendering, release checks, opt-in real smoke, and an opt-in Stop hook gate with explicit Gemini or Claude model-provider selection.

## Installation

Install from GitHub:

```bash
codex plugin marketplace add yilibinbin/external-models-for-codex --ref claude-for-codex-v0.14.1
codex plugin add claude-for-codex@external-models-for-codex

codex plugin marketplace add yilibinbin/external-models-for-codex --ref gemini-for-codex-v0.11.2
codex plugin add gemini-for-codex@external-models-for-codex

codex plugin marketplace add yilibinbin/external-models-for-codex --ref antigravity-for-codex-v0.5.2
codex plugin add antigravity-for-codex@external-models-for-codex
```

Use the provider-specific immutable release ref for the plugin you want to install. Use `main` only for development snapshots.

Upgrade an existing installation:

```bash
codex plugin marketplace upgrade external-models-for-codex
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@external-models-for-codex
codex plugin remove gemini-for-codex
codex plugin add gemini-for-codex@external-models-for-codex
codex plugin remove antigravity-for-codex
codex plugin add antigravity-for-codex@external-models-for-codex
```

Rollback from `0.4.0`: disable the review gate with `setup --disable-review-gate`, remove or downgrade the plugin, then remove stale trusted hook entries for `SessionStart`, `SessionEnd`, `UserPromptSubmit`, or `Stop` if Codex Settings still points at missing files.

Install from a local checkout:

```bash
codex plugin marketplace add .
codex plugin add claude-for-codex@external-models-for-codex
codex plugin add gemini-for-codex@external-models-for-codex
codex plugin add antigravity-for-codex@external-models-for-codex
```

## Requirements

- Codex CLI with plugin support
- Claude Code CLI available as `claude`, configured with `CLAUDE_CODE_PATH`, or installed at `~/.local/bin/claude`
- Optional `@anthropic-ai/claude-agent-sdk` package for Claude SDK native subagent mode; `@anthropic-ai/claude-code` remains supported as a compatibility fallback
- Gemini CLI available as `gemini` or `GEMINI_CLI_PATH` for Gemini for Codex
- Google Antigravity CLI available as `agy`, configured with `AGY_CLI_PATH` or `ANTIGRAVITY_CLI_PATH`, for Antigravity for Codex
- Node.js 20 or newer
- A Git repository for review context collection

Check runtime status:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup
```

Claude CLI resolution order:

1. `CLAUDE_CODE_PATH` when it points to an executable file.
2. `claude` from the current `PATH`.
3. `~/.local/bin/claude`, which covers the default Claude install path that Codex Desktop may omit from `PATH`.

If setup reports `claudeAvailable: false` but Claude is installed elsewhere, set `CLAUDE_CODE_PATH` to the absolute executable path.

Antigravity for Codex model selection:

1. `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=gemini` is the default.
2. `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude` explicitly selects a Claude model exposed by Antigravity.
3. `ANTIGRAVITY_FOR_CODEX_MODEL`, `ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL`, `ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL`, and `--model` are validated against the selected provider. GPT/OpenAI labels are rejected.
4. Claude-through-Antigravity is explicit and separate from `claude-for-codex`; it still invokes `agy`, not the Claude Code CLI or Claude SDK.

Legacy Gemini CLI resolution order:

1. `GEMINI_CLI_PATH` when it points to an executable file.
2. `gemini` from the current `PATH`.
3. Common user and JavaScript toolchain locations, including `~/.local/bin`, `~/bin`, npm global prefix, pnpm, Volta, asdf, bun, deno, nvm, and fnm paths.
4. Common package-manager/system locations such as the configured Homebrew prefix, `/opt/homebrew/bin`, `/usr/local/bin`, and `/usr/bin`.

## Capabilities

- `claude-review`: read-only Claude review of local git changes or branch diffs.
- `claude-adversarial-review`: challenge assumptions, tradeoffs, rollback paths, and hidden failure modes.
- `claude-plan`: request an independent implementation plan before Codex edits.
- `claude-multi-review`: run parallel role reviews for correctness, security, tests, release, and adversarial perspectives.
- `claude-multi-review --backend sdk --agent-team sdk-subagents`: run a Claude native SDK subagent review team.
- `claude-ultrareview`: run Claude cloud ultrareview only after explicit `--confirm-cost` consent for possible usage-credit billing.
- `claude-rescue`: ask Claude for read-only recovery diagnosis or explicit `--write` repair.
- `claude-status`, `claude-result`, `claude-cancel`: track background Claude jobs.
- `claude-review-gate`: configure the optional Stop hook review gate.
- `claude-github-actions-review`: generate or validate a fork-safe GitHub Actions PR review workflow.
- `claude-collaboration-loop`: run a Codex-Claude plan, reconcile, implement, review, and report workflow.
- `gemini-review`, `gemini-adversarial-review`, `gemini-plan`, `gemini-multi-review`, `gemini-rescue`: Gemini CLI-backed equivalents for Codex-side review. Gemini rescue is read-only. `gemini-review --structured` validates schema-backed findings, `gemini-multi-review` runs parallel role fan-out, and Gemini CLI-only native agent/session flags are capability-gated from the installed CLI.
- `gemini-mailbox`, `gemini-leases`: inspect sanitized Gemini coordination summaries and advisory path-attention leases.
- `antigravity-review`, `antigravity-adversarial-review`, `antigravity-plan`, `antigravity-multi-review`, `antigravity-rescue`, `antigravity-review-gate`, `antigravity-github-actions-review`: Antigravity-backed mature plugin-managed review, planning, rescue, Stop gate, and workflow-risk review with explicit Gemini or Claude model-provider selection. Antigravity for Codex uses `agy` only, does not claim Claude SDK, Gemini native-agent, or ultrareview parity, and keeps Claude-through-Antigravity separate from `claude-for-codex`.

## Gemini for Codex

Install from the same local marketplace:

```bash
codex plugin marketplace add .
codex plugin add gemini-for-codex@external-models-for-codex
```

Gemini review is Gemini CLI-only and uses legacy Gemini CLI headless JSON mode (`gemini --approval-mode=plan --output-format=json --prompt`). It uses bounded inline git context and does not depend on Antigravity, Gemini MCP, or a Gemini extension.

`gemini-multi-review` has two multi-agent modes. By default it starts one Gemini CLI review process per selected role in parallel and aggregates the outputs. With `--native-agents`, it creates temporary Gemini subagent definitions and asks Gemini CLI to dispatch `@gfc_<role>` native subagents for the requested review roles.

Gemini for Codex now also registers SessionStart, SessionEnd, UserPromptSubmit, and Stop hooks. Session hooks track the active Codex session, record turn baselines, remind about unread Gemini job results, and only clean up queued/running jobs with an explicit matching session id.

Use `gemini-review --structured` for schema-validated review output. Use `recommend-execution-mode` for noninteractive foreground/background sizing guidance. `setup` reports whether the local Gemini CLI supports `--resume`, `--session-id`, `--session-file`, `--list-sessions`, and `--worktree`; unsupported requested flags fail before Gemini invocation.

## Enhanced Adversarial Review

`claude-adversarial-review` asks Claude to infer the author's intent first, then review through three lenses:

- `skeptic`: correctness, completeness, unproven assumptions, and breakable states.
- `architect`: structural fitness, boundaries, coupling, and responsibility leaks.
- `minimalist`: necessity, complexity, premature abstraction, and deletable work.

The output must include:

- `## Intent`
- `## Verdict: PASS | CONTESTED | REJECT`
- `## Findings`
- `## What Went Well`
- `## Lead Judgment`

Use a lens subset when the review needs a narrower challenge:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --adversarial-lenses skeptic,minimalist --base main
```

Use `--json` for a validated `{verdict, summary, findings, next_steps}` object. Use `--background` on `review`, `adversarial-review`, `multi-review`, or `rescue` to start a tracked job, and retrieve it with `claude-result`. `rescue --write` is explicit opt-in and records before/after git fingerprints.

`review --json` returns a normalized normal-review object using `approve|needs-attention`. `multi-review --json` returns one aggregate object with role-tagged findings and per-role results. `adversarial-review --json` intentionally keeps the specialized `PASS|CONTESTED|REJECT` verdict vocabulary.

For `--json` modes, exit status reports command/parsing success. Inspect `verdict` to decide whether findings need attention.

## Host-forwarded background jobs

`--background` supports a Codex host-forwarded path. Skills first reserve a job with `reserve-job`, then Codex dispatches exactly one forwarding subagent to run the returned `workerCommand`. The child worker only executes `run-reserved-job`; it does not inspect or reinterpret repository state. Existing detached runtime background jobs remain as a compatibility fallback.

## MCP-backed read-only Git review

Read-only Claude review receives a strict MCP config for bounded Git inspection. The bundled read-only Git MCP server exposes status, diff, cached diff, log, show, blame, grep, and ls-files through validated Git arguments while `Bash`, `Edit`, `Write`, and `MultiEdit` remain disallowed. CLI read-only review also disables Claude Code slash commands, settings sources, and session persistence while preserving normal Claude authentication.

`multi-review` runs role reviewers in parallel by default. `adversarial-review --parallel` runs skeptic, architect, and minimalist lens reviewers as independent Claude CLI processes and aggregates their outputs. Use `--sequential` when a deterministic one-at-a-time run is needed.

Claude native SDK mode is explicit and experimental until live SDK subagent smoke tests are stable. Use `multi-review --backend sdk --agent-team sdk-subagents` to create native SDK subagents for the selected review roles; plugin-managed CLI `multi-review` remains the default. The runtime resolves `@anthropic-ai/claude-agent-sdk` first and keeps `@anthropic-ai/claude-code` as a compatibility fallback. SDK read-only review disables settings sources, skills, hooks, plugins, and session persistence while preserving normal Claude authentication. Combine `--json --native-structured` for SDK schema-backed aggregate output where `role_results[].result.review` is a full per-role review JSON object validated locally by the plugin. Raw role text and raw SDK `structured_output` are not stored in reports. Add `--stream-progress` for sanitized streaming progress without printing raw SDK chunks or storing raw SDK messages in reports.

SDK native subagent structured reviews use nested per-role review objects and remain an explicit opt-in path. The default review backend is unchanged.

`ultrareview` forwards to Claude's native cloud ultrareview command. It is not used by hooks or default review paths, and it refuses to run unless the user has explicitly consented with `--confirm-cost` or `CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1` because it may use remote/cloud execution and usage-credit billing.

Claude reviewer role packs are built-in presets for `multi-review`. Use `roles list`, `roles inspect <pack>`, and `multi-review --role-pack <pack>` for presets such as `minimal`, `release`, `security`, and `default`. User-authored JSON packs can be validated with `roles validate <file>`, but they are validate/inspect-only and are not executable by review commands. Role packs are plugin-managed presets, not native Claude subagents, and cannot grant tools, shell commands, hooks, MCP servers, environment variables, backend mode, or write permissions.

Mailbox and advisory leases are optional coordination metadata. `mailbox list|show|post` stores sanitized summaries only, not transcripts. `leases list|claim|release` declares path attention without locking files. Lease conflicts are warnings only and do not change review verdicts or `review-gate` behavior.

Claude review output is a review artifact, not implementation authority. Preserve file paths, line numbers, role names, uncertainty markers, and residual-risk notes. Do not auto-fix review findings in the same step unless the user explicitly asks which findings to adopt.

## Routing

Claude for Codex is a skills-and-hook plugin, not an MCP/app tool plugin. It is expected that `tool_search` will not return a `claude-for-codex` callable tool. That is not an installation failure.

Use the Codex skills instead:

- `claude-for-codex:claude-review`
- `claude-for-codex:claude-adversarial-review`
- `claude-for-codex:claude-multi-review`
- `claude-for-codex:claude-ultrareview`
- `claude-for-codex:claude-rescue`
- `claude-for-codex:claude-status`
- `claude-for-codex:claude-result`
- `claude-for-codex:claude-cancel`
- `claude-for-codex:claude-plan`
- `claude-for-codex:claude-review-gate`
- `claude-for-codex:claude-collaboration-loop`

## Stop Hook Review Gate

The hook file is installed with the plugin, but the gate is disabled by default. Enable it from the repository you want to protect:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --enable-review-gate --review-gate-mode multi-role
```

Disable it:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --disable-review-gate
```

After installing or upgrading, open Codex Settings > Hooks and trust or enable the `Claude for Codex` hooks. `SessionStart`, `SessionEnd`, and `UserPromptSubmit` provide session tracking, unread-result reminders, and turn-baseline capture when supported by the local Codex runtime.

The Stop gate uses the `UserPromptSubmit` turn-baseline fingerprint to avoid reviewing old dirty workspace changes when the current turn did not change the working tree. Payload-based classification of status/setup/report-only Stop turns is deferred until a real Codex Stop payload exposes a verified edit/no-edit signal.

## Safety Model

- Review workflows call Claude with read-only permissions, disabled slash/settings/session side effects, and explicit write-tool denial.
- Background jobs persist outside the repository under plugin data state.
- Codex remains responsible for accepting or rejecting Claude findings.
- The Stop gate blocks only when Claude explicitly returns `BLOCK:`.
- Missing Claude, authentication failures, rate limits, timeouts, invalid output, or runtime failures fail open with warnings instead of blocking Codex.

## Direct Runtime Commands

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --backend sdk --agent-team sdk-subagents --json --native-structured --stream-progress --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs ultrareview --confirm-cost --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs review --background --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs jobs
node plugins/claude-for-codex/scripts/claude-companion.mjs result <job-id>
node plugins/claude-for-codex/scripts/claude-companion.mjs capabilities
node plugins/claude-for-codex/scripts/claude-companion.mjs report --latest
node plugins/claude-for-codex/scripts/claude-companion.mjs release-check
node plugins/claude-for-codex/scripts/claude-companion.mjs release-check --ci-simulate
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions render
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions init --write
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions validate
node plugins/claude-for-codex/scripts/claude-companion.mjs plan "implement the feature and include tests"
node plugins/claude-for-codex/scripts/claude-companion.mjs status
```

`capabilities` reports Claude CLI flags, optional SDK backend availability, native subagent capability probes, Git/GitHub CLI, hooks, MCP, and optional semantic-provider diagnostics without initializing providers. CLI remains the default backend; `--backend sdk` or `CLAUDE_FOR_CODEX_BACKEND=sdk` explicitly selects the Claude SDK backend when it can preserve the read-only tool and Git MCP boundary. SDK native subagent review additionally requires `--agent-team sdk-subagents`. Semantic context is off by default; use `--semantic-context <provider>` only with a repo-external argv-array provider config. Semantic context is advisory, provider failures degrade confidence rather than normal review execution, and `review-gate` records degraded metadata such as `DEGRADED_PASS` when semantic context fails. `report --latest` reads a sanitized repo-external report; prompts, diffs, raw model output, source code, environment variables, semantic snippets, raw SDK messages, and raw absolute workspace paths are omitted by default. `github-actions render|init|validate` manages a GitHub Actions PR review template with immutable release refs, no default `pull_request_target`, fork PR Claude/comment/annotation skipping, env-mapped GitHub context, sanitized comments, and optional Checks annotations. `release-check --ci-simulate` validates those GitHub Actions assumptions offline without live GitHub API calls or secrets. `release-check` validates release hygiene and skips remote install smoke unless explicitly requested.
