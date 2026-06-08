# Changelog

## Unreleased

## 0.14.2 - 2026-06-08

### Added

- Add adaptive `--quality auto|fast|standard|strong|max` model/effort policy using Claude Code aliases instead of pinned concrete model IDs.
- Add Claude for Codex artwork based on the Claude.app icon and the actual Codex.app icon, with PNG manifest assets for the plugin page.
- Add release-check validation for manifest image assets.

### Fixed

- Propagate resolved effort into native SDK subagent definitions.
- Cover explicit high-quality manual `review-gate` escalation while keeping Stop hooks capped to conservative defaults.

### Documentation

- Document adaptive quality routing, `ultracode` CLI limitations, SDK native subagent behavior, and immutable `0.14.2` install refs.

## 0.14.1 - 2026-06-06

### Fixed

- Add runtime-compatible Claude write deny-list handling so read-only review retries safely when a Claude Code version rejects a configured deny candidate such as `MultiEdit` as unknown.
- Add `CLAUDE_FOR_CODEX_DENY_TOOLS` filtering for manual deny-list remediation without allowing unknown tool names to be passed through.
- Keep release checks from scanning local `docs/superpowers` planning artifacts.

## 0.14.0 - 2026-06-04

### Added

- Add native SDK subagent review teams for `multi-review --backend sdk --agent-team sdk-subagents`.
- Add package compatibility for `@anthropic-ai/claude-agent-sdk` while retaining the existing Claude SDK fallback path.
- Add native structured and sanitized streaming progress options with `--native-structured` and `--stream-progress`.
- Add `claude-ultrareview` skill and `ultrareview` runtime command for explicit Claude cloud ultrareview.

### Safety

- Keep SDK subagent mode explicit with `--backend sdk`; reject incompatible sequential and non-SDK combinations before Claude invocation.
- Preserve read-only SDK boundaries for native subagents and omit raw SDK messages from reports.
- Require `--confirm-cost` or `CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1` before ultrareview can run because it may use remote/cloud execution and usage-credit billing.

## 0.13.0 - 2026-06-04

- Rename the repository marketplace to `external-models-for-codex` so it can host multiple Codex plugins backed by external model CLIs.
- Add sanitized mailbox commands for review/job coordination summaries.
- Add advisory lease commands for declaring path attention without locking files or changing review verdicts.
- Add optional `multi-review --use-mailbox` and `--advisory-leases` coordination metadata.
- Add shared runtime summary sanitizer for secret, local path, control character, and UTF-8 byte-cap handling.

## 0.12.0 - 2026-06-04

- Add built-in Claude reviewer role packs with `roles list`, `roles inspect`, and `multi-review --role-pack`.
- Keep user-authored role packs validate/inspect-only; `--role-pack-file` is not executable in this release.
- Add schema and boundary validation for role-pack files, including forbidden tool/shell/hook/MCP/write fields and workspace/symlink rejection.
- Preserve the existing default multi-review and bare review-gate behavior while rejecting gate-incompatible explicit role packs.

## 0.11.0 - 2026-06-04

- Add opt-in `--backend sdk` execution for Claude review, structured review, multi-review, review gate, plan, and rescue flows.
- Keep CLI as the default backend and fail clearly when the SDK backend is explicitly requested but unavailable.
- Preserve read-only SDK safety with explicit allowed tools, write-tool denials, strict Git MCP config, SDK exception normalization, and sanitized SDK report metadata.
- Keep `rescue --write --backend sdk` explicit and preserve before/after git fingerprint reporting.

## 0.10.0 - 2026-06-04

- Add `github-actions render|init|validate` for fork-safe Claude PR review workflow templates.
- Add offline PR comment and Checks annotation rendering helpers with markdown, HTML, local-path, and annotation-path sanitization.
- Extend `release-check` with fixture-driven `--ci-simulate` validation for GitHub Actions workflow assumptions.
- Add `claude-github-actions-review` skill guidance for installing, validating, and reviewing generated workflows.
- Keep generated workflows on `pull_request` and validate against accidental `pull_request_target` usage.
- Pin generated workflow installs to immutable release refs instead of mutable `main`.

## 0.9.0 - 2026-06-04

- Add optional semantic context for `review`, `multi-review`, `adversarial-review`, and `review-gate`.
- Keep semantic context off by default and require explicit `--semantic-context`.
- Add repo-external provider config validation with argv-only commands, env isolation, permission checks, workspace path containment, and bounded execution.
- Add semantic metadata to sanitized reports without storing provider snippets or raw output.

