---
name: claude-assisted-review
description: Run a bounded advisory Claude scorecard review loop; Codex remains responsible for judging and applying fixes.
---

# Claude Assisted Review

Use this skill only when the user explicitly asks Claude to run a bounded assisted review or quality-feedback loop. Claude stays read-only; Codex or the user applies fixes between review passes.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" assisted-review --scorecard --max-review-rounds 2 "$ARGUMENTS"
```

## Natural-Language Claude Routing

<!--
routing:assisted-review
routing:bounded-quality-loop
-->

- Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.
- Keep Codex responsible for reconciling Claude output before edits.
- Use this skill for explicit assisted-review, quality-feedback-loop, or bounded Claude review iteration requests.
- Do not use this skill for ordinary one-pass review; use `claude-review` or `claude-multi-review` instead.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.

User-facing examples:
- "Run Claude assisted review until the scorecard is acceptable."
- "Use a bounded Claude quality feedback loop."
- "Have Claude produce scorecard feedback for the remaining fixes."

Internal routing procedure:
- Invoke `assisted-review` only when the user asks for a review loop or assisted quality gate.
- Keep `--scorecard` enabled; it is the basis for stopping decisions.
- Use `--max-review-rounds 2` by default; accept user-requested values only within the runtime limit.
- Pass `--validation-log`, `--test-summary`, or `--ci-summary` only for files Codex/user already produced.
- Do not run project commands, edit files, commit, push, create pull requests, merge, close issues, or request ultrareview.
- Preserve the returned `loopId`, `stopReason`, score, threshold, and blocking-finding count in the final report.

Rules:
- Claude output is advisory and not self-executing.
- Codex must judge every finding against local evidence.
- If the runtime reports `capacity_blocked`, do not start extra Claude processes; retry later or lower concurrency.
- If scorecard output is malformed, report the structured-output failure instead of replacing it with Codex guesses.
