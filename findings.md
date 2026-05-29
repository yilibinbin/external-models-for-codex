# Findings & Decisions

## Requirements
- Build a Claude-for-Codex plugin/workflow package inspired by OpenAI's official `openai/codex-plugin-cc`.
- Improve the user's existing adversarial-review skill/process.
- Research official Claude Code command/plugin/skill surfaces.
- Add Claude planning workflows, not only review, so Codex and Claude can cross-check each other's blind spots.

## Research Findings
- Official `openai/codex-plugin-cc` provides `/codex:review`, `/codex:adversarial-review`, `/codex:rescue`, `/codex:status`, `/codex:result`, and `/codex:cancel`, plus a `codex:codex-rescue` subagent and an optional Stop hook review gate.
- Official `codex-plugin-cc` installation flow is Claude-side plugin marketplace commands: `/plugin marketplace add openai/codex-plugin-cc`, `/plugin install codex@openai-codex`, `/reload-plugins`, then `/codex:setup`.
- Official `codex-plugin-cc` review distinction: normal review is read-only and not steerable; adversarial review is read-only but accepts focus text and pressure-tests assumptions, tradeoffs, failure modes, and alternatives.
- Claude Code official plugin structure: `.claude-plugin/plugin.json` at plugin root, with root-level `skills/`, `commands/`, `agents/`, `hooks/`, `.mcp.json`, `.lsp.json`, `monitors/`, `bin/`, and `settings.json`. Do not put those directories inside `.claude-plugin/`.
- Claude Code plugin skills are namespaced as `/plugin-name:skill-name`.
- Claude Code skills support frontmatter including `description`, `when_to_use`, `argument-hint`, `disable-model-invocation`, `allowed-tools`, `disallowed-tools`, `model`, `effort`, `context`, `agent`, `hooks`, `paths`, and `shell`.
- Claude Code CLI supports `claude -p "query"` for non-interactive print mode, `claude -c -p` to continue the latest conversation, `claude agents --json` for live sessions, `claude plugin`/`claude plugins` for plugin management, and flags including `--permission-mode`, `--allowedTools`, `--disallowedTools`, `--tools`, `--output-format`, and `--plugin-dir`.
- Claude Code hooks include read-only `/hooks` inspection and event hooks such as `Stop`, `SubagentStop`, `PostToolUse`, `PreToolUse`, `UserPromptSubmit`, `TaskCompleted`, and `TaskCreated`, with command/prompt/agent/http/mcp_tool variants depending on event.
- Local `claude plugin validate --strict research/codex-plugin-cc/plugins/codex` passed against the official reference plugin manifest.
- Local `claude --help` confirms the CLI exposes `--print`, `--permission-mode`, `--tools`, `--allowedTools`, `--disallowedTools`, `--output-format`, `--model`, `--effort`, `--plugin-dir`, and `agents` / `plugin` subcommands.
- Local `claude agents --help` confirms `claude agents --json --cwd <path>` is available for scriptable background-session status.
- Codex plugin creation uses `.codex-plugin/plugin.json`, not Claude's `.claude-plugin/plugin.json`.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Start with a local plugin package instead of publishing | Publication/marketplace submission can be added later; the user asked for a plugin and workflow, and local validation is the fastest reliable target. |
| Prefer skill-based commands over flat `commands/` | Official Claude docs recommend `skills/` for new plugins and plugin skills get namespaced commands automatically. |
| Enforce read-only Claude review by tool restrictions | Runtime invokes Claude with `Read,Grep,Glob` only and disallows `Edit,Write,MultiEdit,Bash`, preventing shell-based mutation during reviews/plans. |
| Split single `$ARGUMENTS` token in runtime | Codex skill snippets pass `"$ARGUMENTS"` as one shell token; runtime now normalizes this so flags such as `--base`, `--path`, `--model`, and `--effort` parse correctly. |
| Use repo root for Codex marketplace add | Current Codex CLI accepts `codex plugin marketplace add .` for this repo-local marketplace layout, not the direct `.agents/plugins/marketplace.json` file path. |
| Implement Claude multi-agent v1 as role fan-out | Current runtime has one safe `claude --print` path and no native dispatch API; role fan-out is deterministic, testable, and preserves existing single-agent commands. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Official Claude docs pages are rendered through a JS-heavy Mintlify site, making raw `curl` output noisy | Used the cloned official repository, local Claude CLI help, and official docs URLs as primary evidence. |
| `plugin-creator` was not at the shorthand non-system path | Opened `/Users/fanghao/.codex/skills/.system/plugin-creator/SKILL.md` and used its Codex plugin manifest guidance. |
| Final review found quoted `$ARGUMENTS` did not parse as separate flags | Added shell-like single-argument splitting and behavioral test coverage. |
| Final review found the test file was not discovered by default pytest | Renamed the suite to `tests/test_claude_for_codex_plugin.py` and verified default `pytest` runs it. |

