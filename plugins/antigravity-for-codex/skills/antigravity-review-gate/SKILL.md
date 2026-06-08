---
name: antigravity-review-gate
description: Configure and manually run the opt-in Antigravity Stop review gate for Antigravity for Codex.
---

# Antigravity Review Gate

Use this skill when the user wants Antigravity to run a Stop-time gate before Codex finishes a turn.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" review-gate "$ARGUMENTS"
```

Enable the gate in the environment that launches Codex hooks:

```bash
export ANTIGRAVITY_FOR_CODEX_REVIEW_GATE=on
```

Disable or bypass the gate:

```bash
export ANTIGRAVITY_FOR_CODEX_REVIEW_GATE=off
```

## Natural-Language Model Routing

Codex should let the user ask for manual Antigravity review-gate checks in normal language. Do not ask the user to write `--model-provider` or `--model` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:
- Default to the Gemini provider by omitting provider overrides and relying on the runtime default when no provider is explicit.
- Use the Claude provider only when the user explicitly asks for Claude through Antigravity, for example "use Antigravity's Claude model" or "Claude via Antigravity"; pass explicit `--model-provider claude` argv tokens or set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude`.
- Provider selection is explicit: gate, strict gate, Stop hook, or blocking-review language does not switch providers. Keep Gemini unless Claude is explicit.
- If the user names a concrete Gemini model, keep or choose the Gemini provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- If the user names a concrete Claude/Sonnet/Opus model through Antigravity, choose the Claude provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- Do not concatenate provider/model flags into quoted `$ARGUMENTS`; quoted `$ARGUMENTS` is only for natural-language focus text.
- Reject GPT/OpenAI model requests as unsupported for this plugin instead of mapping them to Antigravity.

Internal invocation examples, not for users:
- Manual direct argv path: `node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" review-gate --model-provider claude "$ARGUMENTS"`.
- Environment path: set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude` or `ANTIGRAVITY_FOR_CODEX_MODEL=<model>` before the existing `review-gate "$ARGUMENTS"` command.
- Hook enable/disable remains environment-based: `ANTIGRAVITY_FOR_CODEX_REVIEW_GATE=on|off`.
- Provider/model are added outside quoted `$ARGUMENTS`; `$ARGUMENTS` carries only natural-language focus text.

<!--
routing:review-gate
-->

Rules:
- The hook is opt-in. Installing the plugin does not enable blocking behavior.
- Empty, `off`, `false`, and `0` values disable the runtime gate.
- Only a first output line beginning `BLOCK:` blocks Stop.
- `ALLOW:` exits successfully with no hook decision JSON.
- Antigravity runtime failures, timeouts, invalid output, or wrapper errors warn and fail open.
- Do not add a `hooks` field to `.codex-plugin/plugin.json`; `hooks/hooks.json` is auto-discovered by the plugin runtime.

User-facing examples:
- "Use Antigravity to run a manual review gate for these changes."
- "Enable the Antigravity Stop gate."
- "Use Antigravity's Claude model for a manual gate check."

Internal routing procedure:
- Classify the request as review-gate when the user asks for Stop gate setup, manual gate runs, fail-open blocking policy, or hook gate troubleshooting.
- Select the provider from explicit model intent for manual `review-gate` runs: Gemini by default, Claude only when requested through Antigravity.
- Keep hook enable/disable separate from provider/model selection; gate activation uses `ANTIGRAVITY_FOR_CODEX_REVIEW_GATE`, while model routing uses explicit argv or `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER`.
- Add model selection only as explicit argv tokens or environment variables when the user names a concrete model label.
