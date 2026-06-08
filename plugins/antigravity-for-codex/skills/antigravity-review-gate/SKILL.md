---
name: antigravity-review-gate
description: Configure and manually run the opt-in Antigravity Stop review gate for Antigravity for Codex.
---

# Antigravity Review Gate

Use this skill when the user wants Antigravity to run a Stop-time gate before Codex finishes a turn.

Enable the gate in the environment that launches Codex hooks:

```bash
export ANTIGRAVITY_FOR_CODEX_REVIEW_GATE=on
```

Disable or bypass the gate:

```bash
export ANTIGRAVITY_FOR_CODEX_REVIEW_GATE=off
```

Manual gate run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/antigravity-companion.mjs" review-gate "$ARGUMENTS"
```

Rules:
- The hook is opt-in. Installing the plugin does not enable blocking behavior.
- Empty, `off`, `false`, and `0` values disable the runtime gate.
- Only a first output line beginning `BLOCK:` blocks Stop.
- `ALLOW:` exits successfully with no hook decision JSON.
- Antigravity runtime failures, timeouts, invalid output, or wrapper errors warn and fail open.
- Do not add a `hooks` field to `.codex-plugin/plugin.json`; `hooks/hooks.json` is auto-discovered by the plugin runtime.
