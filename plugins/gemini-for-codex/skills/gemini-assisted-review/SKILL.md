---
name: gemini-assisted-review
description: Run a bounded Gemini scorecard review loop that helps Codex decide whether current changes need more work.
---

# Gemini Assisted Review

Use this skill after implementation when Codex needs a bounded Gemini quality loop over the current git changes.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" assisted-review "$ARGUMENTS"
```

Optional taskset input:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" assisted-review --taskset <taskset-id> "$ARGUMENTS"
```

Rules:
- Assisted review is read-only and advisory. It never edits, commits, pushes, opens PRs, or closes issues.
- The loop uses scorecard review rounds and stops when the threshold is met, no improvement is detected, a repeated blocker appears, provider failure is classified, or `--max-review-rounds` is reached.
- Keep `--max-review-rounds` small; valid values are 1 through 3.
- Treat `needs_attention` as a signal for Codex to inspect and decide next fixes, not as an automatic blocker.
- If Gemini fails, times out, or returns invalid scorecard JSON, report the failure explicitly.

Output usage:
- Report `status`, `stopReason`, `scoreTotal`, `blockingFindings`, and the suggested follow-up command.
- Use the stored round summaries as evidence when deciding whether to run another targeted review or fix findings.
