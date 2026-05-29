# Task Plan: Claude-for-Codex Review Plugin

## Goal
Build a Claude-for-Codex plugin package and workflow plan that mirrors OpenAI's Claude-side Codex plugin in the opposite direction: Codex can invoke Claude for adversarial review, planning, and complementary multi-model collaboration.

## Current Phase
Implementing Claude Stop review gate hook

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

### Phase 6: Hook Design Research
- [x] Inspect local review/security plugins for hook structures
- [x] Compare hook packaging between Claude-side and Codex-native plugins
- [x] Decide whether/how to add hook-like support to claude-for-codex safely
- **Status:** complete

### Phase 7: Stop Review Gate Hook
- [x] Add opt-in gate state and `review-gate` runtime command
- [x] Add plugin `hooks/hooks.json` and hook wrapper
- [x] Add fake-Claude gate behavior tests
- [x] Add skill and documentation for enabling/disabling the gate
- [x] Validate and prepare `0.3.0`
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
| Implement Stop gate as opt-in and fail-open on Claude runtime failure | The user chose multi-role blocking for explicit BLOCK results, but Claude availability/network failures should not wedge Codex Stop. |
| Use `hooks/hooks.json` without `plugin.json` hooks field | Local hook docs and existing plugins show standard hook files are auto-loaded; manifest `hooks` risks validation failure or duplicate load. |

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
- [x] Implement opt-in `multi-review` role fan-out orchestration.
- [x] Validate and prepare `0.2.0` release.
