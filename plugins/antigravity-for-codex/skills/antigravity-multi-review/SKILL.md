---
name: antigravity-multi-review
description: Run plugin-managed Antigravity role fan-out review from Codex when the user asks for multiple perspectives, named roles, multi-agent or multi-role review, or high-risk multi-role review; do not use this skill for strict-only review.
---

# Antigravity Multi Review

Use this skill when Codex needs several independent Antigravity review passes with different role directives.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" multi-review "$ARGUMENTS"
```

## Natural-Language Model Routing

Codex should let the user ask for Antigravity multi-role review in normal language. Do not ask the user to write `--model-provider` or `--model` unless troubleshooting the plugin itself.

When converting the user's request to companion invocation:
- Default to the Gemini provider by omitting provider overrides and relying on the runtime default when no provider is explicit.
- Use the Claude provider only when the user explicitly asks for Claude through Antigravity, for example "use Antigravity's Claude model" or "Claude via Antigravity"; pass explicit `--model-provider claude` argv tokens or set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude`.
- Provider selection is explicit: strict, deep, high-confidence, advanced, or multi-agent language does not switch providers. Keep Gemini unless Claude is explicit.
- If the user names a concrete Gemini model, keep or choose the Gemini provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- If the user names a concrete Claude/Sonnet/Opus model through Antigravity, choose the Claude provider and pass the model as explicit `--model` argv tokens or `ANTIGRAVITY_FOR_CODEX_MODEL`.
- Do not concatenate provider/model flags into quoted `$ARGUMENTS`; quoted `$ARGUMENTS` is only for natural-language focus text.
- Reject GPT/OpenAI model requests as unsupported for this plugin instead of mapping them to Antigravity.

Internal invocation examples, not for users:
- Direct argv path: `node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" multi-review --model-provider claude "$ARGUMENTS"`.
- Requested roles path: `node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" multi-review --roles correctness,security "$ARGUMENTS"`.
- Managed background path: `node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" multi-review --background "$ARGUMENTS"`; return the job id and use the plugin job/result workflow instead of shell backgrounding.
- Environment path: set `ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=claude` or `ANTIGRAVITY_FOR_CODEX_MODEL=<model>` before the existing `node ... "$ARGUMENTS"` command.
- Provider/model are added outside quoted `$ARGUMENTS`; `$ARGUMENTS` carries only natural-language focus text.

<!--
routing:strict-only-to-single-review
routing:multi-review-fanout-background
routing:multi-review-roles-syntax
routing:multi-review-background-syntax
-->

Rules:
- This is read-only.
- Antigravity must not edit files or apply fixes.
- Treat each role output as review findings for Codex to reconcile.
- Codex remains responsible for deciding which findings to adopt, reject, or report as residual risk.
- Preserve role headers, file paths, line numbers, uncertainty markers, failed-role diagnostics, and the orchestration summary.
- Do not fix review findings in the same turn unless the user explicitly asks which findings to adopt.

Default roles:
- `correctness`: bugs, regressions, edge cases, and contract breaks.
- `security`: read-only safety, secrets exposure, injection risks, and unsafe command or path handling.
- `tests`: missing, brittle, or overfit tests and validation gaps.
- `release`: install, marketplace, versioning, documentation, and upgrade risks.
- `adversarial`: assumptions, simpler alternatives, hidden costs, and failure modes.

Fan-out sizing and background guidance:
- Use the requested roles when the user names review dimensions.
- Use the default role set for broad high-risk or release-sensitive multi-role review.
- For large diffs, slow providers, or more than three roles, prefer the plugin-managed background job path and return the job id instead of blocking the main Codex turn.
- Do not start ad hoc parallel shell commands outside the companion; use the companion's multi-review orchestration so role diagnostics and summaries stay structured.

User-facing examples:
- "Use Antigravity to run a multi-role review with Gemini."
- "Use Antigravity for strict multi-agent release and security review."
- "Use Antigravity's Claude model for a skeptical multi-role review."

Internal routing procedure:
- Classify the request as multi-role review when the user asks for multiple perspectives, multi-agent review, role fan-out, named review dimensions, or high-risk multi-role review.
- If the request is only "strict review" with no multi-role signal, use antigravity-review instead of this skill.
- Treat "strict" as review strength for the selected workflow; do not use "strict" alone to fan out into multi-role review.
- Select the provider from explicit model intent: Gemini by default, Claude only when requested through Antigravity.
- Select roles from requested review dimensions when the user names them; otherwise use the default role set.
- Do not add a role that the user did not ask for unless using the documented default role set.
- Add model selection only as explicit argv tokens or environment variables when the user names a concrete model label.
