# Claude for Codex

Codex plugin that invokes Claude Code for independent read-only review, adversarial review, implementation planning, rescue diagnosis, and tracked job workflows.

## Publishing Metadata

This plugin is prepared for a Codex plugin page with:

- Name: Claude for Codex
- Category: Productivity
- Developer: fanghao
- License: MIT
- Homepage: https://github.com/yilibinbin/external-models-for-codex
- Repository: https://github.com/yilibinbin/external-models-for-codex
- Marketplace id: `external-models-for-codex`
- Plugin id: `claude-for-codex`
- Current version: `0.14.1`

Published capabilities:

- Read-only Claude Code review of working-tree or branch changes.
- Adversarial review for assumptions, rollback risks, hidden failure modes, and simpler alternatives.
- Claude implementation planning for Codex to reconcile before editing.
- Read-only Claude rescue diagnosis when Codex is stuck or validation is failing.
- Multi-role review fan-out across correctness, security, tests, release, and adversarial perspectives.
- Native SDK subagent review teams with `--backend sdk --agent-team sdk-subagents`.
- Structured `review --json` and role-tagged `multi-review --json` for machine-readable findings.
- Native structured output and sanitized streaming progress with `--native-structured` and `--stream-progress`.
- Tracked job lifecycle commands for status, result retrieval, and conservative cancellation.
- Capability diagnostics for Claude CLI, Git, GitHub CLI, hooks, MCP, and optional semantic providers.
- Optional semantic context for review commands, disabled by default.
- Optional Claude SDK backend for explicitly selected review, gate, plan, and rescue flows.
- Explicit-consent Claude ultrareview with `--confirm-cost`.
- GitHub Actions PR review workflow templates with fork-safe defaults.
- Sanitized per-run review reports that omit prompts, diffs, raw model output, and secrets by default.
- Release checks for manifest, hook, docs, prompt, skill, and secret-scan hygiene.
- Background execution for long Claude review, adversarial review, multi-review, and rescue jobs.
- Session/UserPrompt hooks for session state, turn baselines, and unread-result reminders.
- Opt-in Stop hook review gate that blocks only on explicit Claude `BLOCK:` verdicts.
- Codex-Claude collaboration loop for plan, review, reconcile, implement, and report workflows.

Safety and operating model:

- Review commands invoke Claude Code with read-only tool permissions.
- Read-only CLI review disables Claude Code slash commands, settings sources, and session persistence while preserving the normal Claude authentication path.
- Read-only SDK review disables Claude Code settings, skills, hooks, plugins, and SDK session persistence while preserving the normal Claude authentication path.
- CLI remains the default backend. The SDK backend runs only with `--backend sdk` or `CLAUDE_FOR_CODEX_BACKEND=sdk`.
- Native SDK subagent teams require the SDK backend and keep the read-only Git MCP boundary.
- Ultrareview may use remote/cloud execution and usage-credit billing; it requires `--confirm-cost` or `CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1`.
- Rescue is read-only by default; `rescue --write` is explicit opt-in and records git fingerprints before and after Claude runs.
- Codex remains responsible for applying or rejecting Claude findings.
- The Stop gate is disabled by default after installation.
- Claude CLI failures, authentication failures, rate limits, timeouts, or invalid gate output fail open and emit warnings.
- The Stop gate reviews current git working-tree changes, not an exact previous-turn file list.
- Generated GitHub Actions workflows use `pull_request`, avoid default `pull_request_target`, pin immutable release refs, and skip fork PR Claude/comment/annotation steps by default.

Important routing note:

- Claude for Codex is a skills-and-hook plugin, not an MCP/app tool plugin.
- `tool_search` only discovers callable tools exposed by MCP servers or apps, so it is expected not to return a `claude-for-codex` tool.
- When this plugin is installed and enabled, Codex should route through the `claude-for-codex:*` skills, which run `scripts/claude-companion.mjs`; lack of a `tool_search` result is not an installation failure.

## Requirements

- Codex CLI with plugin support
- Claude Code CLI available as `claude`, configured with `CLAUDE_CODE_PATH`, or installed at `~/.local/bin/claude`
- Git repository for review scope collection
- Node.js 20 or newer
- Optional `@anthropic-ai/claude-agent-sdk` package for SDK native subagent mode; `@anthropic-ai/claude-code` remains supported as a compatibility fallback

## Setup Check

From the plugin directory:

```bash
cd plugins/claude-for-codex
node scripts/claude-companion.mjs setup
```

Expected output includes:

```json
{
  "claudeAvailable": true,
  "claudeCommand": "/Users/you/.local/bin/claude",
  "gitAvailable": true,
  "reviewGate": {
    "enabled": false,
    "mode": "multi-role"
  }
}
```

