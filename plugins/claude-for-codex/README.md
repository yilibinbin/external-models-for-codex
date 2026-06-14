# Claude for Codex

Codex plugin that invokes Claude Code for independent read-only review, adversarial review, implementation planning, rescue diagnosis, and tracked job workflows.

## Publishing Metadata

This plugin is prepared for a Codex plugin page with:

- Name: Claude for Codex
- Category: Productivity
- Developer: fanghao
- License: MIT
- Homepage: https://github.com/yilibinbin/external-models-for-codex
- Repository: https://github.com/yilibinbin/external-models-for-codex
- Marketplace id: `external-models-for-codex`
- Plugin id: `claude-for-codex`
- Current version: `0.19.0`

Published capabilities:

- Read-only Claude Code review of working-tree or branch changes.
- Adversarial review for assumptions, rollback risks, hidden failure modes, and simpler alternatives.
- Claude implementation planning for Codex to reconcile before editing.
- Read-only Claude rescue diagnosis when Codex is stuck or validation is failing.
- Multi-role review fan-out across correctness, security, tests, release, and adversarial perspectives.
- Adaptive `--quality auto|fast|standard|strong|max` model/effort policy using Claude Code aliases.
- Dynamic model alias registry for Claude Code aliases such as `best`, `fable`, `opus`, `sonnet`, `haiku`, `opusplan`, `opus[1m]`, `sonnet[1m]`, and `inherit`.
- Native SDK subagent review teams with `--backend sdk --agent-team sdk-subagents`.
- Request-local outcome classification for Claude CLI and SDK runs, including compact refusal, fallback, timeout, and permission-compatibility metadata.
- Structured `review --json` and role-tagged `multi-review --json` for machine-readable findings.
- Native structured output and sanitized streaming progress with `--native-structured` and `--stream-progress`.
- Tracked job lifecycle commands for status, result retrieval, and conservative cancellation.
- Global Claude work-slot governor that bounds foreground review, background jobs, plugin-managed role fan-out, SDK subagents, Stop hooks, and delegated Codex subagent launches.
- Capability diagnostics and cheap `doctor --json` health checks for Claude CLI, SDK package resolution, Git, GitHub CLI, hooks, MCP, state, review-gate, and optional semantic providers.
- Optional semantic context for review commands, disabled by default.
- Optional Claude SDK backend for explicitly selected review, gate, plan, and rescue flows.
- Explicit-consent Claude ultrareview with `--confirm-cost`.
- GitHub Actions PR review workflow templates with fork-safe defaults.
- Fork-safe repository CI dogfood for plugin syntax, focused tests, release checks, and whitespace validation.
- Sanitized per-run review reports that omit prompts, diffs, raw model output, and secrets by default.
- Release checks for manifest, hook, docs, prompt, skill, and secret-scan hygiene.
- Background execution for long Claude review, adversarial review, multi-review, and rescue jobs.
- Session/UserPrompt hooks for session state, turn baselines, and unread-result reminders.
- Opt-in Stop hook review gate that blocks only on explicit Claude `BLOCK:` verdicts.
- Codex-Claude collaboration loop for plan, review, reconcile, implement, and report workflows.

Safety and operating model:

- Review commands invoke Claude Code with read-only tool permissions.
- Read-only CLI review disables Claude Code slash commands, settings sources, and session persistence while preserving the normal Claude authentication path.
- Read-only SDK review disables Claude Code settings, skills, hooks, plugins, and SDK session persistence while preserving the normal Claude authentication path.
- CLI mode remains the default backend. The SDK backend runs only with `--backend sdk` or `CLAUDE_FOR_CODEX_BACKEND=sdk`.
- Native SDK subagent teams require the SDK backend and keep the read-only Git MCP boundary.
- Native SDK child agents run with fresh isolated context, preserve normalized model aliases, and deny nested `Agent`, shell, workflow, and write-tool escalation.
- Outcome classification stores compact status metadata only; reports do not persist raw SDK events, prompt text, source snippets, or raw model output.
- Ultrareview may use remote/cloud execution and usage-credit billing; it requires `--confirm-cost` or `CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1`.
- Rescue is read-only by default; `rescue --write` is explicit opt-in and records git fingerprints before and after Claude runs.
- Codex remains responsible for applying or rejecting Claude findings.
- The Stop gate is disabled by default after installation.
- Claude CLI failures, authentication failures, rate limits, timeouts, or invalid gate output fail open and emit warnings.
- Claude work-slot capacity exhaustion returns `capacity_blocked` instead of spawning unbounded extra work. Plugin-managed role fan-out downgrades to sequential when partial capacity is available; SDK subagent teams reserve one slot per requested role and return `capacity_blocked` when the whole team cannot be admitted safely.
- The Stop gate reviews current git working-tree changes, not an exact previous-turn file list.
- Generated GitHub Actions workflows use `pull_request`, avoid default `pull_request_target`, pin immutable release refs, and skip fork PR Claude/comment/annotation steps by default.

