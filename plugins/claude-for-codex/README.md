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
  "gitAvailable": true
}
```

## Install From This Repository

```bash
codex plugin marketplace add .
```

Then install or enable `claude-for-codex` from the Codex plugin UI.

## Remote Install

```bash
codex plugin marketplace add git@github.com:yilibinbin/claude-for-codex.git --ref main
codex plugin add claude-for-codex@claude-for-codex-local
```

`claude-for-codex-local` is the stable marketplace id for this repository, even when installed from GitHub.

## Upgrade

```bash
codex plugin marketplace upgrade claude-for-codex-local
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@claude-for-codex-local
```

## Skills

- `claude-review`: normal read-only review of current changes or `--base <ref>`.
- `claude-adversarial-review`: steerable challenge review for design assumptions and failure modes.
- `claude-plan`: independent Claude implementation plan for Codex reconciliation.
- `claude-collaboration-loop`: full plan, reconcile, implement, adversarial review, report workflow.

## Direct Runtime Commands

Run from the repository root:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --base main challenge the rollback design
node plugins/claude-for-codex/scripts/claude-companion.mjs plan build the plugin and include tests
node plugins/claude-for-codex/scripts/claude-companion.mjs status
```

## Verification

Default tests use a fake Claude executable and do not require network or model access:

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
3. Run `python3 -m pytest -q`.
4. Run `python3 /Users/fanghao/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/claude-for-codex`.
5. Run all skill validators.
6. Commit, tag, and push.
