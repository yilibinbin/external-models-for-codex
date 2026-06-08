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

## Natural-Language Model Routing

Codex should let the user ask for Antigravity planning in normal language. Do not ask the user to write `--model-provider` or `--model` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:
- Default to the Gemini provider by omitting provider overrides and relying on the runtime default when no provider is explicit.
- Use the Claude provider only when the user explicitly asks for Claude through Antigravity, for example "use Antigravity's Claude model" or "Claude via Antigravity"; pass explicit `--model-provider claude` argv tokens or set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude`.
- Provider selection is explicit: strict, deep, high-confidence, advanced, or multi-agent language does not switch providers. Keep Gemini unless Claude is explicit.
- If the user names a concrete Gemini model, keep or choose the Gemini provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- If the user names a concrete Claude/Sonnet/Opus model through Antigravity, choose the Claude provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- Do not concatenate provider/model flags into quoted `$ARGUMENTS`; quoted `$ARGUMENTS` is only for natural-language focus text.
- Reject GPT/OpenAI model requests as unsupported for this plugin instead of mapping them to Antigravity.

Internal invocation examples, not for users:
- Direct argv path: `node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" plan --model-provider claude "$ARGUMENTS"`.
- Environment path: set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude` or `ANTIGRAVITY_FOR_CODEX_MODEL=<model>` before the existing `node ... "$ARGUMENTS"` command.
- Provider/model are added outside quoted `$ARGUMENTS`; `$ARGUMENTS` carries only natural-language focus text.

<!--
routing:plan
-->

Rules:
- Do not let Antigravity edit files.
- Use the plan as advisory input; Codex owns the final implementation plan and edits.
- Ask for risks, validation commands, and rollback notes when the task is high impact.
- Preserve assumptions and uncertainty instead of converting Antigravity's plan into stronger claims.

User-facing examples:
- "Use Antigravity to plan a safe database migration."
- "Use Antigravity for a strict implementation plan before Codex edits."
- "Use Antigravity's Claude model to plan a minimal compatibility patch."

Internal routing procedure:
- Classify the request as planning when the user asks for implementation sequencing, risk analysis, validation commands, rollback notes, or a second plan before Codex edits.
- Select the provider from explicit model intent: Gemini by default, Claude only when requested through Antigravity.
- Preserve the planning goal as natural-language focus text.
- Add model selection only as explicit argv tokens or environment variables when the user names a concrete model label.