Adaptive quality:

- `--quality auto` is the default and scores command type, JSON output, role count, risky roles, backend, SDK subagent teams, semantic context, and diff size.
- `--quality fast` uses Claude Code's `sonnet` alias with low effort.
- `--quality standard` uses `sonnet` with high effort.
- `--quality strong` uses `opus` with xhigh effort.
- `--quality max` uses the strongest advertised local Claude alias with max effort, preferring `best`, then `fable`, then `opus`.
- Explicit `--model` and `--effort` always win over `--quality`.
- The policy uses Claude Code aliases instead of concrete model ids such as `claude-opus-4-8`, so Claude Code can map aliases to the current best available model.
- The model alias registry is shared by CLI quality policy and SDK subagent selection so future Claude Code alias updates do not require duplicated hardcoded allowlists.
- `ultracode` is not passed as `--effort`; current noninteractive Claude Code accepts only `low`, `medium`, `high`, `xhigh`, and `max`.
- `claude ultrareview` remains a separate explicit command requiring `--confirm-cost` and is never used by hooks or default review paths.
- Set `CLAUDE_FOR_CODEX_QUALITY=standard|strong|max` to change the default for manual commands. Stop hooks and `review-gate` remain capped to `standard` unless you run `review-gate` manually with explicit `--quality strong` or `--quality max`.

### Fable / top-model routing

Claude for Codex treats `--quality max` as the strongest local Claude tier. On Claude Code versions that advertise a top model alias, the runtime prefers `best`, then `fable`, then falls back to `opus`. When a top model is selected through the CLI backend and `--fallback-model` is available, the plugin adds Claude Code's native fallback unless you supplied your own fallback. It uses `--fallback-model opus,sonnet` only when the installed CLI help advertises comma-separated fallback lists; otherwise it uses `--fallback-model opus`. This fallback only handles Claude Code-supported model unavailable, overload, or server-side model errors; it does not handle auth, quota, billing, rate-limit, network, or request-size failures.

Explicit model choices always win:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review \
  --model fable --effort max --scope branch --base origin/main --json
```

Natural language routing uses Fable only for explicit Fable/top/max requests or very high-risk automatic scores. Ordinary deep review remains `--quality strong`, which maps to Opus. Installed Stop hooks stay conservative and do not automatically use Fable. SDK backend runs do not infer top-model availability from CLI help; use explicit `--model` or `CLAUDE_FOR_CODEX_TOP_MODEL` when you want SDK subagents to use a top alias.

Natural-language Claude routing:

- Users can ask for Claude in natural language. Codex maps the request to Claude for Codex quality, model, effort, backend, role, and background-job arguments.
- Normal review requests use the existing adaptive quality policy.
- Strict, deep, advanced, high-confidence, high-risk, release, security, migration, or difficult rescue/planning requests should route to stronger local Claude quality when the skill context supports it.
- Requests for the strongest local Claude review route to max local quality unless the user names a concrete model or effort.
- Requests for native Claude subagents route to SDK subagent mode only when explicitly asked.
- Generic strict or strong language never selects ultrareview. Ultrareview remains explicit because it may use remote/cloud execution and usage-credit billing.
- Users do not need to write internal flags in normal conversation. The skills describe the internal routing rules Codex should apply.

Important routing note:

- Claude for Codex is a skills-and-hook plugin, not an MCP/app tool plugin.
- `tool_search` only discovers callable tools exposed by MCP servers or apps, so it is expected not to return a `claude-for-codex` tool.
- When this plugin is installed and enabled, Codex should route through the `claude-for-codex:*` skills, which run `scripts/claude-companion.mjs`; lack of a `tool_search` result is not an installation failure.

## Requirements

- Codex CLI with plugin support
- Claude Code CLI available as `claude`, configured with `CLAUDE_CODE_PATH`, or installed at `~/.local/bin/claude`
- Git repository for review scope collection
- Node.js 20 or newer
- Optional `@anthropic-ai/claude-agent-sdk` package for SDK native subagent mode; `@anthropic-ai/claude-code` remains supported as a compatibility fallback

## Setup Check

From the plugin directory:

```bash
cd plugins/claude-for-codex
node scripts/claude-companion.mjs setup
```

Expected output includes:

```json
{
  "claudeAvailable": true,
  "claudeCommand": "<resolved claude command path>",
  "gitAvailable": true,
  "reviewGate": {
    "enabled": false,
    "mode": "multi-role"
  },
  "resourceGovernor": {
    "ok": true,
    "enabled": true,
    "lockRootClass": "user-codex-data"
  }
}
```

Claude CLI resolution order:

1. `CLAUDE_CODE_PATH` when it points to an executable file.
2. `claude` from the current `PATH`.
3. `~/.local/bin/claude`, which covers the default Claude install path that Codex Desktop may omit from `PATH`.

If setup reports `claudeAvailable: false` but Claude is installed elsewhere, set `CLAUDE_CODE_PATH` to the absolute executable path before running Codex.

For a cheap health check that does not run a Claude prompt or spend model quota:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs doctor --json
```