Claude CLI resolution order:

1. `CLAUDE_CODE_PATH` when it points to an executable file.
2. `claude` from the current `PATH`.
3. `~/.local/bin/claude`, which covers the default Claude install path that Codex Desktop may omit from `PATH`.

If setup reports `claudeAvailable: false` but Claude is installed elsewhere, set `CLAUDE_CODE_PATH` to the absolute executable path before running Codex.

## Install From This Repository

```bash
codex plugin marketplace add .
```

Then install or enable `claude-for-codex` from the Codex plugin UI.
After installing or upgrading, open Codex Settings > Hooks and trust or enable the `Claude for Codex` Stop hook if you want the opt-in review gate available.

## Remote Install

Install the released Claude plugin from the immutable Claude release ref:

```bash
codex plugin marketplace add yilibinbin/external-models-for-codex --ref claude-for-codex-v0.14.1
codex plugin add claude-for-codex@external-models-for-codex
```

`external-models-for-codex` is this repository's Codex marketplace for plugins that connect Codex to external model CLIs. It currently publishes both Claude for Codex and Gemini for Codex.

The `yilibinbin/external-models-for-codex` owner/repo form assumes this repository remains under that GitHub owner. If your Codex setup requires an explicit Git URL, use `https://github.com/yilibinbin/external-models-for-codex.git` or `git@github.com:yilibinbin/external-models-for-codex.git`.

For local development only, replace the release ref with `--ref main`.

## Upgrade

```bash
codex plugin marketplace upgrade external-models-for-codex
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@external-models-for-codex
```

## Rollback

To roll back from `0.4.0` to a `0.3.x` install, first disable the review gate in each protected repository:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --disable-review-gate
```

Then remove or downgrade the plugin through Codex. If Codex Settings > Hooks still shows trusted entries for `SessionStart`, `SessionEnd`, `UserPromptSubmit`, or `Stop` that point at missing files, remove or disable those hook entries before continuing work. The repo-external state directory printed by `setup` may be deleted to reset gate/job/session state for that workspace.

## Skills

- `claude-review`: normal read-only review of current changes or `--base <ref>`.
- `claude-adversarial-review`: steerable challenge review for design assumptions and failure modes.
- `claude-multi-review`: opt-in role fan-out review across multiple read-only Claude perspectives.
- `claude-ultrareview`: explicit Claude cloud ultrareview after user consent for possible usage-credit billing.
- `claude-rescue`: read-only diagnosis by default, or explicit `--write` repair when the user asks Claude to modify files.
- `claude-status`: list tracked Claude jobs for the current workspace.
- `claude-result`: retrieve a tracked Claude job result by job id.
- `claude-cancel`: cancel only when the runtime can safely validate the job state.
- `claude-review-gate`: configure the opt-in Stop-time Claude review gate.
- `claude-github-actions-review`: generate or validate fork-safe GitHub Actions PR review workflows.
- `claude-plan`: independent Claude implementation plan for Codex reconciliation.
- `claude-collaboration-loop`: full plan, reconcile, implement, adversarial review, report workflow.

## Direct Runtime Commands

Run from the repository root:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs review --json --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs review --backend sdk --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --base main challenge the rollback design
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --json --roles correctness,security --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --backend sdk --agent-team sdk-subagents --json --native-structured --stream-progress --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --roles correctness,security --scope branch --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs ultrareview --confirm-cost --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs rescue diagnose the failing release validation
node plugins/claude-for-codex/scripts/claude-companion.mjs rescue --write fix the failing test
node plugins/claude-for-codex/scripts/claude-companion.mjs review --background --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs jobs
node plugins/claude-for-codex/scripts/claude-companion.mjs result <job-id>
node plugins/claude-for-codex/scripts/claude-companion.mjs cancel <job-id>
node plugins/claude-for-codex/scripts/claude-companion.mjs capabilities
node plugins/claude-for-codex/scripts/claude-companion.mjs report --latest
node plugins/claude-for-codex/scripts/claude-companion.mjs release-check
node plugins/claude-for-codex/scripts/claude-companion.mjs release-check --ci-simulate
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions render
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions init --write
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions validate
node plugins/claude-for-codex/scripts/claude-companion.mjs plan build the plugin and include tests
node plugins/claude-for-codex/scripts/claude-companion.mjs status
```

