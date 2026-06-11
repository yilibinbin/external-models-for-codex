---
name: claude-review-gate
description: Configure the opt-in Claude Stop review gate for Claude for Codex.
---

# Claude Review Gate

Use this skill when the user wants Claude to run a Stop-time gate before Codex finishes a turn.

Enable the gate:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" setup --enable-review-gate --review-gate-mode multi-role
```

Disable the gate:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" setup --disable-review-gate
```

Inspect gate status:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" setup
```

Manual gate run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" review-gate
```

Manual deep gate debugging:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" review-gate --quality strong
```

## Natural-Language Claude Routing

<!--
routing:review-gate
routing:manual-gate-escalation-only
routing:stop-hook-conservative
-->

- Do not ask the user to write `--quality`, `--model`, or `--effort` unless troubleshooting the plugin itself.
- Installed Stop hooks stay conservative and do not force `--quality strong`, `--quality max`, SDK backend, SDK subagents, or ultrareview.
- Manual gate debugging may use `review-gate --quality strong` or `review-gate --quality max` only when the user explicitly asks for deeper manual gate review.
- Gate enable/disable state is separate from model, effort, and quality routing.
- Claude runtime failures, timeouts, invalid gate output, missing Claude, and semantic context failures remain fail-open unless Claude explicitly returns `BLOCK:`.
- Do not substitute strong local Claude routing with `claude ultrareview`; ultrareview requires the claude-ultrareview skill and explicit cost confirmation.

User-facing examples:
- "Enable the Claude Stop review gate."
- "Disable the Claude Stop review gate."
- "Manually run a deeper Claude gate check for this repository."

Internal routing procedure:
- Classify the user's intent first, then invoke the narrowest Claude for Codex command that satisfies it.
- If the user explicitly names Fable, Fable 5, or `claude-fable-5`, route with `--quality max` by default so the runtime can use capabilities detection (`best`, then `fable`, then `opus`). Use exact `--model fable` only when the user explicitly asks for an exact model flag or the local capabilities/status output advertises Fable support.
- If the user asks for the strongest, top, best, max, 顶级, 最强, or 不要省成本 local Claude pass without naming Fable, route with `--quality max`; the runtime selects the strongest supported local model and uses Claude Code's native fallback-model only for supported availability failures.
- Do not escalate routine "strict" or "deep" language directly to Fable; use `--quality strong` unless release, security, migration, multi-agent, SDK-subagent, large-diff, or explicit max signals justify `--quality max`.
- Installed Stop hook / review-gate paths stay conservative: they do not auto-select Fable from env or auto scoring, and only manual `review-gate --quality max` can use top-model routing.
- Installed Stop hooks never auto-select Fable from env or auto scoring. Only manual `review-gate --quality max`, or an exact model flag requested by the user after capabilities advertise Fable support, may use Fable.
- Use setup for enable, disable, or status; use manual review-gate only for explicit debugging or explicit one-off gate review.
- Translate explicit strength, model, effort, backend, role, or background-job requests into argv tokens outside quoted `$ARGUMENTS`.
- Keep Codex responsible for reading Claude output, judging whether findings are correct, and reconciling the final answer or implementation plan.

Rules:
- The hook is opt-in. Installing the plugin does not enable blocking behavior.
- The gate reviews current git working-tree changes, not an exact per-turn edit set.
- The enabled gate uses the default multi-role review set: correctness, security, tests, release, adversarial.
- The installed Stop hook does not force `--quality strong` or `--quality max`; it stays conservative unless a user manually runs `review-gate --quality strong|max`.
- The review gate never starts background jobs, never calls `reserve-job`, and never invokes ultrareview. It is a bounded fail-open Stop hook only.
- Explicit `review-gate --role-pack <pack>` is allowed only for gate-compatible built-in packs. `review-gate --role-pack default` is rejected because the gate accepts only gate-compatible packs; bare `review-gate` keeps the existing default set.
- Only explicit `BLOCK:` verdicts from Claude block Stop.
- Semantic context is off by default for the gate and never implicitly uses `auto`. If explicitly enabled and unavailable, the gate records degraded metadata such as `DEGRADED_PASS` and still blocks only on explicit Claude `BLOCK:`.
- Claude CLI failures, timeouts, invalid gate output, or missing Claude warn but do not block Stop.
- Export `CLAUDE_FOR_CODEX_REVIEW_GATE=off` in the environment that launches Codex hooks to bypass the gate immediately.
- Unchanged diffs that already received an all-`ALLOW:` gate result are skipped until the working-tree diff changes.
- Do not add a `hooks` field to `.codex-plugin/plugin.json`; the standard `hooks/hooks.json` file is auto-discovered by the plugin runtime.
- Do not substitute manual `--quality strong` or `--quality max` with `claude ultrareview`; ultrareview requires the `claude-ultrareview` skill and explicit cost confirmation.

After install or upgrade, check Codex Settings > Hooks and trust or enable the `Claude for codex` Stop hook if prompted.