`doctor --json` also reports install consistency between the running `claude-for-codex` plugin manifest and Codex's enabled `claude-for-codex@external-models-for-codex` registry entry. If Codex reports an older installed version than the running plugin, run:

```bash
codex plugin marketplace upgrade external-models-for-codex
codex plugin add claude-for-codex@external-models-for-codex
```

Review, multi-review, adversarial review, plan, and rescue prompts automatically include bounded advisory project rules from `CLAUDE.md`, `REVIEW.md`, `.claude/review.md`, and `.claude/CLAUDE.md`. Symlinks and files outside the workspace are ignored. `capabilities --json` includes a quality-policy explanation so Codex can report why `--quality auto|fast|standard|strong|max` chose a model and effort.

## Install From This Repository

```bash
codex plugin marketplace add .
```

Then install or enable `claude-for-codex` from the Codex plugin UI.
After installing or upgrading, open Codex Settings > Hooks and trust or enable the `Claude for Codex` Stop hook if you want the opt-in review gate available.

## Remote Install

Install the released Claude plugin from the immutable Claude release ref:

```bash
codex plugin marketplace add yilibinbin/external-models-for-codex --ref claude-for-codex-v0.19.0
codex plugin add claude-for-codex@external-models-for-codex
```

`external-models-for-codex` is this repository's Codex marketplace for plugins that connect Codex to external model CLIs. It currently publishes both Claude for Codex and Gemini for Codex.

The `yilibinbin/external-models-for-codex` owner/repo form assumes this repository remains under that GitHub owner. If your Codex setup requires an explicit Git URL, use `https://github.com/yilibinbin/external-models-for-codex.git` or `git@github.com:yilibinbin/external-models-for-codex.git`.

For local development only, replace the release ref with `--ref main`.

## Upgrade

```bash
codex plugin marketplace upgrade external-models-for-codex
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@external-models-for-codex
```

## Rollback

