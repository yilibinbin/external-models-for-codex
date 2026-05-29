# Claude for Codex

[中文](docs/README.zh-CN.md) | [English](docs/README.en.md)

Claude for Codex is a Codex plugin that lets Codex call the local Claude Code CLI for independent read-only review, adversarial critique, implementation planning, multi-role review, and an optional Stop hook review gate.

Claude for Codex 是一个 Codex 插件，用于在 Codex 中调用本地 Claude Code CLI，提供只读代码审阅、对抗性审阅、独立规划、多角色审阅，以及可选的 Stop Hook 审阅门禁。

## Install

Remote install from GitHub:

```bash
codex plugin marketplace add yilibinbin/claude-for-codex --ref main
codex plugin add claude-for-codex@claude-for-codex-local
```

Upgrade an existing install:

```bash
codex plugin marketplace upgrade claude-for-codex-local
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@claude-for-codex-local
```

Local development install from this repository:

```bash
codex plugin marketplace add .
codex plugin add claude-for-codex@claude-for-codex-local
```

## Requirements

- Codex CLI with plugin support
- Claude Code CLI available as `claude`
- Node.js 18 or newer
- Git repository for review context collection

Check runtime status:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup
```

## What It Provides

- `claude-review`: read-only Claude review of local changes or branch diffs.
- `claude-adversarial-review`: challenge assumptions, tradeoffs, rollback paths, and hidden failure modes.
- `claude-plan`: ask Claude for an independent implementation plan before Codex edits.
- `claude-multi-review`: run role-based review across correctness, security, tests, release, and adversarial perspectives.
- `claude-review-gate`: configure the optional Stop hook review gate.
- `claude-collaboration-loop`: run a plan, reconcile, implement, review, and report workflow.

This is a skills-and-hook plugin, not an MCP/app tool plugin. It is expected that `tool_search` will not expose a `claude-for-codex` callable tool. Codex should route through the `claude-for-codex:*` skills.

## Stop Review Gate

The Stop hook is installed but disabled by default. Enable it in the repository you want to protect:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --enable-review-gate --review-gate-mode multi-role
```

Disable it:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --disable-review-gate
```

After installing or upgrading, open Codex Settings > Hooks and trust or enable the `Claude for Codex` Stop hook if prompted.

## Documentation

- [中文文档](docs/README.zh-CN.md)
- [English documentation](docs/README.en.md)
- [Plugin README](plugins/claude-for-codex/README.md)
- [Changelog](plugins/claude-for-codex/CHANGELOG.md)

## Safety Model

Review workflows invoke Claude with read-only permissions. Codex remains responsible for applying or rejecting Claude findings. The Stop gate blocks only when Claude explicitly returns `BLOCK:`; Claude runtime failures, authentication failures, rate limits, invalid output, or timeouts fail open with warnings.

