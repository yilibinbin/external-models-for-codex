---
name: gemini-review-gate
description: Configure the opt-in Gemini Stop review gate for Gemini for Codex.
---

# Gemini Review Gate

Use this skill when the user wants Gemini to run a Stop-time gate before Codex finishes a turn.

Enable the gate:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" setup --enable-review-gate --review-gate-mode multi-role
```

Disable the gate:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" setup --disable-review-gate
```

Inspect gate status:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" setup
```

Manual gate run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/gemini-companion.mjs" review-gate
```

Rules:
- The hook is opt-in. Installing the plugin does not enable blocking behavior.
- The gate reviews current git working-tree changes, not an exact per-turn edit set.
- The enabled gate uses the default multi-role review set: correctness, security, tests, release, adversarial.
- Only explicit `BLOCK:` verdicts from Gemini block Stop.
- Gemini CLI failures, timeouts, invalid gate output, or missing Gemini warn but do not block Stop.
- Export `GEMINI_FOR_CODEX_REVIEW_GATE=off` in the environment that launches Codex hooks to bypass the gate immediately.
- Unchanged diffs that already received an all-`ALLOW:` gate result are skipped until the working-tree diff changes.
- Do not add a `hooks` field to `.codex-plugin/plugin.json`; the standard `hooks/hooks.json` file is auto-discovered by the plugin runtime.

After install or upgrade, check Codex Settings > Hooks and trust or enable the `Gemini for codex` Stop hook if prompted.
