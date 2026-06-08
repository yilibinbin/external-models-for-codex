---
name: gemini-github-actions-review
description: Generate, initialize, validate, and consume fork-safe GitHub Actions PR review workflows for Gemini for Codex.
---

# Gemini GitHub Actions Review

Use this skill when the user asks to add, inspect, or validate a GitHub Actions workflow that runs Gemini for Codex PR review.

## Render

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" github-actions render
```

## Write

Only write when the user explicitly asks to create or initialize the workflow:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" github-actions init --write
```

Use `--force` only when the user explicitly wants to overwrite an existing workflow.

## Validate

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" github-actions validate
```

## Rules

- Do not use `pull_request_target`.
- Fork PRs skip Gemini execution by default.
- Context providers are off by default in CI; do not use `auto`.
- Checks annotations are optional and add `checks: write`.
- The workflow pins an immutable `gemini-for-codex-v0.11.2` ref by default.