To roll back from `0.4.0` to a `0.3.x` install, first disable the review gate in each protected repository:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --disable-review-gate
```

Then remove or downgrade the plugin through Codex. If Codex Settings > Hooks still shows trusted entries for `SessionStart`, `SessionEnd`, `UserPromptSubmit`, or `Stop` that point at missing files, remove or disable those hook entries before continuing work. The repo-external state directory printed by `setup` may be deleted to reset gate/job/session state for that workspace.

## Skills

- `claude-review`: normal read-only review of current changes or `--base <ref>`.
- `claude-adversarial-review`: steerable challenge review for design assumptions and failure modes.
- `claude-multi-review`: opt-in role fan-out review across multiple read-only Claude perspectives.
- `claude-ultrareview`: explicit Claude cloud ultrareview after user consent for possible usage-credit billing.
- `claude-rescue`: read-only diagnosis by default, or explicit `--write` repair when the user asks Claude to modify files.
- `claude-status`: list tracked Claude jobs for the current workspace.
- `claude-result`: retrieve a tracked Claude job result by job id.
- `claude-cancel`: cancel only when the runtime can safely validate the job state.
- `claude-review-gate`: configure the opt-in Stop-time Claude review gate.
- `claude-github-actions-review`: generate or validate fork-safe GitHub Actions PR review workflows.
- `claude-plan`: independent Claude implementation plan for Codex reconciliation.
- `claude-plan-review`: review a saved implementation plan file with Claude role agents; use `--backend sdk --agent-team sdk-subagents` only when explicitly requesting Claude SDK native subagents.
- `claude-collaboration-loop`: full plan, reconcile, implement, adversarial review, report workflow.

## Direct Runtime Commands

Run from the repository root:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs review --json --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs review --backend sdk --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs recommend-execution-mode --json
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --base main challenge the rollback design
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --json --roles correctness,security --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --backend sdk --agent-team sdk-subagents --json --native-structured --stream-progress --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --roles correctness,security --scope branch --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs plan-review --plan "docs/superpowers/plans/example.md" --json
node plugins/claude-for-codex/scripts/claude-companion.mjs plan-review --plan "docs/superpowers/plans/example.md" --backend sdk --agent-team sdk-subagents --json --native-structured
node plugins/claude-for-codex/scripts/claude-companion.mjs native-plugin validate --json
node plugins/claude-for-codex/scripts/claude-companion.mjs native-plugin path
node plugins/claude-for-codex/scripts/claude-companion.mjs ultrareview --confirm-cost --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs rescue diagnose the failing release validation
node plugins/claude-for-codex/scripts/claude-companion.mjs rescue --write fix the failing test
node plugins/claude-for-codex/scripts/claude-companion.mjs review --background --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs jobs
node plugins/claude-for-codex/scripts/claude-companion.mjs result <job-id>
node plugins/claude-for-codex/scripts/claude-companion.mjs cancel <job-id>
node plugins/claude-for-codex/scripts/claude-companion.mjs capabilities
node plugins/claude-for-codex/scripts/claude-companion.mjs report --latest
node plugins/claude-for-codex/scripts/claude-companion.mjs release-check
node plugins/claude-for-codex/scripts/claude-companion.mjs release-check --ci-simulate
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions render
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions init --write
node plugins/claude-for-codex/scripts/claude-companion.mjs github-actions validate
node plugins/claude-for-codex/scripts/claude-companion.mjs plan build the plugin and include tests
node plugins/claude-for-codex/scripts/claude-companion.mjs status
```

`multi-review` runs several role-specialized Claude review prompts in parallel by default and prints one section per role plus an orchestration summary. This plugin-managed CLI fan-out is read-only; Codex must reconcile findings before any follow-up changes. Use `--sequential` to run one role at a time for debugging or rate-limit-sensitive environments. For Claude native SDK mode, use `multi-review --backend sdk --agent-team sdk-subagents`; this requires the Claude SDK backend, configures native subagents for the selected roles, and rejects incompatible `--sequential` or non-SDK combinations before invoking Claude.

Native SDK mode resolves `@anthropic-ai/claude-agent-sdk` first and keeps `@anthropic-ai/claude-code` as a compatibility fallback. It is opt-in and experimental until live SDK subagent smoke tests are stable; plugin-managed CLI `multi-review` remains the default. SDK subagents are created as fresh isolated context reviewers, preserve normalized Claude model aliases including long-context forms, and cannot call nested `Agent`, shell, workflow, or write tools. Combine `--json --native-structured` to request a schema-backed SDK aggregate where `role_results[].result.review` is a full review JSON object. The plugin validates that object locally and does not serialize raw role text or raw SDK `structured_output` into reports. Add `--stream-progress` to show sanitized progress events without printing raw SDK chunks or storing raw SDK messages in reports.

Role packs are named reviewer presets for `multi-review`. Use `roles list`, `roles inspect <pack>`, and `multi-review --role-pack <pack>` for built-in packs such as `minimal`, `release`, `security`, and `default`. User-authored JSON packs can be validated with `roles validate <file>`, but they are validate/inspect-only and are not executable by review commands. Role packs are plugin-managed presets, not native Claude subagents, and they cannot grant tools, shell commands, hooks, MCP servers, environment variables, backend mode, or write permissions.

Mailbox and advisory leases are optional coordination metadata for long-running review. `mailbox list|show|post` stores sanitized summaries only under repo-external plugin state. `leases list|claim|release` declares path attention without locking files. Lease conflicts are warnings only; they do not change review verdicts or `review-gate` behavior.

`review --json` asks Claude for a normalized `{verdict, summary, findings, next_steps}` object using `approve` or `needs-attention`. `multi-review --json` asks every role for the same schema and returns one aggregate object with role-tagged findings and per-role results. Invalid or malformed structured output exits non-zero and includes the raw Claude output for diagnosis.

For `--json` modes, exit status reports whether the Claude invocation and JSON parsing succeeded. Callers must inspect the returned `verdict` to decide whether findings need attention.

