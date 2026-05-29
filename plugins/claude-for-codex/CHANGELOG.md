# Changelog

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
