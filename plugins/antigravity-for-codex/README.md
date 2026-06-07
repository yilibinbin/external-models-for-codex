# Antigravity for Codex

Codex plugin that invokes the local Antigravity CLI (`agy`) for independent read-only review, planning, adversarial critique, rescue diagnosis, multi-role review, and an opt-in Stop hook gate.

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