`jobs`, `result`, and `cancel` are the stable lifecycle surface for tracked Claude work. The existing `status` command remains a diagnostic command that calls `claude agents --json --cwd`; it is intentionally not repurposed for job listing. Use `--background` on `review`, `adversarial-review`, `multi-review`, or `rescue` to start a tracked job. Add `--wait` when a script should block until that job reaches a terminal state.

`--wait` is a short observation window, not the hard Claude timeout. It defaults to 45 seconds and can be adjusted with `--wait-timeout-ms <ms>`. If the window expires while the worker is still healthy, the command exits 0 with `{"status":"running","waitTimedOut":true,"job":...}`; use `jobs` or `result <job-id>` later. Do not rerun the same review just because `--wait` expired.

Stored job `stdout` and `stderr` are sanitized and capped to keep plugin state bounded. `result <job-id>` includes `stdoutBytes`, `stderrBytes`, `stdoutStoredBytes`, `stderrStoredBytes`, `stdoutTruncated`, and `stderrTruncated` so Codex can report when long model output was shortened instead of silently treating the stored text as complete.

`recommend-execution-mode --json` inspects bounded local git signals and recommends `foreground` only for tiny one-to-two-file work. It recommends `background` for untracked directories, more than two files, more than roughly fifty changed lines, multi-role/adversarial/rescue work, unclear scope, or git signal timeouts. A timeout means the git signal collection was inconclusive; it is not evidence that an existing Claude job failed.

Claude for Codex starts at most three active tracked background jobs per workspace by default. Set `CLAUDE_FOR_CODEX_MAX_ACTIVE_JOBS=<n>` to adjust the cap. When the cap is reached, the plugin refuses to start another expensive Claude request and asks you to inspect or cancel an existing job. Stale-heartbeat cleanup is process-aware: it does not free capacity or resubmit while a validated worker or child process still exists. If `jobs` shows phase `unsafe-child-identity`, the plugin found a live child PID without saved identity and deliberately preserves capacity instead of risking PID-reuse signaling; inspect the process and use `cancel <job-id>` or manual cleanup before resubmitting. If `jobs` shows `leaderless-liveness-inconclusive`, the bounded `ps` probe could not prove whether a process group still has live members, so capacity is preserved until probing recovers or you inspect the process. Loaded CI runners can adjust the probe window with `CLAUDE_FOR_CODEX_PS_TIMEOUT_MS=<milliseconds>`.

If the same background command, arguments, and workspace are submitted again while the original job is queued or running, Claude for Codex returns the existing job id with `reusedExisting: true` and does not spawn another Claude process. This is the runtime enforcement behind the no-resubmit rule.

## Global Claude Resource Governor

Claude for Codex also has a process-level governor that applies across workspaces and launch surfaces. It stores portable process leases under `~/.codex/claude-for-codex/global-resource-locks` by default, outside repositories and outside plugin checkout paths. Override the location only with an absolute, private directory through `CLAUDE_FOR_CODEX_GLOBAL_RESOURCE_LOCK_DIR`; unsafe, symlinked, group-writable, world-writable, file, or wrong-owner roots are rejected instead of being silently used.

Set `CLAUDE_FOR_CODEX_MAX_CLAUDE_PROCESSES=<n>` to cap all plugin-owned Claude work slots for foreground reviews, background jobs, plugin-managed multi-role/adversarial fan-out, SDK subagent teams, Stop hooks, and Codex subagent delegation. The default follows detected host parallelism up to a small cap; typical 5+ core hosts preserve normal five-role parallel review, while constrained hosts scale down automatically. `0` is an emergency/test setting that blocks new Claude launches.

When no slot is available, foreground JSON commands return `{"status":"capacity_blocked", ...}` and exit `75`; human foreground commands print a concise stderr message and exit `75`; background jobs become terminal `capacity_blocked`; Stop hooks fail open and warn. If a plugin-managed parallel role review has at least one slot but not enough for the whole role set, it automatically runs sequentially and records `executionMode: "sequential"` in reports. SDK subagent teams reserve up to one slot per requested role, capped by the effective host limit, and return `capacity_blocked` when the whole native-agent team cannot be admitted safely.

Leases refresh while Claude work is running. When a command has a known long timeout, Claude for Codex raises that lease's TTL floor to cover the operation timeout plus a refresh margin. An expired lease whose owner PID is still live, or whose liveness is inconclusive, is preserved rather than reclaimed; dead-PID leases remain reclaimable.

