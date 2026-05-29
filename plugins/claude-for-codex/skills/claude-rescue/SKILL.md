---
name: claude-rescue
description: Ask Claude Code for rescue diagnosis or explicit write-mode repair when Codex is stuck, failing tests, or uncertain about recovery.
---

# Claude Rescue

Use this skill when Codex needs an independent Claude diagnosis for a stuck implementation, failing validation, confusing git state, or unclear next recovery step.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" rescue "$ARGUMENTS"
```

Rules:
- Default mode is read-only.
- Claude must diagnose and propose recovery steps, not edit files, unless `--write` is explicitly present.
- Codex remains responsible for applying any fixes after reviewing the diagnosis.
- Use `--write` only when the user explicitly asks Claude to modify files.
- In write mode, report Claude's output and inspect the resulting git diff before claiming the task is fixed.

Arguments:
- `--base <ref>` includes branch diff context when available.
- `--scope auto|working-tree|branch` controls git context selection.
- `--path <path>` or `--paths <path>` filters git context.
- `--model <model>` and `--effort <level>` are passed to Claude CLI.
- `--write` allows Claude Code write permissions after recording git working-tree fingerprints.
- `--background` starts a tracked job and returns a job id.
- `--wait` waits for a background job to finish before returning.
