---
name: antigravity-review
description: Use Antigravity CLI from Codex for a read-only single review of local git changes or a focused diff; strict-only review stays in this single-review skill unless the user asks for multiple roles or perspectives.
---

# Antigravity Review

Use this skill when Codex needs an independent Antigravity review before shipping.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" review "$ARGUMENTS"
```

## Natural-Language Model Routing

Codex should let the user ask for Antigravity in normal language. Do not ask the user to write `--model-provider` or `--model` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:
- Default to the Gemini provider by omitting provider overrides and relying on the runtime default when no provider is explicit.
- Use the Claude provider only when the user explicitly asks for Claude through Antigravity, for example "use Antigravity's Claude model" or "Claude via Antigravity"; pass explicit `--model-provider claude` argv tokens or set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude`.
- Provider selection is explicit: strict, deep, high-confidence, advanced, or multi-agent language does not switch providers. Keep Gemini unless Claude is explicit.
- If the user names a concrete Gemini model, keep or choose the Gemini provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- If the user names a concrete Claude/Sonnet/Opus model through Antigravity, choose the Claude provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- Do not concatenate provider/model flags into quoted `$ARGUMENTS`; quoted `$ARGUMENTS` is only for natural-language focus text.
- Reject GPT/OpenAI model requests as unsupported for this plugin instead of mapping them to Antigravity.

Internal invocation examples, not for users:
- Direct argv path: `node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" review --model-provider claude "$ARGUMENTS"`.
- Environment path: set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude` or `ANTIGRAVITY_FOR_CODEX_MODEL=<model>` before the existing `node ... "$ARGUMENTS"` command.
- Provider/model are added outside quoted `$ARGUMENTS`; `$ARGUMENTS` carries only natural-language focus text.

<!--
routing:strict-only-single-review
routing:multi-agent-to-multi-review
-->

Rules:
- Treat the output as review findings, not implementation instructions.
- Do not fix findings in the same turn unless the user explicitly asks.
- Preserve Antigravity's file paths, line numbers, uncertainty markers, and residual-risk notes.
- Preserve evidence boundaries; if Antigravity marks a claim as inference or uncertainty, keep that distinction.
- If Antigravity fails, returns malformed structured output, or reports setup/auth problems, report that failure instead of replacing it with Codex guesses.

User-facing examples:
- "Use Antigravity to review the current changes with Gemini."
- "Use Antigravity to run a strict release-risk review."
- "Use Antigravity's Claude model to challenge this API design."

Internal routing procedure:
- Classify the request as a normal read-only review.
- If the user asks for "strict review" without multiple perspectives, named roles, or multi-agent fan-out, keep this as a normal review with stricter focus.
- If the user asks for multi-agent review, multiple perspectives, named roles, or role fan-out, use antigravity-multi-review instead of this skill.
- Select the provider from explicit model intent: Gemini by default, Claude only when requested through Antigravity.
- Preserve the user's review focus as natural-language focus text.
- Add model selection only as explicit argv tokens or environment variables when the user names a concrete model label.
