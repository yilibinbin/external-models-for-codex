---
name: claude-github-actions-review
description: Generate or validate a fork-safe GitHub Actions workflow that runs Claude for Codex PR review.
---

# Claude GitHub Actions Review

Use this skill when Codex should prepare a GitHub Actions PR review workflow for Claude for Codex.

Render without writing:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" github-actions render "$ARGUMENTS"
```

Initialize the workflow only when the user explicitly wants a file written:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" github-actions init --write "$ARGUMENTS"
```

Validate the generated or existing workflow:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" github-actions validate "$ARGUMENTS"
```

Rules:
- Default generated workflows use `pull_request`, not `pull_request_target`.
- Fork pull requests skip Claude invocation, comments, annotations, and secret use by default.
- Generated release workflows must pin an immutable release ref such as `claude-for-codex-v0.10.0`; do not default to mutable `main`.
- GitHub context expressions are mapped through `env:` first. Do not insert `${{ github.* }}` directly into shell `run:` blocks.
- Checks annotations are opt-in because they require `checks: write`.
- Treat this as a template/render/validate workflow. Local tests must use fixtures and must not call the live GitHub API.
