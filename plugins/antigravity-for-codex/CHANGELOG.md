# Changelog

## 0.1.0 - 2026-06-07

- Initial Antigravity for Codex plugin.
- Document the local Antigravity CLI (`agy`) requirement and supported discovery through `agy`, `AGY_CLI_PATH`, or `ANTIGRAVITY_CLI_PATH`.
- Add explicit Gemini/Claude model-provider switching, including Claude-through-Antigravity model selection.
- Reject GPT/OpenAI model labels as unsupported for this plugin.
- Cover the initial command surface: `setup`, `capabilities`, `review`, `adversarial-review`, `multi-review`, `plan`, `rescue`, `review-gate`, `real-smoke`, and `release-check`.