`setup`, `status`, `doctor --json`, and sanitized reports expose `resourceGovernor`, `capacityStatus`, `lockRootClass`, requested slots, effective max, and available slots without leaking raw lock-root paths.

Stop hook never starts background jobs, never calls `reserve-job`, and never invokes ultrareview. If review-gate work exceeds its bounded role timeout or Claude is unavailable, it fails open and tells you to run an explicit tracked review.

`capabilities` prints JSON diagnostics for the resolved Claude CLI, supported Claude flags, optional SDK availability, Git/GitHub CLI availability, hook trust, the bundled Git MCP server, and path-only detection of future semantic context providers. `doctor --json` is the cheap first-stop diagnostic for installed setups: it reports CLI, SDK, hook compatibility, review-gate state, resource-governor capacity, state-file health, and semantic-provider configuration without running a Claude prompt or initializing providers.

`--backend sdk` opts into the Claude SDK backend when `@anthropic-ai/claude-agent-sdk` or `@anthropic-ai/claude-code` is importable locally or through a controlled global npm resolution fallback. SDK review mode uses explicit read-only allowed tools, denies configured write-tool candidates such as `Edit`, `Write`, `MultiEdit`, and `Bash` when the installed Claude runtime recognizes them, disables SDK settings sources, skills, hooks, plugins, and session persistence, and reuses the strict read-only Git MCP config. SDK-backed background and reserved jobs automatically enable sanitized stream progress so `jobs` and `result` can show progress previews without raw model chunks. If the SDK cannot be resolved or cannot provide the required safety controls, the command fails before Claude invocation. Unset `CLAUDE_FOR_CODEX_BACKEND` or pass `--backend cli` to return to the default CLI backend.

`ultrareview` forwards to Claude's native cloud ultrareview command. It is never used by hooks or default review paths, and it refuses to run unless the user has explicitly consented with `--confirm-cost` or `CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1` because the command may use remote/cloud execution and usage-credit billing.

Semantic context is disabled by default. Use `--semantic-context <provider>` on `review`, `multi-review`, `adversarial-review`, or `review-gate` only after configuring a repo-external provider. Provider commands must be argv arrays, run with an allowlist-only environment, stay outside the workspace, and return workspace-bound JSON. Semantic context is advisory; Claude findings still need changed-file or git evidence. If semantic context fails in `review-gate`, the gate records degraded metadata such as `DEGRADED_PASS` and still blocks only on explicit Claude `BLOCK:`.

`report --latest` reads the latest sanitized review report from the repo-external plugin data directory. Reports are minimal metadata only: command, scope, roles/lenses, backend, model/effort, timestamps, exit status, output byte counts, and structured verdict/finding counts when available. Reports do not store prompts, source code, diffs, raw model output, environment variables, or raw absolute workspace paths by default. Set `CLAUDE_FOR_CODEX_NO_TELEMETRY=1` to disable all non-job report writes.

`github-actions render` prints a GitHub Actions PR review workflow and writes nothing. `github-actions init --write` writes `.github/workflows/claude-for-codex-review.yml` and refuses to overwrite without `--force`. `github-actions validate` checks minimal permissions, fork-safe gates, immutable release refs, GitHub context env mapping, absence of local absolute paths, and no default `pull_request_target`. Checks annotations are opt-in with `--annotations` because they add `checks: write`. This repository also ships a fork-safe CI dogfood workflow that runs syntax checks, focused pytest, `release-check --ci-simulate --json`, and `git diff --check` for Claude for Codex changes.

The generated GitHub Actions workflow is a template. It uses `pull_request`, pins `codex plugin marketplace add yilibinbin/external-models-for-codex --ref claude-for-codex-v0.19.0`, maps GitHub context through environment variables before shell use, uploads structured review JSON as a short-retention artifact, and skips Claude/comment/annotation publishing for fork PRs by default. Maintainers must configure Claude authentication or secrets explicitly in their CI environment. A future unsafe `pull_request_target` variant would need separate review; this version does not generate one.

`release-check` validates release hygiene for this repository, including manifest metadata, model alias registry wiring, outcome classification/reporting, resource-governor wiring, hook compatibility diagnostics, doctor availability, fork-safe repository CI, and docs. `release-check --ci-simulate` adds fixture-driven GitHub Actions validation without calling the live GitHub API, reading user HOME, requiring secrets, or using local Codex caches. Remote install smoke is skipped by default for local development; use `--remote-install --ref claude-for-codex-v0.19.0` for a fail-soft smoke or `--require-remote-install --ref claude-for-codex-v0.19.0` when a release must fail if GitHub install fails.

