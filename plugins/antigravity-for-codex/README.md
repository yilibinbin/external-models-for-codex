# Antigravity for Codex

Version: 0.6.0

Codex plugin that invokes the local Antigravity CLI (`agy`) for independent read-only review, planning, adversarial critique, rescue diagnosis, multi-role review, structured reports, background jobs, advisory coordination, GitHub Actions workflow rendering, release checks, and an opt-in Stop hook gate.

Antigravity for Codex has operational maturity for plugin-managed workflows: bounded CLI invocation, explicit model selection, repo-external state, sanitized reports, lifecycle hooks, release checks, and `real-smoke` is opt-in. It does not claim Claude SDK, Gemini native-agent, or ultrareview parity. Claude-through-Antigravity is an explicit Antigravity model-provider choice and remains separate from `claude-for-codex`.

## Agy-Native Maturity Boundary

This plugin absorbs mature workflow patterns from Claude for Codex only when they make sense for Antigravity:

- `agy` remains the only model execution command.
- Gemini remains the default provider.
- Claude-through-Antigravity is explicit and stays separate from Claude for Codex.
- Fable, Claude SDK subagents, Claude ultrareview, Claude Code `--fallback-model`, and Claude Code permission brokering are not Antigravity features.
- `doctor` is cheap by default and does not call a model.
- `real-smoke` is still opt-in because it requires an authenticated `agy` account and live provider quota.

## Requirements

- Codex with plugin support
- Antigravity CLI available as `agy`, `AGY_CLI_PATH`, or `ANTIGRAVITY_CLI_PATH`
- Node.js 20 or newer
- Git repository for review context collection

## Model Selection

Users can ask for Antigravity in natural language. Codex should map the request to internal Antigravity arguments; users do not need to write `--model-provider` or `--model`.

Default behavior:

- Use the Gemini provider.
- Use `Gemini 3.1 Pro (High)` unless `ANTIGRAVITY_FOR_CODEX_MODEL` or `ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL` overrides it.
- Treat "strict", "deep", "advanced", "high-confidence", and "multi-agent" as review strength, not a request to switch providers.

Explicit Claude-through-Antigravity behavior:

- Use the Claude provider only when the user clearly asks for Claude through Antigravity.
- Use `Claude Sonnet 4.6 (Thinking)` unless `ANTIGRAVITY_FOR_CODEX_MODEL` or `ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL` overrides it.
- Keep Claude-through-Antigravity separate from `claude-for-codex`; this plugin still calls `agy`, not Claude Code.

Examples users can say:

- "Use Antigravity to review the current changes."
- "Use Antigravity for a strict multi-role release review."
- "Use Antigravity's Claude model to challenge this plan."

Advanced environment defaults:

```bash
ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=gemini
ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL="Gemini 3.1 Pro (High)"
ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL="Claude Sonnet 4.6 (Thinking)"
```

Workflow generation note:

- `github-actions init` persists the selected provider into the generated workflow. Use `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER` for workflow generation only when you intend that provider to be committed for the team. Provider-specific model defaults remain runtime-owned unless `ANTIGRAVITY_FOR_CODEX_MODEL` is explicitly set.

The plugin rejects GPT/OpenAI model labels and never passes `--dangerously-skip-permissions`.

## Commands

- `setup`
- `capabilities`
- `review`
- `adversarial-review`
- `multi-review`
- `plan`
- `rescue`
- `review-gate`
- `real-smoke`
- `release-check`
- `report`
- `roles`
- `jobs`
- `status`
- `result`
- `cancel`
- `reserve-job`
- `run-reserved-job`
- `mailbox`
- `leases`
- `github-actions`

## Smoke And CI

`real-smoke` is opt-in:

```bash
ANTIGRAVITY_FOR_CODEX_REAL_SMOKE=1 node plugins/antigravity-for-codex/scripts/antigravity-companion.mjs real-smoke --quick
```

GitHub Actions rendering and validation are available through `github-actions render|init|validate`. CI runs require an authenticated `agy` command in the runner environment; release checks validate the generated workflow offline but do not authenticate Antigravity.
