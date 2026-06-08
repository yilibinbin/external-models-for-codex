---
name: antigravity-review
description: Use Antigravity CLI from Codex for a read-only review of local git changes or a focused diff.
---

# Antigravity Review

Use this skill when Codex needs an independent Antigravity review before shipping.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" review "$ARGUMENTS"
```

Rules:
- Treat the output as review findings, not implementation instructions.
- Do not fix findings in the same turn unless the user explicitly asks.
- Preserve Antigravity's file paths, line numbers, uncertainty markers, and residual-risk notes.
- Use `--model-provider gemini` for the default Gemini model path.
- Use `--model-provider claude` only when the user explicitly wants a Claude model through Antigravity.

Examples:
- `--model-provider gemini check release risk`
- `--model-provider claude challenge the API design`