`multi-review` runs several role-specialized Claude review prompts in parallel by default and prints one section per role plus an orchestration summary. This plugin-managed CLI fan-out is read-only; Codex must reconcile findings before any follow-up changes. Use `--sequential` to run one role at a time for debugging or rate-limit-sensitive environments. For Claude native SDK mode, use `multi-review --backend sdk --agent-team sdk-subagents`; this requires the Claude SDK backend, configures native subagents for the selected roles, and rejects incompatible `--sequential` or non-SDK combinations before invoking Claude.

Native SDK mode resolves `@anthropic-ai/claude-agent-sdk` first and keeps `@anthropic-ai/claude-code` as a compatibility fallback. It is opt-in and experimental until live SDK subagent smoke tests are stable; plugin-managed CLI `multi-review` remains the default. Combine `--json --native-structured` to request a schema-backed SDK aggregate where `role_results[].result.review` is a full review JSON object. The plugin validates that object locally and does not serialize raw role text or raw SDK `structured_output` into reports. Add `--stream-progress` to show sanitized progress events without printing raw SDK chunks or storing raw SDK messages in reports.

Role packs are named reviewer presets for `multi-review`. Use `roles list`, `roles inspect <pack>`, and `multi-review --role-pack <pack>` for built-in packs such as `minimal`, `release`, `security`, and `default`. User-authored JSON packs can be validated with `roles validate <file>`, but they are validate/inspect-only and are not executable by review commands. Role packs are plugin-managed presets, not native Claude subagents, and they cannot grant tools, shell commands, hooks, MCP servers, environment variables, backend mode, or write permissions.

Mailbox and advisory leases are optional coordination metadata for long-running review. `mailbox list|show|post` stores sanitized summaries only under repo-external plugin state. `leases list|claim|release` declares path attention without locking files. Lease conflicts are warnings only; they do not change review verdicts or `review-gate` behavior.

`review --json` asks Claude for a normalized `{verdict, summary, findings, next_steps}` object using `approve` or `needs-attention`. `multi-review --json` asks every role for the same schema and returns one aggregate object with role-tagged findings and per-role results. Invalid or malformed structured output exits non-zero and includes the raw Claude output for diagnosis.

For `--json` modes, exit status reports whether the Claude invocation and JSON parsing succeeded. Callers must inspect the returned `verdict` to decide whether findings need attention.

`jobs`, `result`, and `cancel` are the stable lifecycle surface for tracked Claude work. The existing `status` command remains a diagnostic command that calls `claude agents --json --cwd`; it is intentionally not repurposed for job listing. Use `--background` on `review`, `adversarial-review`, `multi-review`, or `rescue` to start a tracked job. Add `--wait` when a script should block until that job reaches a terminal state.

`capabilities` prints JSON diagnostics for the resolved Claude CLI, supported Claude flags, optional SDK availability, Git/GitHub CLI availability, hook trust, the bundled Git MCP server, and path-only detection of future semantic context providers. It does not execute external semantic providers.

`--backend sdk` opts into the Claude SDK backend when `@anthropic-ai/claude-agent-sdk` or `@anthropic-ai/claude-code` is importable locally or through a controlled global npm resolution fallback. SDK review mode uses explicit read-only allowed tools, denies configured write-tool candidates such as `Edit`, `Write`, `MultiEdit`, and `Bash` when the installed Claude runtime recognizes them, disables SDK settings sources, skills, hooks, plugins, and session persistence, and reuses the strict read-only Git MCP config. If the SDK cannot be resolved or cannot provide the required safety controls, the command fails before Claude invocation. Unset `CLAUDE_FOR_CODEX_BACKEND` or pass `--backend cli` to return to the default CLI backend.

`ultrareview` forwards to Claude's native cloud ultrareview command. It is never used by hooks or default review paths, and it refuses to run unless the user has explicitly consented with `--confirm-cost` or `CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1` because the command may use remote/cloud execution and usage-credit billing.

Semantic context is disabled by default. Use `--semantic-context <provider>` on `review`, `multi-review`, `adversarial-review`, or `review-gate` only after configuring a repo-external provider. Provider commands must be argv arrays, run with an allowlist-only environment, stay outside the workspace, and return workspace-bound JSON. Semantic context is advisory; Claude findings still need changed-file or git evidence. If semantic context fails in `review-gate`, the gate records degraded metadata such as `DEGRADED_PASS` and still blocks only on explicit Claude `BLOCK:`.

`report --latest` reads the latest sanitized review report from the repo-external plugin data directory. Reports are minimal metadata only: command, scope, roles/lenses, backend, model/effort, timestamps, exit status, output byte counts, and structured verdict/finding counts when available. Reports do not store prompts, source code, diffs, raw model output, environment variables, or raw absolute workspace paths by default. Set `CLAUDE_FOR_CODEX_NO_TELEMETRY=1` to disable all non-job report writes.

