# Antigravity for Codex

Version: 0.5.2

Codex plugin that invokes the local Antigravity CLI (`agy`) for independent read-only review, planning, adversarial critique, rescue diagnosis, multi-role review, structured reports, background jobs, advisory coordination, GitHub Actions workflow rendering, release checks, and an opt-in Stop hook gate.

Antigravity for Codex has operational maturity for plugin-managed workflows: bounded CLI invocation, explicit model selection, repo-external state, sanitized reports, lifecycle hooks, release checks, and `real-smoke` is opt-in. It does not claim Claude SDK, Gemini native-agent, or ultrareview parity. Claude-through-Antigravity is an explicit Antigravity model-provider choice and remains separate from `claude-for-codex`.

## Requirements

- Codex with plugin support
- Antigravity CLI available as `agy`, `AGY_CLI_PATH`, or `ANTIGRAVITY_CLI_PATH`
- Node.js 20 or newer
- Git repository for review context collection

## Model Selection

Default:

```bash
ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=gemini
ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL="Gemini 3.1 Pro (High)"
```

Claude through Antigravity is explicit:

```bash
ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude
ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL="Claude Sonnet 4.6 (Thinking)"
```

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