## 0.8.0 - 2026-06-04

- Add `capabilities` diagnostics and include nested capability details in `setup`.
- Add sanitized per-run review reports plus `report --latest`.
- Add `release-check` for manifest, hook, docs, prompt, skill, secret-scan, and optional remote-install validation.
- Preserve explicit `rescue --write` while adding stronger baseline coverage for default read-only rescue behavior.

## 0.7.0 - 2026-06-03

- Add external prompt templates for Claude review, adversarial review, multi-review, plan, rescue, and review-gate prompts.
- Add shared structured review schema and renderer support for `review --json` and role-tagged `multi-review --json`.
- Preserve specialized `adversarial-review --json` verdicts (`PASS`, `CONTESTED`, `REJECT`) while documenting the separate schema.
- Tighten Stop gate documentation around the existing turn-baseline fingerprint boundary and defer payload-based edit classification until a real Codex Stop payload contract is available.
- Strengthen review result-handling guidance and foreground/background review-size guidance in skills and docs.

## 0.6.0 - 2026-06-03

- Add default parallel role execution for `multi-review` and opt-in parallel adversarial lens execution with `adversarial-review --parallel`.

## 0.5.0 - 2026-05-30

- Added host-forwarded background job reservations with `reserve-job` and `run-reserved-job`.
- Added a bundled read-only Git MCP server and strict Claude MCP config wiring for review flows.
- Added setup diagnostics for hook discovery/trust and MCP availability.
- Documented upgrade and rollback behavior for the forwarded-job and MCP review path.

## 0.4.0

- Add repo-external state modules with atomic writes and explicit corrupt-state reporting.
- Preserve existing runtime `status` diagnostics and add separate `jobs`, `result`, and `cancel` lifecycle commands with background worker persistence.
- Add process identity validation for running job cancellation.
- Add `rescue` runtime command and `claude-rescue` skill, with read-only default mode and explicit `--write` opt-in.
- Add `claude-status`, `claude-result`, and `claude-cancel` skills for tracked job lifecycle workflows.
- Add `SessionStart`, `SessionEnd`, and `UserPromptSubmit` hook scripts for session state, cleanup, turn baselines, and unread-result reminders.
- Add structured `adversarial-review --json` parsing and validation.
- Add bounded read-only git helper for plugin-owned git inspection without exposing Bash to Claude.
- Keep Stop gate fail-open on corrupt state while setup reports unreadable state as an error.
- Add rollback notes for disabling lifecycle hooks and returning to `0.3.x`.

- Expand Codex plugin page metadata with homepage, repository, developer URL, richer capabilities, and publishing prompts.
- Add logo, composer icon, and product-page screenshot assets.
- Add per-skill OpenAI agent metadata for clearer Codex skill presentation.
- Document publishing metadata, safety model, and hook trust guidance in the README.
- Document that Claude for Codex is a skills-and-hook plugin, not a `tool_search` callable tool.
- Add Claude CLI path fallback via `CLAUDE_CODE_PATH` and `~/.local/bin/claude` for Codex Desktop environments with reduced `PATH`.
- Strengthen `adversarial-review` with intent-first verdict output, Skeptic/Architect/Minimalist lenses, and Lead Judgment.
- Add `skeptic`, `architect`, and `minimalist` as opt-in `multi-review` roles.

## 0.3.0

- Add opt-in Stop-time Claude review gate hook.
- Add `review-gate` runtime command and `claude-review-gate` skill.
- Add repo-external per-workspace gate state with enable/disable setup flags.
- Add fail-open behavior for Claude CLI failures and invalid gate output.
- Document hook auto-discovery, settings trust, and emergency bypass.

## 0.2.0

- Add opt-in `multi-review` command.
- Add role-based Claude review fan-out.
- Add deterministic aggregation for multi-review output.
- Add partial-failure reporting for failed review roles.
- Add new `claude-multi-review` skill.

## 0.1.1

- Implement real `--scope auto|working-tree|branch` review behavior.
- Clarify no-HEAD and invalid-base `--base` handling in review context.
- Harden quoted argument parsing for Codex skill `$ARGUMENTS`.
- Add `status` command coverage.
- Add opt-in real Claude CLI integration test.
- Document remote marketplace install, upgrade, and release checks.

## 0.1.0

- Initial Claude-for-Codex plugin with read-only Claude review, adversarial review, planning, and collaboration loop skills.
