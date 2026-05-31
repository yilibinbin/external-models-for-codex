# Changelog

## Unreleased

- Rename the local repository marketplace to `external-models-for-codex-local` so it can host multiple Codex plugins backed by external model CLIs.

## 0.1.0 - 2026-05-31

- Initial Gemini for Codex plugin.
- Added read-only Gemini CLI review, adversarial review, planning, rescue diagnosis, and multi-role review.
- Added tracked job commands and Codex skills for status, result, and cancellation.
- Added opt-in Stop review gate that fails open unless Gemini explicitly returns `BLOCK:`.
- Uses bounded inline git context; Gemini MCP and native extension packaging are deferred.