## Host-forwarded background jobs

`--background` supports a Codex host-forwarded path. Skills first reserve a job with `reserve-job`, then Codex dispatches exactly one forwarding subagent to run the returned `workerCommand`. The child worker only executes `run-reserved-job`; it does not inspect or reinterpret repository state. The returned command carries an explicit `--cwd` so it can claim the correct workspace state even if the forwarding shell starts elsewhere. Existing detached runtime background jobs remain as a compatibility fallback.

Unclaimed host-forwarded reservations use a separate claim deadline from direct worker bootstrap cleanup: the default is ten minutes and can be adjusted with `CLAUDE_FOR_CODEX_RESERVATION_CLAIM_MS=<milliseconds>`. After that deadline, ordinary `jobs`/`result` polling may reap the reservation as `reservation-expired` to release capacity. Unclaimed reservations count toward the active job cap while they are waiting; high-fanout operators can tune both `CLAUDE_FOR_CODEX_RESERVATION_CLAIM_MS` and `CLAUDE_FOR_CODEX_MAX_ACTIVE_JOBS`.

If a host-forwarded reservation is still unclaimed and the same request is started directly with `--background`, the direct path starts its own tracked worker instead of waiting on the reservation. That avoids a silent no-op on abandoned reservations, but it can temporarily consume two active slots if the forwarding subagent later claims the original reservation.

## Codex subagent delegation

For foreground read-only delegation, the parent Codex turn uses `subagent-command` for review commands instead of hand-building Claude invocations:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command review "$ARGUMENTS"
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command adversarial-review "$ARGUMENTS"
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command multi-review "$ARGUMENTS"
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command rescue "$ARGUMENTS"
```

`subagent-command` prints JSON containing an absolute `workerCommand` argv array and the exact `cwd` to use. Dispatch exactly one Codex subagent to run that argv exactly once from the returned `cwd`. The child must not inspect or reinterpret the repository before execution, and must not replace the plugin call with raw `claude -p` or any other hand-built Claude CLI command.

Use `reserve-job` for `--background` delegation so the plugin runtime tracks the job. `ultrareview` and every `--write` command are intentionally not delegatable.

`rescue --write` requires a git repository and runs Claude Code with write permissions. The runtime writes a before/after working-tree fingerprint to stderr; Codex must inspect the resulting diff before reporting success.

Default roles:

- `correctness`: bugs, regressions, edge cases, and behavioral contract breaks.
- `security`: read-only safety, secrets exposure, injection risks, and unsafe command or path handling.
- `tests`: missing, brittle, or overfit tests and release validation gaps.
- `release`: install, marketplace, versioning, documentation, and upgrade risks.
- `adversarial`: assumptions, simpler alternatives, hidden costs, and failure modes.

Use `--roles correctness,security` for an ordered comma-separated subset. Use repeated `--role` flags, such as `--role release --role adversarial`, when shell composition or incremental selection is clearer.

## Enhanced Adversarial Review

`adversarial-review` uses an intent-first verdict workflow inspired by cross-model adversarial review practice. Claude must infer the author's intent, review through the `skeptic`, `architect`, and `minimalist` lenses, and produce:

- `## Intent`
- `## Verdict: PASS | CONTESTED | REJECT`
- `## Findings`
- `## What Went Well`
- `## Lead Judgment`

Use lens selection when a review needs a narrower challenge:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --adversarial-lenses skeptic,minimalist --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --roles skeptic,architect,minimalist --base main
```

Use `adversarial-review --parallel` to run each selected adversarial lens as an independent Claude reviewer process and aggregate their outputs. `--json` remains available for the single-call adversarial verdict path and is intentionally not combined with parallel lens execution.

Use structured adversarial output when Codex needs a machine-checked verdict:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --json --base main
```

The adversarial JSON contract is `{ "verdict": "PASS|CONTESTED|REJECT", "summary": "...", "findings": [], "next_steps": [] }`. It intentionally keeps adversarial verdict semantics separate from normal `review --json`, which uses `approve|needs-attention`. Mixed or fenced Claude output is parsed and validated before being returned.

## Hooks And Background Jobs

`hooks/hooks.json` registers `SessionStart`, `SessionEnd`, `UserPromptSubmit`, and `Stop` hooks. Session hooks record the active session and attempt safe cleanup of tracked queued/running jobs at session end. The prompt-submit hook records a turn baseline fingerprint and emits a short stderr reminder for unread terminal job results.

