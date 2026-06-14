# Changelog

## 0.12.0 - 2026-06-14

- Add normalized scorecard review contracts for `review`, plugin-managed `multi-review`, `adversarial-review`, and `plan-review`.
- Add `plan --taskset`, `plan-review`, and bounded advisory `assisted-review` quality-loop commands.
- Add workspace-bound plan-file reading, repo-external taskset state, validation-evidence blocks, project-instruction advisory context, and round summary indexes.
- Harden Gemini CLI script-wrapper execution by resolving POSIX shebang scripts through their interpreters before spawning.
- Add quality-loop skills, release-check guards, and fake-CLI regression tests.

## 0.11.3 - 2026-06-13

- Add a file-backed global resource governor for foreground reviews, Stop gates, multi-review fan-out, background jobs, and reserved workers.
- Add bounded spawn retry handling for transient local process pressure (`EAGAIN`, `EMFILE`, `ENFILE`, `ENOBUFS`).
- Add release-check and pytest coverage for resource-governor and spawn-retry safety.

## 0.11.2 - 2026-06-08

- Replace the Gemini plugin logo and composer icon with a dual-tile Gemini + Codex joint-brand design.
- Keep existing Gemini CLI behavior, hooks, workflows, and safety boundaries unchanged.

## 0.11.1 - 2026-06-08

- Refresh the Gemini plugin logo and composer icon using the official Gemini app sparkle mark as the base visual element.
- Keep existing Gemini CLI behavior, hooks, workflows, and safety boundaries unchanged.

## 0.11.0 - 2026-06-07

- Restore Gemini for Codex as a Gemini CLI-only plugin.
- Move Antigravity CLI workflows to the separate Antigravity for Codex plugin.
- Keep existing Gemini CLI review, native-agent, real-smoke, hook, and GitHub Actions behavior unchanged.

## 0.10.2 - 2026-06-07

- Fix command help handling so `multi-review --help` and other command help requests return usage without invoking Gemini.
- Add regression coverage that help output does not start external Gemini review calls.

## 0.10.1 - 2026-06-07

- Harden all Gemini plugin Git subprocess probes with bounded timeouts and explicit `SIGKILL` timeout cleanup.
- Improve real-smoke Git fixture cleanup and timeout diagnostics.
- Expand `release-check` coverage for Git subprocess timeout constants, `killSignal` enforcement, and source-shape drift.

## 0.10.0 - 2026-06-05

- Add opt-in real Gemini smoke diagnostics for `review --json`, plugin-managed `multi-review --stream-progress`, native-agent structured review, and capability reporting.
- Split real smoke into lightweight `--quick` and heavier `--full` profiles. Quick smoke now validates install/runtime plumbing without native-agent checks; use `--full` or `--include-native` to include native structured diagnostics.
- Add real-smoke controls `--model`, `--roles`, `--timeout-seconds`, `--include-native`, plus `GEMINI_FOR_CODEX_REAL_SMOKE_MODEL` / `GEMINI_FOR_CODEX_MODEL` model selection.
- Expand Gemini CLI capability diagnostics for stream JSON, sessions, extensions, MCP, skills, hooks, policy flags, and raw-output support without enabling those surfaces by default.
- Add release-check guards that keep raw-output, extension/MCP execution, native-agent orchestration, native structured output, and stream progress out of Stop hooks and generated default CI.
- Add an evaluation-only Gemini extension/MCP design note for future opt-in work.

## 0.9.0 - 2026-06-05

- Add explicit Gemini review-team selection with `multi-review --agent-team plugin|native-agents`; existing `--native-agents` remains a compatibility alias.
- Add `multi-review --agent-team native-agents --native-structured` for validated native-agent aggregate JSON output.
- Add `multi-review --stream-progress` lifecycle progress events on stderr without raw Gemini chunks, prompts, diffs, or provider output.
- Add release-check guards to keep native-agent orchestration opt-in and out of hooks and default GitHub Actions workflows.