`github-actions render` prints a GitHub Actions PR review workflow and writes nothing. `github-actions init --write` writes `.github/workflows/claude-for-codex-review.yml` and refuses to overwrite without `--force`. `github-actions validate` checks minimal permissions, fork-safe gates, immutable release refs, GitHub context env mapping, absence of local absolute paths, and no default `pull_request_target`. Checks annotations are opt-in with `--annotations` because they add `checks: write`.

The generated GitHub Actions workflow is a template. It uses `pull_request`, pins `codex plugin marketplace add yilibinbin/external-models-for-codex --ref claude-for-codex-v0.14.1`, maps GitHub context through environment variables before shell use, uploads structured review JSON as a short-retention artifact, and skips Claude/comment/annotation publishing for fork PRs by default. Maintainers must configure Claude authentication or secrets explicitly in their CI environment. A future unsafe `pull_request_target` variant would need separate review; this version does not generate one.

`release-check` validates release hygiene for this repository. `release-check --ci-simulate` adds fixture-driven GitHub Actions validation without calling the live GitHub API, reading user HOME, requiring secrets, or using local Codex caches. Remote install smoke is skipped by default for local development; use `--remote-install --ref claude-for-codex-v0.14.1` for a fail-soft smoke or `--require-remote-install --ref claude-for-codex-v0.14.1` when a release must fail if GitHub install fails.

## Host-forwarded background jobs

`--background` supports a Codex host-forwarded path. Skills first reserve a job with `reserve-job`, then Codex dispatches exactly one forwarding subagent to run the returned `workerCommand`. The child worker only executes `run-reserved-job`; it does not inspect or reinterpret repository state. Existing detached runtime background jobs remain as a compatibility fallback.

## Codex subagent delegation

For foreground read-only delegation, the parent Codex turn uses `subagent-command` for review commands instead of hand-building Claude invocations:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command review "$ARGUMENTS"
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command adversarial-review "$ARGUMENTS"
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command multi-review "$ARGUMENTS"
```

`subagent-command` prints JSON containing an absolute `workerCommand` argv array and the exact `cwd` to use. Dispatch exactly one Codex subagent to run that argv exactly once from the returned `cwd`. The child must not inspect or reinterpret the repository before execution, and must not replace the plugin call with raw `claude -p` or any other hand-built Claude CLI command.

Use `reserve-job` for `--background` delegation so the plugin runtime tracks the job. `ultrareview` and every `--write` command are intentionally not delegatable.

`rescue --write` requires a git repository and runs Claude Code with write permissions. The runtime writes a before/after working-tree fingerprint to stderr; Codex must inspect the resulting diff before reporting success.

Default roles:

- `correctness`: bugs, regressions, edge cases, and behavioral contract breaks.
- `security`: read-only safety, secrets exposure, injection risks, and unsafe command or path handling.
- `tests`: missing, brittle, or overfit tests and release validation gaps.
- `release`: install, marketplace, versioning, documentation, and upgrade risks.
- `adversarial`: assumptions, simpler alternatives, hidden costs, and failure modes.

Use `--roles correctness,security` for an ordered comma-separated subset. Use repeated `--role` flags, such as `--role release --role adversarial`, when shell composition or incremental selection is clearer.

## Enhanced Adversarial Review

`adversarial-review` uses an intent-first verdict workflow inspired by cross-model adversarial review practice. Claude must infer the author's intent, review through the `skeptic`, `architect`, and `minimalist` lenses, and produce:

- `## Intent`
- `## Verdict: PASS | CONTESTED | REJECT`
- `## Findings`
- `## What Went Well`
- `## Lead Judgment`

Use lens selection when a review needs a narrower challenge:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --adversarial-lenses skeptic,minimalist --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --roles skeptic,architect,minimalist --base main
```

Use `adversarial-review --parallel` to run each selected adversarial lens as an independent Claude reviewer process and aggregate their outputs. `--json` remains available for the single-call adversarial verdict path and is intentionally not combined with parallel lens execution.

Use structured adversarial output when Codex needs a machine-checked verdict:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --json --base main
```

The adversarial JSON contract is `{ "verdict": "PASS|CONTESTED|REJECT", "summary": "...", "findings": [], "next_steps": [] }`. It intentionally keeps adversarial verdict semantics separate from normal `review --json`, which uses `approve|needs-attention`. Mixed or fenced Claude output is parsed and validated before being returned.

## Hooks And Background Jobs

