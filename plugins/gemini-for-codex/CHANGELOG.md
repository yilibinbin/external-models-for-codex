# Changelog

## 0.1.1 - 2026-06-02

- Rename the local repository marketplace to `external-models-for-codex-local` so it can host multiple Codex plugins backed by external model CLIs.
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
