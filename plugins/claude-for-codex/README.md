# Claude for Codex

Codex plugin that invokes Claude Code for independent read-only review, adversarial review, implementation planning, rescue diagnosis, and tracked job workflows.

## Publishing Metadata

This plugin is prepared for a Codex plugin page with:

- Name: Claude for Codex
- Category: Productivity
- Developer: fanghao
- License: MIT
- Homepage: https://github.com/yilibinbin/claude-for-codex
- Repository: https://github.com/yilibinbin/claude-for-codex
- Marketplace id: `external-models-for-codex-local`
- Plugin id: `claude-for-codex`
- Current version: `0.4.0`

Published capabilities:

- Read-only Claude Code review of working-tree or branch changes.
- Adversarial review for assumptions, rollback risks, hidden failure modes, and simpler alternatives.
- Claude implementation planning for Codex to reconcile before editing.
- Read-only Claude rescue diagnosis when Codex is stuck or validation is failing.
- Multi-role review fan-out across correctness, security, tests, release, and adversarial perspectives.
- Tracked job lifecycle commands for status, result retrieval, and conservative cancellation.
- Background execution for long Claude review, adversarial review, multi-review, and rescue jobs.
- Session/UserPrompt hooks for session state, turn baselines, and unread-result reminders.
- Opt-in Stop hook review gate that blocks only on explicit Claude `BLOCK:` verdicts.
- Codex-Claude collaboration loop for plan, review, reconcile, implement, and report workflows.

Safety and operating model:

- Review commands invoke Claude Code with read-only tool permissions.
- Rescue is read-only by default; `rescue --write` is explicit opt-in and records git fingerprints before and after Claude runs.
- Codex remains responsible for applying or rejecting Claude findings.
- The Stop gate is disabled by default after installation.
- Claude CLI failures, authentication failures, rate limits, timeouts, or invalid gate output fail open and emit warnings.
- The Stop gate reviews current git working-tree changes, not an exact previous-turn file list.

Important routing note:

- Claude for Codex is a skills-and-hook plugin, not an MCP/app tool plugin.
- `tool_search` only discovers callable tools exposed by MCP servers or apps, so it is expected not to return a `claude-for-codex` tool.
- When this plugin is installed and enabled, Codex should route through the `claude-for-codex:*` skills, which run `scripts/claude-companion.mjs`; lack of a `tool_search` result is not an installation failure.

## Requirements

- Codex CLI with plugin support
- Claude Code CLI available as `claude`, configured with `CLAUDE_CODE_PATH`, or installed at `~/.local/bin/claude`
- Git repository for review scope collection
- Node.js 20 or newer

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

```bash
codex plugin marketplace add yilibinbin/claude-for-codex --ref main
codex plugin add claude-for-codex@external-models-for-codex-local
```

`external-models-for-codex-local` is the local marketplace for this repository's Codex plugins that connect to external model CLIs. It currently publishes both Claude for Codex and Gemini for Codex.

The `yilibinbin/claude-for-codex` owner/repo form assumes this repository remains under that GitHub owner. If your Codex setup requires an explicit Git URL, use `https://github.com/yilibinbin/claude-for-codex.git` or `git@github.com:yilibinbin/claude-for-codex.git`.

## Upgrade

```bash
codex plugin marketplace upgrade external-models-for-codex-local
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@external-models-for-codex-local
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
- `claude-rescue`: read-only diagnosis by default, or explicit `--write` repair when the user asks Claude to modify files.
- `claude-status`: list tracked Claude jobs for the current workspace.
- `claude-result`: retrieve a tracked Claude job result by job id.
- `claude-cancel`: cancel only when the runtime can safely validate the job state.
- `claude-review-gate`: configure the opt-in Stop-time Claude review gate.
- `claude-plan`: independent Claude implementation plan for Codex reconciliation.
- `claude-collaboration-loop`: full plan, reconcile, implement, adversarial review, report workflow.

## Direct Runtime Commands

Run from the repository root:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --base main challenge the rollback design
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --roles correctness,security --scope branch --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs rescue diagnose the failing release validation
node plugins/claude-for-codex/scripts/claude-companion.mjs rescue --write fix the failing test
node plugins/claude-for-codex/scripts/claude-companion.mjs review --background --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs jobs
node plugins/claude-for-codex/scripts/claude-companion.mjs result <job-id>
node plugins/claude-for-codex/scripts/claude-companion.mjs cancel <job-id>
node plugins/claude-for-codex/scripts/claude-companion.mjs plan build the plugin and include tests
node plugins/claude-for-codex/scripts/claude-companion.mjs status
```

`multi-review` runs several role-specialized Claude review prompts and prints one section per role plus an orchestration summary. This is role fan-out from the plugin runtime, not Claude native background agents. It is read-only; Codex must reconcile findings before any follow-up changes.

`jobs`, `result`, and `cancel` are the stable lifecycle surface for tracked Claude work. The existing `status` command remains a diagnostic command that calls `claude agents --json --cwd`; it is intentionally not repurposed for job listing. Use `--background` on `review`, `adversarial-review`, `multi-review`, or `rescue` to start a tracked job. Add `--wait` when a script should block until that job reaches a terminal state.

## Host-forwarded background jobs

`--background` supports a Codex host-forwarded path. Skills first reserve a job with `reserve-job`, then Codex dispatches exactly one forwarding subagent to run the returned `workerCommand`. The child worker only executes `run-reserved-job`; it does not inspect or reinterpret repository state. Existing detached runtime background jobs remain as a compatibility fallback.

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

Use structured adversarial output when Codex needs a machine-checked verdict:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --json --base main
```

The JSON contract is `{ "verdict": "PASS|CONTESTED|REJECT", "summary": "...", "findings": [], "next_steps": [] }`. Mixed or fenced Claude output is parsed and validated before being returned.

## Hooks And Background Jobs

`hooks/hooks.json` registers `SessionStart`, `SessionEnd`, `UserPromptSubmit`, and `Stop` hooks. Session hooks record the active session and attempt safe cleanup of tracked queued/running jobs at session end. The prompt-submit hook records a turn baseline fingerprint and emits a short stderr reminder for unread terminal job results.

If the local Codex runtime does not expose one of these hook events, the plugin degrades to explicit `jobs`, `result`, and `cancel` commands. The Stop hook remains opt-in through setup state.

## Read-Only Git Boundary

Claude review commands run with `Read,Grep,Glob` only and explicitly disallow `Edit,Write,MultiEdit,Bash`. Read-only Claude review also receives a strict MCP config for bounded Git inspection. The bundled read-only Git MCP server exposes status, diff, cached diff, log, show, blame, grep, and ls-files through validated Git arguments; unsupported paths, refs, and operations are rejected before Git is invoked.

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

The v1 gate reviews current git changes, not the exact files changed by the immediately previous Codex turn. It skips non-git directories, clean working trees, recursive Stop-hook invocations, and unchanged diffs that already received an all-`ALLOW:` gate result. Each Claude role has a two-minute timeout inside the overall 15-minute Stop hook budget.

Emergency bypass for the shell environment that launches Codex hooks:

```bash
export CLAUDE_FOR_CODEX_REVIEW_GATE=off
```

The setup output prints the per-workspace `stateFile`; deleting that file also resets the gate to disabled. After installing or upgrading, check Codex Settings > Hooks and trust or enable the `Claude for codex` Stop hook if prompted.

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
6. Run plugin validation: `python3 "$HOME/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py" plugins/claude-for-codex`.
7. Run skill validation: `for d in plugins/claude-for-codex/skills/*; do python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" "$d"; done`.
8. Commit, tag, and push.