## Claude Hardening Review Findings
- Confirmed: `--permission-mode dontAsk`, `--tools`, `--disallowedTools`, and `--effort` are valid in the installed Claude CLI; no fix needed for those flags.
- Fix needed: `--scope` is currently parsed and printed but does not change git context selection.
- Fix needed: no-HEAD repositories with `--base` fall back to status context without explicitly saying the requested base is unavailable.
- Fix needed: `status` command lacks test coverage.
- Fix needed: real Claude CLI behavior should have an opt-in integration test so future flag changes are caught.
- Fix needed: release/upgrade process needs `CHANGELOG.md`, version bump, and documented validation steps.
- Non-breaking decision: keep marketplace id `claude-for-codex-local` for compatibility, but document it as the stable id even for remote GitHub installs.
- Additional quality finding: with an existing HEAD but invalid `--base`, the prompt previously reported the requested base as effective. Fixed by validating the base ref and marking it unavailable before branch diff collection.
- Additional parser finding: unmatched quoted argument strings were silently accepted. Fixed by failing argument normalization before Claude invocation.
- Additional parser finding: empty quoted option values were dropped, allowing the next focus token to become the option value. Fixed by preserving empty quoted tokens and rejecting empty option values.
- Final Claude adversarial review residual risk: default pytest uses fake Claude for speed and determinism; real installed Claude CLI compatibility is covered by the opt-in release gate, not the default suite.
- Final Claude adversarial review documentation fix: marketplace id and GitHub owner are compatibility assumptions, so README now says they must be updated together if the remote or marketplace id changes.

## Claude Multi-Agent Planning Findings
- Existing Claude review commands are single-call `claude --print` flows through `claudePrint`; they do not spawn or coordinate multiple Claude agents.
- Recommended v1 multi-agent implementation is an opt-in `multi-review` command that runs multiple role-specialized Claude calls sequentially and aggregates results deterministically.
- Existing command defaults should remain unchanged to avoid surprising users and to preserve current tests.
- Adding a new `claude-multi-review` skill requires updating `test_all_skills_have_frontmatter_and_runtime_call`, which currently asserts the exact four-skill set.
- The largest ambiguity is terminology: role fan-out is not Claude Code native background-agent orchestration. Native agent dispatch should be researched separately if that is the desired target.

## Local Hook Research Findings
- `@codex` is a Claude-side plugin cache entry under `openai-codex/codex/1.0.4`; it includes a root-level `hooks/hooks.json` plus scripts such as `stop-review-gate-hook.mjs` and `session-lifecycle-hook.mjs`.
- CodeRabbit and Codex Security local Codex-native plugins expose skills/assets/scripts but no hook directory in the inspected cache entries.
- Current local `plugin-creator` guidance for Codex plugins says to omit unsupported manifest fields including `hooks` from `.codex-plugin/plugin.json`. Later local evidence clarifies the nuance: Codex App can still auto-discover root-level `hooks/hooks.json` from enabled plugins; the unsupported part is declaring `hooks` explicitly in `.codex-plugin/plugin.json`.
- `@codex` hook events: `SessionStart`, `SessionEnd`, and `Stop`. `SessionStart/SessionEnd` manage per-session env/state and broker cleanup; `Stop` optionally runs a Codex stop-gate review.
- `@codex` stop review gate is opt-in. The hook is always registered in the Claude plugin, but the script exits early unless `config.stopReviewGate` is true. `/codex:setup --enable-review-gate` toggles that state.
- `@codex` Stop hook reads hook JSON from stdin, uses `cwd`, `session_id`, and `last_assistant_message`, checks for running Codex jobs in the current session, then either prints a stderr note or emits JSON `{ "decision": "block", "reason": "..." }`.
- The stop-gate prompt requires first-line `ALLOW:` or `BLOCK:`. Non-empty unexpected/invalid output is treated as a block to force manual review or bypass.
- The `@codex` design intentionally limits gate scope to the immediately previous Claude turn and says to allow immediately when that turn did not produce code edits.
- CodeRabbit uses a skill wrapper around `coderabbit review --agent`, with auth/install handling and NDJSON output parsing guidance; no automatic stop hook was found.
- Codex Security uses phased skill orchestration, artifacts, goals, and validation scripts; no automatic hook was found.
- Codex global hook state exists in `~/.codex/config.toml` under `[hooks.state]`, including entries such as `codex@openai-codex:hooks/hooks.json:session_start:0:0` and `codex@openai-codex:hooks/hooks.json:stop:0:0`.
- User-level hooks also exist at `~/.codex/hooks.json` and `~/.codex/hooks/hooks.json`; the settings UI screenshot corresponds to this global hook management layer.

## Stop Review Gate Implementation Findings
- `claude-for-codex` now ships a standard `hooks/hooks.json` Stop hook without adding a `hooks` field to `.codex-plugin/plugin.json`.
- The gate is opt-in and stores per-workspace state outside the repo, using `CLAUDE_PLUGIN_DATA/state/...` when available and `~/.codex/claude-for-codex/state/...` otherwise.
- The enabled gate reviews current git working-tree changes, not the exact previous-turn edit set.
- The gate blocks only on explicit Claude `BLOCK:` verdicts; Claude failures, timeouts, invalid output, and missing runtime fail open with stderr warnings.
- The hook command uses `${CLAUDE_PLUGIN_ROOT:-$CODEX_PLUGIN_ROOT}` to tolerate both root-variable names in hook environments.
- The gate caches the last all-ALLOW working-tree fingerprint so unchanged diffs do not trigger repeated multi-role review.

## Resources
- https://github.com/openai/codex-plugin-cc
- https://code.claude.com/docs/en/plugins
- https://code.claude.com/docs/en/commands
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/slash-commands
- https://code.claude.com/docs/en/hooks
