# Changelog

## 0.5.4 - 2026-06-09

- Resolve generated GitHub Actions workflows through the installed Antigravity plugin root instead of repo-relative runtime paths.
- Allow `release-check` to run from an installed plugin cache where repository-level README/docs files are absent.
- Add release guards for installed-plugin release checks and workflow plugin-root resolution.

## 0.5.3 - 2026-06-08

- Reframe Antigravity skills around natural-language model routing so users can ask for review, planning, rescue, and Claude-through-Antigravity without writing internal CLI flags.
- Add release-check and pytest guards that preserve Gemini-default provider behavior, explicit Claude-through-Antigravity selection, and rejection of GPT/OpenAI model labels.
- Keep existing `agy` invocation, model validation, hooks, workflows, and safety boundaries unchanged.

## 0.5.2 - 2026-06-08

- Replace the Antigravity plugin logo and composer icon with a dual-tile Antigravity + Codex joint-brand design.
- Keep existing `agy` behavior, model-provider selection, hooks, workflows, release checks, and safety boundaries unchanged.

## 0.5.1 - 2026-06-08

- Refresh the Antigravity plugin logo and composer icon using the official Antigravity app arch mark as the base visual element.
- Keep existing `agy` behavior, model-provider selection, hooks, workflows, release checks, and safety boundaries unchanged.

## 0.5.0 - 2026-06-08

- Promote Antigravity for Codex to the mature plugin-managed workflow surface: structured review output, sanitized reports, role packs, background jobs, status/result/cancel, mailbox, leases, lifecycle hooks, GitHub Actions workflow rendering and validation, release checks, and opt-in real smoke.
- Document the explicit boundary that Antigravity for Codex uses `agy` only and does not claim Claude SDK, Gemini native-agent, or ultrareview parity.
- Clarify that Claude-through-Antigravity is an explicit Antigravity model-provider choice and remains separate from `claude-for-codex`.
- Document that CI review workflows require an authenticated `agy` command and that real smoke remains opt-in.
- Versions 0.2.0 through 0.4.0 were internal pre-release iterations and were not published as standalone marketplace releases.

## 0.1.0 - 2026-06-07

- Initial Antigravity for Codex plugin.
- Document the local Antigravity CLI (`agy`) requirement and supported discovery through `agy`, `AGY_CLI_PATH`, or `ANTIGRAVITY_CLI_PATH`.
- Add explicit Gemini/Claude model-provider switching, including Claude-through-Antigravity model selection.
- Reject GPT/OpenAI model labels as unsupported for this plugin.
- Cover the initial command surface: `setup`, `capabilities`, `review`, `adversarial-review`, `multi-review`, `plan`, `rescue`, `review-gate`, `real-smoke`, and `release-check`.
