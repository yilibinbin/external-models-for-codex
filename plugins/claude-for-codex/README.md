# Claude for Codex

Codex plugin that invokes Claude Code for independent read-only review, adversarial review, and implementation planning.

## Requirements

- Codex CLI with plugin support
- Claude Code CLI available as `claude`
- Git repository for review scope collection
- Node.js 18 or newer

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
  "gitAvailable": true,
  "reviewGate": {
    "enabled": false,
    "mode": "multi-role"
  }
}
```

## Install From This Repository

```bash
codex plugin marketplace add .
```

Then install or enable `claude-for-codex` from the Codex plugin UI.

## Remote Install

```bash
codex plugin marketplace add yilibinbin/claude-for-codex --ref main
codex plugin add claude-for-codex@claude-for-codex-local
```

This repository currently keeps `claude-for-codex-local` as its marketplace id for compatibility with existing installs, even when installed from GitHub. If the marketplace id changes later, update the install and upgrade commands together.

The `yilibinbin/claude-for-codex` owner/repo form assumes this repository remains under that GitHub owner. If your Codex setup requires an explicit Git URL, use `https://github.com/yilibinbin/claude-for-codex.git` or `git@github.com:yilibinbin/claude-for-codex.git`.

## Upgrade

```bash
codex plugin marketplace upgrade claude-for-codex-local
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@claude-for-codex-local
```

## Skills

- `claude-review`: normal read-only review of current changes or `--base <ref>`.
- `claude-adversarial-review`: steerable challenge review for design assumptions and failure modes.
- `claude-multi-review`: opt-in role fan-out review across multiple read-only Claude perspectives.
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
node plugins/claude-for-codex/scripts/claude-companion.mjs plan build the plugin and include tests
node plugins/claude-for-codex/scripts/claude-companion.mjs status
```

`multi-review` runs several role-specialized Claude review prompts and prints one section per role plus an orchestration summary. This is role fan-out from the plugin runtime, not Claude native background agents. It is read-only; Codex must reconcile findings before any follow-up changes.

Default roles:

- `correctness`: bugs, regressions, edge cases, and behavioral contract breaks.
- `security`: read-only safety, secrets exposure, injection risks, and unsafe command or path handling.
- `tests`: missing, brittle, or overfit tests and release validation gaps.
- `release`: install, marketplace, versioning, documentation, and upgrade risks.
- `adversarial`: assumptions, simpler alternatives, hidden costs, and failure modes.

Use `--roles correctness,security` for an ordered comma-separated subset. Use repeated `--role` flags, such as `--role release --role adversarial`, when shell composition or incremental selection is clearer.

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
