---
name: antigravity-rescue
description: Ask Antigravity CLI from Codex for read-only diagnosis when an implementation or validation run is stuck.
---

# Antigravity Rescue

Use this skill when Codex is blocked by failing tests, confusing runtime output, or an implementation dead end.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" rescue "$ARGUMENTS"
```

## Natural-Language Model Routing

Codex should let the user ask for Antigravity rescue in normal language. Do not ask the user to write `--model-provider` or `--model` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:
- Default to the Gemini provider by omitting provider overrides and relying on the runtime default when no provider is explicit.
- Use the Claude provider only when the user explicitly asks for Claude through Antigravity, for example "use Antigravity's Claude model" or "Claude via Antigravity"; pass explicit `--model-provider claude` argv tokens or set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude`.
- Provider selection is explicit: strict, deep, high-confidence, advanced, or multi-agent language does not switch providers. Keep Gemini unless Claude is explicit.
- If the user names a concrete Gemini model, keep or choose the Gemini provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- If the user names a concrete Claude/Sonnet/Opus model through Antigravity, choose the Claude provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- Do not concatenate provider/model flags into quoted `$ARGUMENTS`; quoted `$ARGUMENTS` is only for natural-language focus text.
- Reject GPT/OpenAI model requests as unsupported for this plugin instead of mapping them to Antigravity.

Internal invocation examples, not for users:
- Direct argv path: `node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" rescue --model-provider claude "$ARGUMENTS"`.
- Environment path: set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude` or `ANTIGRAVITY_FOR_CODEX_MODEL=<model>` before the existing `node ... "$ARGUMENTS"` command.
- Provider/model are added outside quoted `$ARGUMENTS`; `$ARGUMENTS` carries only natural-language focus text.

<!--
routing:rescue
-->

Rules:
- Keep the rescue pass read-only.
- Provide the observed error, commands already tried, and current git context.
- Treat the response as diagnosis and possible next steps; Codex decides what to change.
- Preserve uncertainty, file references, failed-command evidence, and residual-risk notes.

User-facing examples:
- "Use Antigravity to diagnose why pytest fails after the hook change."
- "Use Antigravity for a strict rescue pass on this release-check failure."
- "Use Antigravity's Claude model to diagnose this compatibility failure."

Internal routing procedure:
- Classify the request as rescue when Codex is blocked by failing tests, confusing runtime output, repeated validation failures, or an implementation dead end.
- Select the provider from explicit model intent: Gemini by default, Claude only when requested through Antigravity.
- Preserve the observed error, attempted commands, and current hypothesis as natural-language focus text.
- Add model selection only as explicit argv tokens or environment variables when the user names a concrete model label.
