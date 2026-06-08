---
name: antigravity-adversarial-review
description: Use Antigravity CLI from Codex to challenge assumptions, rollback paths, and hidden failure modes.
---

# Antigravity Adversarial Review

Use this skill when Codex needs a skeptical second pass over an implementation or plan.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" adversarial-review "$ARGUMENTS"
```

## Natural-Language Model Routing

Codex should let the user ask for Antigravity adversarial review in normal language. Do not ask the user to write `--model-provider` or `--model` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:
- Default to the Gemini provider by omitting provider overrides and relying on the runtime default when no provider is explicit.
- Use the Claude provider only when the user explicitly asks for Claude through Antigravity, for example "use Antigravity's Claude model" or "Claude via Antigravity"; pass explicit `--model-provider claude` argv tokens or set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude`.
- Provider selection is explicit: strict, deep, high-confidence, advanced, or multi-agent language does not switch providers. Keep Gemini unless Claude is explicit.
- If the user names a concrete Gemini model, keep or choose the Gemini provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- If the user names a concrete Claude/Sonnet/Opus model through Antigravity, choose the Claude provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- Do not concatenate provider/model flags into quoted `$ARGUMENTS`; quoted `$ARGUMENTS` is only for natural-language focus text.
- Reject GPT/OpenAI model requests as unsupported for this plugin instead of mapping them to Antigravity.

Internal invocation examples, not for users:
- Direct argv path: `node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" adversarial-review --model-provider claude "$ARGUMENTS"`.
- Environment path: set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude` or `ANTIGRAVITY_FOR_CODEX_MODEL=<model>` before the existing `node ... "$ARGUMENTS"` command.
- Provider/model are added outside quoted `$ARGUMENTS`; `$ARGUMENTS` carries only natural-language focus text.

<!--
routing:adversarial-review
-->

Rules:
- Keep the review read-only.
- Ask for concrete failure modes, assumptions, simpler alternatives, and rollback concerns.
- Treat the response as critique for Codex to reconcile, not as an instruction to edit files.
- Preserve any file paths, line references, uncertainty markers, and residual-risk notes in the final report.

User-facing examples:
- "Use Antigravity to challenge this migration plan."
- "Use Antigravity for a strict adversarial release review."
- "Use Antigravity's Claude model to look for hidden security and rollback risks."

Internal routing procedure:
- Classify the request as adversarial review when the user asks to challenge assumptions, rollback paths, hidden risks, simpler alternatives, or failure modes.
- Select the provider from explicit model intent: Gemini by default, Claude only when requested through Antigravity.
- Preserve the skeptical focus as natural-language focus text.
- Add model selection only as explicit argv tokens or environment variables when the user names a concrete model label.
