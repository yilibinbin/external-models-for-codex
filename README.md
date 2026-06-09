# External Models for Codex

[中文](docs/README.zh-CN.md) | [English](docs/README.en.md)

External Models for Codex is a Codex plugin marketplace for external model CLI workflows. It publishes provider-specific Codex plugins that let Codex ask another local model CLI to review, plan, challenge, rescue, and gate work without turning that model into the implementation authority.

The marketplace currently includes:

- Claude for Codex: calls the local Claude Code CLI for read-only review, adversarial critique, implementation planning, multi-role review, native SDK subagent teams, rescue diagnosis, background jobs, structured review output, explicit-cost ultrareview, and an optional Stop hook review gate. It also includes natural-language routing so Codex can map "strict", "strongest local", "native subagents", and "background review" requests to Claude-native plugin arguments without requiring users to write internal flags.
- Gemini for Codex: calls the legacy Gemini CLI (`gemini`) for Gemini-only read-only review, planning, rescue diagnosis, structured review output, and Gemini CLI-native session capability checks.
- Antigravity for Codex: calls Google Antigravity CLI (`agy`) for mature plugin-managed review workflows: read-only review, adversarial critique, planning, rescue diagnosis, multi-role review, structured reports, role packs, background jobs, mailbox/leases, lifecycle hooks, GitHub Actions workflow rendering, release checks, opt-in real smoke, and an opt-in Stop hook gate with explicit Gemini or Claude model-provider selection.

External Models for Codex 是一个面向 Codex 的外部模型插件市场，用于把本地 Claude Code CLI、Antigravity CLI、Gemini CLI 等外部模型接入 Codex 的审阅、规划、对抗性复审、救援诊断和 Hook 门禁流程。Codex 仍负责实现和最终决策，外部模型提供独立第二视角。

当前市场包含：

- Claude for Codex：调用本地 Claude Code CLI，提供只读审阅、对抗性审阅、独立规划、多角色审阅、原生 SDK subagent 团队、救援诊断、后台任务、结构化审阅输出、需要费用确认的 ultrareview 和可选 Stop Hook 门禁；同时支持自然语言路由，让 Codex 把“严格审阅”“最强本地 Claude”“原生 subagents”“后台审阅”等意图映射到 Claude 原生插件参数，而不要求用户手写内部参数。
- Gemini for Codex：调用 legacy Gemini CLI（`gemini`）提供 Gemini-only 只读审阅、规划、救援诊断、结构化审阅输出和 Gemini CLI 原生 session 能力探测。
- Antigravity for Codex：调用本地 Google Antigravity CLI（`agy`）提供成熟的插件托管审阅工作流：只读审阅、对抗性审阅、规划、救援诊断、多角色审阅、结构化报告、角色包、后台任务、mailbox/leases、生命周期 hooks、GitHub Actions 工作流渲染、release checks、可选真实 smoke 和可选 Stop Hook 门禁，并显式选择 Gemini 或 Claude 模型 provider。

## Install

Remote install from GitHub:

```bash
codex plugin marketplace add yilibinbin/external-models-for-codex --ref claude-for-codex-v0.15.0
codex plugin add claude-for-codex@external-models-for-codex

codex plugin marketplace add yilibinbin/external-models-for-codex --ref gemini-for-codex-v0.11.2
codex plugin add gemini-for-codex@external-models-for-codex

codex plugin marketplace add yilibinbin/external-models-for-codex --ref antigravity-for-codex-v0.5.4
codex plugin add antigravity-for-codex@external-models-for-codex
```

Use the provider-specific immutable release ref for the plugin you want to install. Use `main` only for development snapshots.

Upgrade an existing install:

```bash
codex plugin marketplace upgrade external-models-for-codex
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@external-models-for-codex
codex plugin remove gemini-for-codex
codex plugin add gemini-for-codex@external-models-for-codex
codex plugin remove antigravity-for-codex
codex plugin add antigravity-for-codex@external-models-for-codex
```

Local development install from this repository:

```bash
codex plugin marketplace add .
codex plugin add claude-for-codex@external-models-for-codex
codex plugin add gemini-for-codex@external-models-for-codex
codex plugin add antigravity-for-codex@external-models-for-codex
```

