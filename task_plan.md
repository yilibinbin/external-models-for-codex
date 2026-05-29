# Task Plan: Claude-for-Codex Review Plugin

## Goal
Build a Claude-for-Codex plugin package and workflow plan that mirrors OpenAI's Claude-side Codex plugin in the opposite direction: Codex can invoke Claude for adversarial review, planning, and complementary multi-model collaboration.

## Current Phase
Planning Claude multi-agent orchestration

## Phases

### Phase 1: Requirements & Discovery
- [x] Understand user intent
- [x] Identify constraints
- [x] Document initial research in findings.md
- **Status:** complete

### Phase 2: Planning & Structure
- [x] Define plugin architecture and workflow commands
- [x] Create implementation plan under docs/superpowers/plans/
- **Status:** complete

### Phase 3: Implementation
- [x] Create plugin/skill package files
- [x] Add validation scripts and documentation
- **Status:** complete

### Phase 4: Testing & Verification
- [x] Validate manifests and skill frontmatter
- [x] Smoke-test local command wrappers where possible
- [x] Document test results
- **Status:** complete

### Phase 5: Delivery
- [x] Review outputs
- [x] Deliver to user
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use Claude Code plugin packaging as the model | The user explicitly asked to reference OpenAI's `codex-plugin-cc`; official Claude docs define reusable plugins via `.claude-plugin/plugin.json` and root-level `skills/`, `agents/`, `hooks/`, and `bin/`. |
| Add both review and planning workflows | User requested stronger adversarial review plus extra Claude planning coverage for multi-model complementarity. |
| Implement as a Codex-native plugin with Claude CLI runtime | The requested direction is "Claude for Codex"; Codex plugin manifests use `.codex-plugin/plugin.json`, while Claude can be invoked through the official `claude -p` non-interactive CLI surface. |
| Remove Bash from Claude review/planning tools | Read-only review must be enforced by CLI permissions, not only prompt wording. |
| Make tests discoverable by default pytest | Final review found the original `validate_*.py` filename was not discovered by default pytest. |
| Validate requested base refs before calling them effective | A with-HEAD repo can still have a typoed `--base`; prompt metadata must not tell Claude that an unusable ref is effective. |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| `session-catchup.py` reports Codex session parsing is not implemented | Proceeded from freshly initialized planning files and current git state. |
| Initial plugin-creator path lookup used `~/.codex/skills/plugin-creator/SKILL.md`, which does not exist | Read the actual system skill at `~/.codex/skills/.system/plugin-creator/SKILL.md`. |
| Git commits failed because user.name/user.email are not configured | Staged all final files and reported commit skipped. |

## Hardening Follow-Up
- [x] Implement real `--scope` behavior.
- [x] Make no-HEAD `--base` behavior explicit.
- [x] Add status and real Claude integration tests.
- [x] Add release/upgrade documentation and `0.1.1` changelog.
- [x] Validate release candidate.
- [x] Tag and push `v0.1.1`.
- [x] Test Claude prompt hit behavior across text sizes and argument cases.
- [x] Plan opt-in Claude multi-agent orchestration for a future feature release.