`hooks/hooks.json` registers `SessionStart`, `SessionEnd`, `UserPromptSubmit`, and `Stop` hooks. Session hooks record the active session and attempt safe cleanup of tracked queued/running jobs at session end. The prompt-submit hook records a turn baseline fingerprint and emits a short stderr reminder for unread terminal job results.

If the local Codex runtime does not expose one of these hook events, the plugin degrades to explicit `jobs`, `result`, and `cancel` commands. The Stop hook remains opt-in through setup state. The review gate uses the `UserPromptSubmit` turn-baseline fingerprint to avoid reviewing old dirty workspace changes when the current turn did not change the working tree. Payload-based classification of status/setup/report-only Stop turns is intentionally deferred until a real Codex Stop payload exposes a verified edit/no-edit signal.

## Read-Only Git Boundary

Claude review commands expose `Read,Grep,Glob` only and pass a configured write-tool deny-list for recognized Claude runtime tools. Read-only Claude CLI review also passes `--disable-slash-commands`, `--no-session-persistence`, and an empty `--setting-sources` value so repository-local Claude settings, skills, hooks, slash commands, and sessions cannot add write side effects. Read-only Claude review also receives a strict MCP config for bounded Git inspection. The bundled read-only Git MCP server exposes status, diff, cached diff, log, show, blame, grep, and ls-files through validated Git arguments; unsupported paths, refs, and operations are rejected before Git is invoked.

## Stop Review Gate

The plugin includes `hooks/hooks.json` so Codex can discover a Stop hook. Do not add a `hooks` field to `.codex-plugin/plugin.json`; standard hook files are discovered from `hooks/hooks.json`, and declaring them in the manifest can fail validation or duplicate-load the hook.

The hook is installed but disabled by default. Enable it from the repository you want to protect:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --enable-review-gate --review-gate-mode multi-role
```

Disable it:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --disable-review-gate
```

When enabled, Stop runs a multi-role Claude gate over current git working-tree changes. It blocks only when Claude explicitly returns `BLOCK:`. Claude CLI failures, timeouts, invalid output, missing auth, or missing Claude warn to stderr and allow Stop to continue.

The v1 gate reviews current git changes, not the exact files changed by the immediately previous Codex turn. It skips non-git directories, clean working trees, recursive Stop-hook invocations, unchanged turn-baseline fingerprints, and unchanged diffs that already received an all-`ALLOW:` gate result. Each Claude role has a two-minute timeout inside the overall 15-minute Stop hook budget.

Emergency bypass for the shell environment that launches Codex hooks:

```bash
export CLAUDE_FOR_CODEX_REVIEW_GATE=off
```

The setup output prints the per-workspace `stateFile`; deleting that file also resets the gate to disabled. After installing or upgrading, check Codex Settings > Hooks and trust or enable the `Claude for codex` Stop hook if prompted.

## Review Result Handling

Claude review output is a review artifact, not implementation authority. Preserve file paths, line numbers, role names, uncertainty markers, and residual-risk notes when reporting results. Do not auto-fix review findings in the same step unless the user explicitly asks which findings to adopt. If Claude fails, returns malformed structured output, or reports setup/auth problems, report that failure directly instead of substituting Codex guesses.

Tiny one-to-two file reviews can run foreground. Broader reviews, unclear scope, untracked directories, or multi-role/adversarial reviews should normally use `--background` and retrieve results with `claude-result`.

## Verification

Default tests use a fake Claude executable and do not require network or model access. They verify the read-only CLI arguments passed to Claude, but the actual installed Claude CLI is checked by the opt-in integration test below.

```bash
python3 -m pytest -q
```

Run the opt-in real Claude CLI compatibility check when preparing a release:

```bash
RUN_CLAUDE_INTEGRATION=1 python3 -m pytest tests/test_claude_for_codex_plugin.py::test_real_claude_permission_mode_when_enabled -q
```

## Release Checklist

1. Update `.codex-plugin/plugin.json` version.
2. Update `CHANGELOG.md`.
3. Run default tests: `python3 -m pytest -q`.
4. Run the real Claude CLI compatibility check: `RUN_CLAUDE_INTEGRATION=1 python3 -m pytest tests/test_claude_for_codex_plugin.py::test_real_claude_permission_mode_when_enabled -q`.
5. Run hook syntax validation: `node --check plugins/claude-for-codex/hooks/claude-review-gate.mjs`.
6. Run runtime syntax validation: `node --check plugins/claude-for-codex/scripts/claude-companion.mjs`.
7. Run plugin validation: `python3 "$HOME/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py" plugins/claude-for-codex`.
8. Run skill validation: `for d in plugins/claude-for-codex/skills/*; do python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" "$d"; done`.
9. Commit, tag, and push.
