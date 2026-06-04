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

Rules:
- The hook is opt-in. Installing the plugin does not enable blocking behavior.
- The gate reviews current git working-tree changes, not an exact per-turn edit set.
- The enabled gate uses the default multi-role review set: correctness, security, tests, release, adversarial.
- Explicit `review-gate --role-pack <pack>` is allowed only for gate-compatible built-in packs. `review-gate --role-pack default` is rejected in `0.12.0`; bare `review-gate` keeps the existing default set.
- Only explicit `BLOCK:` verdicts from Claude block Stop.
- Semantic context is off by default for the gate and never implicitly uses `auto`. If explicitly enabled and unavailable, the gate records degraded metadata such as `DEGRADED_PASS` and still blocks only on explicit Claude `BLOCK:`.
- Claude CLI failures, timeouts, invalid gate output, or missing Claude warn but do not block Stop.
- Export `CLAUDE_FOR_CODEX_REVIEW_GATE=off` in the environment that launches Codex hooks to bypass the gate immediately.
- Unchanged diffs that already received an all-`ALLOW:` gate result are skipped until the working-tree diff changes.
- Do not add a `hooks` field to `.codex-plugin/plugin.json`; the standard `hooks/hooks.json` file is auto-discovered by the plugin runtime.

After install or upgrade, check Codex Settings > Hooks and trust or enable the `Claude for codex` Stop hook if prompted.
