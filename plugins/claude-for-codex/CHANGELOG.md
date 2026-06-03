# Changelog

## Unreleased

- Rename the local repository marketplace to `external-models-for-codex-local` so it can host multiple Codex plugins backed by external model CLIs.

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
