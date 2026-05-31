# Claude for Codex Documentation

Claude for Codex is a Codex plugin that lets Codex call the local Claude Code CLI for independent review and planning.

Gemini for Codex is the sibling Codex plugin that calls the local Gemini CLI for independent read-only review and planning. It uses Gemini plan mode and bounded inline git context in v0.1.0.

## Installation

Install from GitHub:

```bash
codex plugin marketplace add yilibinbin/claude-for-codex --ref main
codex plugin add claude-for-codex@external-models-for-codex-local
codex plugin add gemini-for-codex@external-models-for-codex-local
```

Upgrade an existing installation:

```bash
codex plugin marketplace upgrade external-models-for-codex-local
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@external-models-for-codex-local
```

Rollback from `0.4.0`: disable the review gate with `setup --disable-review-gate`, remove or downgrade the plugin, then remove stale trusted hook entries for `SessionStart`, `SessionEnd`, `UserPromptSubmit`, or `Stop` if Codex Settings still points at missing files.

Install from a local checkout:

```bash
codex plugin marketplace add .
codex plugin add claude-for-codex@external-models-for-codex-local
codex plugin add gemini-for-codex@external-models-for-codex-local
```

## Requirements

- Codex CLI with plugin support
- Claude Code CLI available as `claude`, configured with `CLAUDE_CODE_PATH`, or installed at `~/.local/bin/claude`
- Gemini CLI available as `gemini`, or configured with `GEMINI_CLI_PATH`, for Gemini for Codex
- Node.js 20 or newer
- A Git repository for review context collection

Check runtime status:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup
```

Claude CLI resolution order:

1. `CLAUDE_CODE_PATH` when it points to an executable file.
2. `claude` from the current `PATH`.
3. `~/.local/bin/claude`, which covers the default Claude install path that Codex Desktop may omit from `PATH`.

If setup reports `claudeAvailable: false` but Claude is installed elsewhere, set `CLAUDE_CODE_PATH` to the absolute executable path.

## Capabilities

- `claude-review`: read-only Claude review of local git changes or branch diffs.
- `claude-adversarial-review`: challenge assumptions, tradeoffs, rollback paths, and hidden failure modes.
- `claude-plan`: request an independent implementation plan before Codex edits.
- `claude-multi-review`: run ordered role reviews for correctness, security, tests, release, and adversarial perspectives.
- `claude-rescue`: ask Claude for read-only recovery diagnosis or explicit `--write` repair.
- `claude-status`, `claude-result`, `claude-cancel`: track background Claude jobs.
- `claude-review-gate`: configure the optional Stop hook review gate.
- `claude-collaboration-loop`: run a Codex-Claude plan, reconcile, implement, review, and report workflow.
- `gemini-review`, `gemini-adversarial-review`, `gemini-plan`, `gemini-multi-review`, `gemini-rescue`: Gemini CLI equivalents for Codex-side multi-model review. Gemini rescue is read-only in v0.1.0.

## Gemini for Codex

Install from the same local marketplace:

```bash
codex plugin marketplace add .
codex plugin add gemini-for-codex@external-models-for-codex-local
```

Gemini review runs in headless JSON mode with `gemini --approval-mode=plan --output-format=json --prompt`. v0.1.0 uses bounded inline git context and does not depend on Gemini MCP or a Gemini extension.

## Enhanced Adversarial Review

`claude-adversarial-review` asks Claude to infer the author's intent first, then review through three lenses:

- `skeptic`: correctness, completeness, unproven assumptions, and breakable states.
- `architect`: structural fitness, boundaries, coupling, and responsibility leaks.
- `minimalist`: necessity, complexity, premature abstraction, and deletable work.

The output must include:

- `## Intent`
- `## Verdict: PASS | CONTESTED | REJECT`
- `## Findings`
- `## What Went Well`
- `## Lead Judgment`

Use a lens subset when the review needs a narrower challenge:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --adversarial-lenses skeptic,minimalist --base main
```

Use `--json` for a validated `{verdict, summary, findings, next_steps}` object. Use `--background` on `review`, `adversarial-review`, `multi-review`, or `rescue` to start a tracked job, and retrieve it with `claude-result`. `rescue --write` is explicit opt-in and records before/after git fingerprints.

## Host-forwarded background jobs

`--background` supports a Codex host-forwarded path. Skills first reserve a job with `reserve-job`, then Codex dispatches exactly one forwarding subagent to run the returned `workerCommand`. The child worker only executes `run-reserved-job`; it does not inspect or reinterpret repository state. Existing detached runtime background jobs remain as a compatibility fallback.

## MCP-backed read-only Git review

Read-only Claude review receives a strict MCP config for bounded Git inspection. The bundled read-only Git MCP server exposes status, diff, cached diff, log, show, blame, grep, and ls-files through validated Git arguments while `Bash`, `Edit`, `Write`, and `MultiEdit` remain disallowed.

## Routing

Claude for Codex is a skills-and-hook plugin, not an MCP/app tool plugin. It is expected that `tool_search` will not return a `claude-for-codex` callable tool. That is not an installation failure.

Use the Codex skills instead:

- `claude-for-codex:claude-review`
- `claude-for-codex:claude-adversarial-review`
- `claude-for-codex:claude-multi-review`
- `claude-for-codex:claude-rescue`
- `claude-for-codex:claude-status`
- `claude-for-codex:claude-result`
- `claude-for-codex:claude-cancel`
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

After installing or upgrading, open Codex Settings > Hooks and trust or enable the `Claude for Codex` hooks. `SessionStart`, `SessionEnd`, and `UserPromptSubmit` provide session tracking, unread-result reminders, and turn-baseline capture when supported by the local Codex runtime.

## Safety Model

- Review workflows call Claude with read-only permissions.
- Background jobs persist outside the repository under plugin data state.
- Codex remains responsible for accepting or rejecting Claude findings.
- The Stop gate blocks only when Claude explicitly returns `BLOCK:`.
- Missing Claude, authentication failures, rate limits, timeouts, invalid output, or runtime failures fail open with warnings instead of blocking Codex.

## Direct Runtime Commands

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs review --background --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs jobs
node plugins/claude-for-codex/scripts/claude-companion.mjs result <job-id>
node plugins/claude-for-codex/scripts/claude-companion.mjs plan "implement the feature and include tests"
node plugins/claude-for-codex/scripts/claude-companion.mjs status
```