## 0.8.0 - 2026-06-04

- Add sanitized mailbox coordination with `mailbox list`, `mailbox show`, and `mailbox post`.
- Add advisory path leases with `leases list`, `leases claim`, and `leases release`.
- Add `multi-review --use-mailbox` and `multi-review --advisory-leases` for opt-in coordination metadata.
- Keep native-agent mailbox reporting aggregate-only and keep leases advisory; neither feature affects review or Stop gate verdicts.

## 0.7.0 - 2026-06-04

- Add built-in Gemini reviewer role packs with `roles list`, `roles inspect`, and `roles validate`.
- Add `multi-review --role-pack` for built-in review presets and support role packs with Gemini native-agent dispatch.
- Keep user-authored role packs validate/inspect-only; `--role-pack-file` is not executable in this release.
- Preserve the existing default multi-review and bare review-gate behavior while rejecting gate-incompatible explicit role packs.

## 0.6.0 - 2026-06-04

- Add `review --json` for machine-readable, schema-validated review output while preserving `review --structured` as rendered Markdown.
- Add `github-actions render/init/validate/render-comment/render-annotations` for fork-safe PR review workflow templates.
- Add `release-check --ci-simulate` to validate the generated workflow without real GitHub access.
- Generate workflows that install Codex CLI, pin `gemini-for-codex-v0.6.0`, skip fork PR Gemini execution by default, and optionally publish Checks annotations.

## 0.5.0 - 2026-06-04

- Add `capabilities`, sanitized `report`, and `release-check` runtime commands.
- Add optional bounded context-provider enrichment for `review`, `adversarial-review`, `multi-review`, native-agent dispatch, and manual `review-gate`.
- Keep provider execution opt-in, repo-external, no-shell, env-allowlisted, byte-limited, timeout-bounded, and advisory-only in prompts.
- Add context metadata to reports without storing provider stdout, stderr, prompts, source, diffs, config, or raw workspace paths.
- Gate Gemini native `--include-directories` forwarding on the installed CLI help output.

## 0.3.0 - 2026-06-03

- Add SessionStart, SessionEnd, and UserPromptSubmit hooks for session tracking, conservative same-session cleanup, turn baselines, and unread result reminders.
- Move Gemini review prompts into plugin-local prompt templates and add schema-backed `review --structured` output validation.
- Add `recommend-execution-mode` for noninteractive foreground/background sizing guidance.
- Add Gemini session capability reporting plus explicit `--resume`, `--session-id`, `--worktree`, and `sessions` handling gated by current Gemini CLI help output.
- Keep all Gemini execution read-only through plan mode.

## 0.2.0 - 2026-06-03

- Run `multi-review` role fan-out in parallel Gemini CLI invocations by default.
- Add `multi-review --native-agents` to create temporary Gemini subagent definitions and ask Gemini CLI to dispatch `@gfc_*` native subagents for the requested review roles.
- Document the difference between plugin-managed parallel role fan-out and Gemini native subagent orchestration.

## 0.1.1 - 2026-06-02

- Rename the repository marketplace to `external-models-for-codex` so it can host multiple Codex plugins backed by external model CLIs.
- Fix Gemini plugin metadata URLs to point at the hosting repository.
- Harden Gemini Stop hook stdin handling and foreground command timeout behavior.
- Reject `--roles` outside `multi-review`.
- Expand Gemini CLI discovery beyond `PATH` to cover common user, JavaScript toolchain, package-manager, and system install locations while preserving `GEMINI_CLI_PATH` as the highest-priority override.

## 0.1.0 - 2026-05-31

- Initial Gemini for Codex plugin.
- Added read-only Gemini CLI review, adversarial review, planning, rescue diagnosis, and multi-role review.
- Added tracked job commands and Codex skills for status, result, and cancellation.
- Added opt-in Stop review gate that fails open unless Gemini explicitly returns `BLOCK:`.
- Uses bounded inline git context; Gemini MCP and native extension packaging are deferred.
