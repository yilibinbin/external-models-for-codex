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

## Natural-Language Claude Routing

<!--
routing:github-actions-review
routing:github-actions-init-explicit
routing:github-actions-quality-standard
-->

- Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.
- Use `github-actions render` for preview and `github-actions init --write` only when the user explicitly asks to create or update the workflow file.
- Default generated workflows to `--quality standard` even if the local environment requests stronger manual review.
- Persist concrete `--model` and `--effort` into the workflow only when the user explicitly asks for a concrete model or effort for CI.
- Keep generic strict or strong language as review focus unless the user explicitly asks to change the workflow's model or effort.
- Do not add `pull_request_target` unless the user explicitly asks to analyze that risk.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.

User-facing examples:
- "Preview the Claude GitHub Actions review workflow."
- "Create the fork-safe Claude PR review workflow."
- "Validate the existing Claude PR review workflow."

Internal routing procedure:
- Classify the user's intent first, then invoke the narrowest Claude for Codex command that satisfies it.
- Use render for preview, validate for checking an existing workflow, and init only when file creation or update is explicit.
- Translate explicit strength, model, effort, backend, role, or background-job requests into argv tokens outside quoted `$ARGUMENTS`.
- Keep Codex responsible for reading Claude output, judging whether findings are correct, and reconciling the final answer or implementation plan.

Rules:
- Default generated workflows use `pull_request`, not `pull_request_target`.
- Fork pull requests skip Claude invocation, comments, annotations, and secret use by default.
- Generated release workflows must pin an immutable release ref such as `claude-for-codex-v0.20.1`; do not default to mutable `main`.
- GitHub context expressions are mapped through `env:` first. Do not insert `${{ github.* }}` directly into shell `run:` blocks.
- Checks annotations are opt-in because they require `checks: write`.
- Treat this as a template/render/validate workflow. Local tests must use fixtures and must not call the live GitHub API.