If the local Codex runtime does not expose one of these hook events, the plugin degrades to explicit `jobs`, `result`, and `cancel` commands. The Stop hook remains opt-in through setup state. The review gate uses the `UserPromptSubmit` turn-baseline fingerprint to avoid reviewing old dirty workspace changes when the current turn did not change the working tree. Payload-based classification of status/setup/report-only Stop turns is intentionally deferred until a real Codex Stop payload exposes a verified edit/no-edit signal.

## Read-Only Git Boundary

Claude review commands expose `Read,Grep,Glob` only and pass a configured write-tool deny-list for recognized Claude runtime tools. Read-only Claude CLI review also passes `--disable-slash-commands`, `--no-session-persistence`, and an empty `--setting-sources` value so repository-local Claude settings, skills, hooks, slash commands, and sessions cannot add write side effects. Read-only Claude review also receives a strict MCP config for bounded Git inspection. The bundled read-only Git MCP server exposes status, diff, cached diff, log, show, blame, grep, and ls-files through validated Git arguments; unsupported paths, refs, and operations are rejected before Git is invoked.

## Stop Review Gate

The plugin includes `hooks/hooks.json` so Codex can discover a Stop hook. Do not add a `hooks` field to `.codex-plugin/plugin.json`; standard hook files are discovered from `hooks/hooks.json`, and declaring them in the manifest can fail validation or duplicate-load the hook.

The hook is installed but disabled by default. Enable it from the repository you want to protect:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --enable-review-gate --review-gate-mode multi-role
```

Disable it:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup --disable-review-gate
```

When enabled, Stop runs a multi-role Claude gate over current git working-tree changes. It blocks only when Claude explicitly returns `BLOCK:`. Claude CLI failures, timeouts, invalid output, missing auth, or missing Claude warn to stderr and allow Stop to continue.

The v1 gate reviews current git changes, not the exact files changed by the immediately previous Codex turn. It skips non-git directories, clean working trees, recursive Stop-hook invocations, unchanged turn-baseline fingerprints, and unchanged diffs that already received an all-`ALLOW:` gate result. Each Claude role has a two-minute timeout inside the overall 15-minute Stop hook budget.

Emergency bypass for the shell environment that launches Codex hooks:

```bash
export CLAUDE_FOR_CODEX_REVIEW_GATE=off
```

The setup output prints the per-workspace `stateFile`; deleting that file also resets the gate to disabled. After installing or upgrading, check Codex Settings > Hooks and trust or enable the `Claude for codex` Stop hook if prompted.

## Review Result Handling

Claude review output is a review artifact, not implementation authority. Preserve file paths, line numbers, role names, uncertainty markers, and residual-risk notes when reporting results. Do not auto-fix review findings in the same step unless the user explicitly asks which findings to adopt. If Claude fails, returns malformed structured output, or reports setup/auth problems, report that failure directly instead of substituting Codex guesses.

Tiny one-to-two file reviews can run foreground. Broader reviews, unclear scope, untracked directories, or multi-role/adversarial reviews should normally use `--background` and retrieve results with `claude-result`.

## Verification

Default tests use a fake Claude executable and do not require network or model access. They verify the read-only CLI arguments passed to Claude, but the actual installed Claude CLI is checked by the opt-in integration test below.

```bash
python3 -m pytest -q
```

Run the opt-in real Claude CLI compatibility check when preparing a release:

```bash
RUN_CLAUDE_INTEGRATION=1 python3 -m pytest tests/test_claude_for_codex_plugin.py::test_real_claude_permission_mode_when_enabled -q
```

## Release Checklist

1. Update `.codex-plugin/plugin.json` version.
2. Update `CHANGELOG.md`.
3. Run default tests: `python3 -m pytest -q`.
4. Run the real Claude CLI compatibility check: `RUN_CLAUDE_INTEGRATION=1 python3 -m pytest tests/test_claude_for_codex_plugin.py::test_real_claude_permission_mode_when_enabled -q`.
5. Run hook syntax validation: `node --check plugins/claude-for-codex/hooks/claude-review-gate.mjs`.
6. Run runtime syntax validation: `node --check plugins/claude-for-codex/scripts/claude-companion.mjs`.
7. Run plugin validation: `python3 "$HOME/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py" plugins/claude-for-codex`.
8. Run skill validation: `for d in plugins/claude-for-codex/skills/*; do python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" "$d"; done`.
9. Commit, tag, and push.
