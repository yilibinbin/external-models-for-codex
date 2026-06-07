---
name: antigravity-github-actions-review
description: Use Antigravity CLI from Codex to review GitHub Actions workflow changes and PR-review automation risk.
---

# Antigravity GitHub Actions Review

Use this skill when the user asks Antigravity to inspect GitHub Actions workflow changes, review automation, or fork-safety concerns.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" review "$ARGUMENTS"
```

Recommended focus:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" review "GitHub Actions workflow safety, fork PR behavior, secret exposure, permissions, and immutable plugin refs. $ARGUMENTS"
```

Rules:
- This skill is read-only.
- Do not use `pull_request_target` unless the user explicitly asks to analyze that risk.
- Check workflow permissions, secret access, fork PR behavior, pinned refs, and install commands.
- Treat findings as review input for Codex to reconcile.