## Requirements

- Codex CLI with plugin support
- Claude Code CLI available as `claude`
- Optional `@anthropic-ai/claude-agent-sdk` package for Claude `--backend sdk --agent-team sdk-subagents` native review mode
- Gemini CLI available as `gemini` for Gemini for Codex
- Google Antigravity CLI available as `agy`, `AGY_CLI_PATH`, or `ANTIGRAVITY_CLI_PATH` for Antigravity for Codex
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
- `gemini-review`, `gemini-adversarial-review`, `gemini-plan`, `gemini-multi-review`, `gemini-rescue`: Gemini CLI-backed equivalents that stay read-only. `gemini-review --structured` validates schema-backed findings, `gemini-multi-review` runs parallel role fan-out, and Gemini CLI-only native agent/session flags are capability-gated from the installed CLI.
- `gemini-mailbox`, `gemini-leases`: inspect sanitized Gemini coordination summaries and advisory path-attention leases.
- `antigravity-review`, `antigravity-adversarial-review`, `antigravity-plan`, `antigravity-multi-review`, `antigravity-rescue`, `antigravity-review-gate`, `antigravity-github-actions-review`: Antigravity-backed mature plugin-managed review, planning, rescue, Stop gate, and workflow-risk review with explicit Gemini or Claude model-provider selection. It uses `agy` only, does not claim Claude SDK, Gemini native-agent, or ultrareview parity, and keeps Claude-through-Antigravity separate from `claude-for-codex`.

These are skills-and-hook plugins, not MCP/app tool plugins. It is expected that `tool_search` will not expose callable `claude-for-codex`, `gemini-for-codex`, or `antigravity-for-codex` tools. Codex should route through the provider skills such as `claude-for-codex:*`, `gemini-for-codex:*`, and `antigravity-for-codex:*`.

Claude for Codex supports `--quality auto|fast|standard|strong|max`. The policy uses Claude Code aliases (`sonnet`, `opus`) plus valid effort values (`low`, `medium`, `high`, `xhigh`, `max`) instead of concrete model ids, so future Claude Code alias updates do not require a plugin change. Explicit `--model` and `--effort` override quality. `ultracode` is not emitted as an effort value, and `claude-ultrareview` remains explicit-cost only.

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
- [Antigravity Plugin README](plugins/antigravity-for-codex/README.md)
- [Changelog](plugins/claude-for-codex/CHANGELOG.md)
- [Antigravity Changelog](plugins/antigravity-for-codex/CHANGELOG.md)

## Safety Model

Review workflows invoke Claude with read-only permissions. Codex remains responsible for applying or rejecting Claude findings. CLI mode remains the default; `--backend sdk` is explicit, and native SDK subagent teams additionally require `--agent-team sdk-subagents`. SDK mode resolves `@anthropic-ai/claude-agent-sdk` with `@anthropic-ai/claude-code` as a compatibility fallback. Ultrareview may use remote/cloud execution and usage-credit billing, so it requires `--confirm-cost` or `CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1`. The Stop gate blocks only when Claude explicitly returns `BLOCK:`; Claude runtime failures, authentication failures, rate limits, invalid output, or timeouts fail open with warnings.

Gemini workflows are Gemini CLI-only. Gemini for Codex uses the legacy `gemini` CLI with bounded inline git context and keeps Antigravity out of the Gemini plugin so Claude workflows remain owned by Claude for Codex or explicit Antigravity model-provider selection.

Antigravity workflows use `agy --model --print-timeout --prompt` and reject GPT/OpenAI model labels. `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=gemini` is the default; `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude` is explicit and still runs through Antigravity, not through `claude-for-codex`. The Stop gate blocks only when Antigravity's first line is `BLOCK:`; runtime failures, invalid output, or timeouts fail open with warnings. `real-smoke` is opt-in, and generated CI workflows require an authenticated `agy` command in the runner environment.

Gemini for Codex also includes lifecycle hooks for same-session cleanup and unread-result reminders, a noninteractive `recommend-execution-mode` helper, and optional Gemini CLI-native session flags such as `--resume`, `--session-id`, and `--worktree` when the local Gemini CLI reports support.
