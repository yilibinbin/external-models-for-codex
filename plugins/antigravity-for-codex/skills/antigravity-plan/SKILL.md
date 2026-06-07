---
name: antigravity-plan
description: Ask Antigravity CLI from Codex for an independent implementation plan before Codex edits.
---

# Antigravity Plan

Use this skill when Codex should compare its approach with an independent Antigravity plan before implementation.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" plan "$ARGUMENTS"
```

Rules:
- Do not let Antigravity edit files.
- Use the plan as advisory input; Codex owns the final implementation plan and edits.
- Ask for risks, validation commands, and rollback notes when the task is high impact.

Examples:
- `plan a safe database migration`
- `--model-provider claude plan a minimal compatibility patch`
