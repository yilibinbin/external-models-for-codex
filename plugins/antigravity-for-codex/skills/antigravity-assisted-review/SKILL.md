---
name: antigravity-assisted-review
description: Run a bounded Antigravity scorecard review loop that helps Codex decide whether current changes need more work.
---

# Antigravity Assisted Review

Use this skill after implementation when Codex needs a bounded Antigravity quality loop over the current git changes.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" assisted-review "$ARGUMENTS"
```

Optional taskset input:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" assisted-review --taskset <taskset-id> "$ARGUMENTS"
```

## Natural-Language Model Routing

Codex should let the user ask for Antigravity assisted review in normal language. Do not ask the user to write `--model-provider` or `--model` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:
- Default to the Gemini provider by omitting provider overrides and relying on the runtime default when no provider is explicit.
- Use the Claude provider only when the user explicitly asks for Claude through Antigravity, for example "use Antigravity's Claude model" or "Claude via Antigravity"; pass explicit `--model-provider claude` argv tokens or set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude`.
- Provider selection is explicit: strict, deep, high-confidence, advanced, or multi-agent language does not switch providers. Keep Gemini unless Claude is explicit.
- If the user names a concrete Gemini model, keep or choose the Gemini provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- If the user names a concrete Claude/Sonnet/Opus model through Antigravity, choose the Claude provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- Do not concatenate provider/model flags into quoted `$ARGUMENTS`; quoted `$ARGUMENTS` is only for natural-language focus text.
- Reject GPT/OpenAI model requests as unsupported for this plugin instead of mapping them to Antigravity.

Internal invocation examples, not for users:
- Direct argv path: `node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" assisted-review --model-provider claude "$ARGUMENTS"`.
- Environment path: set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude` or `ANTIGRAVITY_FOR_CODEX_MODEL=<model>` before the existing `node ... "$ARGUMENTS"` command.
- Provider/model are added outside quoted `$ARGUMENTS`; `$ARGUMENTS` carries only natural-language focus text.

<!--
routing:assisted-review
-->

Rules:
- Assisted review is read-only and advisory. It never edits, commits, pushes, opens PRs, or closes issues.
- The loop uses scorecard review rounds and stops when the threshold is met, no improvement is detected, a repeated blocker appears, provider failure is classified, or `--max-review-rounds` is reached.
- Keep `--max-review-rounds` small; valid values are 1 through 3.
- Treat `needs_attention` as a signal for Codex to inspect and decide next fixes, not as an automatic blocker.
- If Antigravity fails, times out, or returns invalid scorecard JSON, report the failure explicitly.

User-facing examples:
- "Use Antigravity to run an assisted quality loop after these fixes."
- "Use Antigravity for a bounded scorecard review until it passes or finds blockers."
- "Use Antigravity's Claude model for one assisted-review pass."

Internal routing procedure:
- Classify the request as assisted review when the user asks for a quality loop, iterative scorecard review, or bounded review-until-clear workflow after implementation.
- Select the provider from explicit model intent: Gemini by default, Claude only when requested through Antigravity.
- Preserve the user's review focus as natural-language focus text.
- Add `--taskset <id>` only when the user or prior command provided a concrete taskset id.
- Add model selection only as explicit argv tokens or environment variables when the user names a concrete model label.
