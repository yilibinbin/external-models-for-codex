---
name: claude-adversarial-review
description: Use Claude Code to challenge Codex's implementation approach, assumptions, tradeoffs, and failure modes.
---

# Claude Adversarial Review

Use this skill for high-risk changes, architecture decisions, reliability-sensitive code, security-sensitive code, migrations, rollback-sensitive changes, or when Codex may be overconfident.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" adversarial-review "$ARGUMENTS"
```

Rules:
- This is read-only.
- Ask Claude to challenge the direction, not just inspect code style.
- Preserve all findings exactly enough that the user can decide whether to act.
- Do not apply fixes until the user chooses which findings to adopt.

Useful focus examples:
- `--base main challenge the retry and rollback design`
- `look for race conditions and hidden data-loss paths`
- `question whether this abstraction is simpler than the existing pattern`
