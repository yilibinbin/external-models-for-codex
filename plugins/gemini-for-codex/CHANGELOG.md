# Changelog

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
