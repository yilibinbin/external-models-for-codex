# External Models for Codex

[中文](docs/README.zh-CN.md) | [English](docs/README.en.md)

External Models for Codex is a Codex plugin marketplace for external model CLI workflows. It publishes provider-specific Codex plugins that let Codex ask another local model CLI to review, plan, challenge, rescue, and gate work without turning that model into the implementation authority.

The marketplace currently includes:

- Claude for Codex: calls the local Claude Code CLI for read-only review, adversarial critique, implementation planning, multi-role review, native SDK subagent teams, rescue diagnosis, background jobs, structured review output, explicit-cost ultrareview, and an optional Stop hook review gate.
- Gemini for Codex: calls the local Gemini CLI for the same Codex-side review and planning loop using Gemini plan mode, schema-backed structured review, native session capability checks, and bounded inline git context.

External Models for Codex 是一个面向 Codex 的外部模型插件市场，用于把本地 Claude Code CLI、Gemini CLI 等外部模型接入 Codex 的审阅、规划、对抗性复审、救援诊断和 Hook 门禁流程。Codex 仍负责实现和最终决策，外部模型提供独立第二视角。

当前市场包含：

- Claude for Codex：调用本地 Claude Code CLI，提供只读审阅、对抗性审阅、独立规划、多角色审阅、原生 SDK subagent 团队、救援诊断、后台任务、结构化审阅输出、需要费用确认的 ultrareview 和可选 Stop Hook 门禁。
- Gemini for Codex：调用本地 Gemini CLI，以 Gemini plan mode、有界 inline git context、结构化审阅和能力探测支持第二模型复审与规划。

## Install

Remote install from GitHub:

```bash
codex plugin marketplace add yilibinbin/external-models-for-codex --ref claude-for-codex-v0.14.0
codex plugin add claude-for-codex@external-models-for-codex
```

The immutable `claude-for-codex-v0.14.0` ref is intended for installing the Claude plugin slice from this multi-plugin marketplace. Install Gemini from its own release ref or from `main` during development.

Upgrade an existing install:

```bash
codex plugin marketplace upgrade external-models-for-codex
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@external-models-for-codex
codex plugin add gemini-for-codex@external-models-for-codex
```

Local development install from this repository:

```bash
codex plugin marketplace add .
codex plugin add claude-for-codex@external-models-for-codex
codex plugin add gemini-for-codex@external-models-for-codex
```

## Requirements

- Codex CLI with plugin support
- Claude Code CLI available as `claude`
- Optional `@anthropic-ai/claude-agent-sdk` package for Claude `--backend sdk --agent-team sdk-subagents` native review mode
- Gemini CLI available as `gemini` for Gemini for Codex
- Node.js 20 or newer
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
- `claude-multi-review --backend sdk --agent-team sdk-subagents`: run Claude native SDK subagent review teams; add `--native-structured` for SDK schema-backed output and `--stream-progress` for sanitized progress events.
- `claude-ultrareview`: run Claude cloud ultrareview only after explicit `--confirm-cost` consent for possible usage-credit billing.
- `claude-role-packs`: inspect built-in Claude reviewer presets and validate user-authored role-pack JSON.
- `claude-mailbox`: inspect sanitized review/job coordination summaries.
- `claude-leases`: inspect, claim, or release advisory path attention leases.
- `claude-review-gate`: configure the optional Stop hook review gate.
- `claude-collaboration-loop`: run a plan, reconcile, implement, review, and report workflow.
- `gemini-review`, `gemini-adversarial-review`, `gemini-plan`, `gemini-multi-review`, `gemini-rescue`: Gemini CLI equivalents that stay read-only. `gemini-review --structured` validates schema-backed findings, `gemini-multi-review` runs parallel role fan-out and supports `--native-agents`, and Gemini session flags are capability-gated from the installed CLI.

These are skills-and-hook plugins, not MCP/app tool plugins. It is expected that `tool_search` will not expose callable `claude-for-codex` or `gemini-for-codex` tools. Codex should route through the provider skills such as `claude-for-codex:*` and `gemini-for-codex:*`.

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
- [Gemini Plugin README](plugins/gemini-for-codex/README.md)
- [Changelog](plugins/claude-for-codex/CHANGELOG.md)

## Safety Model

Review workflows invoke Claude with read-only permissions. Codex remains responsible for applying or rejecting Claude findings. CLI mode remains the default; `--backend sdk` is explicit, and native SDK subagent teams additionally require `--agent-team sdk-subagents`. SDK mode resolves `@anthropic-ai/claude-agent-sdk` with `@anthropic-ai/claude-code` as a compatibility fallback. Ultrareview may use remote/cloud execution and usage-credit billing, so it requires `--confirm-cost` or `CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1`. The Stop gate blocks only when Claude explicitly returns `BLOCK:`; Claude runtime failures, authentication failures, rate limits, invalid output, or timeouts fail open with warnings.

Gemini workflows invoke Gemini with `--approval-mode=plan --output-format=json --prompt` and bounded inline git context. Gemini MCP and native extension packaging are deferred until their CLI configuration path is validated.

Gemini for Codex also includes lifecycle hooks for same-session cleanup and unread-result reminders, a noninteractive `recommend-execution-mode` helper, and optional Gemini-native session flags such as `--resume`, `--session-id`, and `--worktree` when the local Gemini CLI reports support.
