---
name: antigravity-role-packs
description: List and use Antigravity for Codex role packs for focused multi-review teams.
---

# Antigravity Role Packs

Use this skill when the user asks which Antigravity reviewer teams are available or wants a focused multi-review.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" roles --json
```

## Natural-Language Model Routing

Codex should let the user ask for Antigravity role-pack review in normal language. Do not ask the user to write `--model-provider` or `--model` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:
- Default to the Gemini provider by omitting provider overrides and relying on the runtime default when no provider is explicit.
- Use the Claude provider only when the user explicitly asks for Claude through Antigravity, for example "use Antigravity's Claude model" or "Claude via Antigravity"; pass explicit `--model-provider claude` argv tokens or set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude`.
- Provider selection is explicit: strict, deep, high-confidence, advanced, coordinated, role-pack, or multi-agent language does not switch providers. Keep Gemini unless Claude is explicit.
- If the user names a concrete Gemini model, keep or choose the Gemini provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- If the user names a concrete Claude/Sonnet/Opus model through Antigravity, choose the Claude provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- Do not concatenate provider/model flags into quoted `$ARGUMENTS`; quoted `$ARGUMENTS` is only for natural-language focus text.
- Reject GPT/OpenAI model requests as unsupported for this plugin instead of mapping them to Antigravity.

Internal invocation examples, not for users:
- List packs: `node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" roles --json`.
- Direct argv path: `node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" multi-review --model-provider claude --role-pack release "$ARGUMENTS"`.
- Role syntax: `multi-review --role-pack release "$ARGUMENTS"` or `multi-review --roles correctness,security "$ARGUMENTS"`.
- Environment path: set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude` or `ANTIGRAVITY_FOR_CODEX_MODEL=<model>` before the existing `multi-review ... "$ARGUMENTS"` command.
- Provider/model are added outside quoted `$ARGUMENTS`; `$ARGUMENTS` carries only natural-language focus text.

<!--
routing:role-packs
routing:multi-review-roles-syntax
-->

Rules:
- Use `roles --json` when the user asks what teams or role packs are available.
- Use `multi-review --role-pack <pack>` only when the user asks for a focused team or named review pack.
- Keep the review read-only and let Codex reconcile findings before edits.

User-facing examples:
- "Use Antigravity's release role pack to review this PR."
- "Use Antigravity to list available reviewer teams."
- "Use Antigravity's Claude model for a security role-pack review."

Internal routing procedure:
- Classify the request as role-pack review when the user asks for a named review team, role pack, release team, security team, or available reviewer teams.
- Select the provider from explicit model intent: Gemini by default, Claude only when requested through Antigravity.
- Preserve the role-pack goal as natural-language focus text.
- Add model selection only as explicit argv tokens or environment variables when the user names a concrete model label.
