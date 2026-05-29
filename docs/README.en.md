# Claude for Codex Documentation

Claude for Codex is a Codex plugin that lets Codex call the local Claude Code CLI for independent review and planning.

## Installation

Install from GitHub:

```bash
codex plugin marketplace add yilibinbin/claude-for-codex --ref main
codex plugin add claude-for-codex@claude-for-codex-local
```

Upgrade an existing installation:

```bash
codex plugin marketplace upgrade claude-for-codex-local
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@claude-for-codex-local
```

Install from a local checkout:

```bash
codex plugin marketplace add .
codex plugin add claude-for-codex@claude-for-codex-local
```

## Requirements

- Codex CLI with plugin support
- Claude Code CLI available as `claude`
- Node.js 18 or newer
- A Git repository for review context collection

Check runtime status:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup
```

## Capabilities

- `claude-review`: read-only Claude review of local git changes or branch diffs.
- `claude-adversarial-review`: challenge assumptions, tradeoffs, rollback paths, and hidden failure modes.
- `claude-plan`: request an independent implementation plan before Codex edits.
- `claude-multi-review`: run ordered role reviews for correctness, security, tests, release, and adversarial perspectives.
- `claude-review-gate`: configure the optional Stop hook review gate.
- `claude-collaboration-loop`: run a Codex-Claude plan, reconcile, implement, review, and report workflow.

## Routing

Claude for Codex is a skills-and-hook plugin, not an MCP/app tool plugin. It is expected that `tool_search` will not return a `claude-for-codex` callable tool. That is not an installation failure.

Use the Codex skills instead:

- `claude-for-codex:claude-review`
- `claude-for-codex:claude-adversarial-review`
- `claude-for-codex:claude-multi-review`
- `claude-for-codex:claude-plan`
- `claude-for-codex:claude-review-gate`
- `claude-for-codex:claude-collaboration-loop`

## Stop Hook Review Gate

The hook file is installed with the plugin, but the gate is disabled by default. Enable it from the repository you want to protect:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --enable-review-gate --review-gate-mode multi-role
```

Disable it:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --disable-review-gate
```

After installing or upgrading, open Codex Settings > Hooks and trust or enable the `Claude for Codex` Stop hook.

## Safety Model

- Review workflows call Claude with read-only permissions.
- Codex remains responsible for accepting or rejecting Claude findings.
- The Stop gate blocks only when Claude explicitly returns `BLOCK:`.
- Missing Claude, authentication failures, rate limits, timeouts, invalid output, or runtime failures fail open with warnings instead of blocking Codex.

## Direct Runtime Commands

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs plan "implement the feature and include tests"
node plugins/claude-for-codex/scripts/claude-companion.mjs status
```

