# Claude Multi-Agent Orchestration Implementation Plan

> **Recommended execution skill:** `superpowers:subagent-driven-development` for implementation, with one worker per task and spec/quality review after each task.

## Goal

Add an opt-in Claude multi-agent review workflow to `claude-for-codex` while preserving the existing single-call `review`, `adversarial-review`, `plan`, and `status` behavior by default.

## Decision

Implement v1 as **role-based Claude prompt fan-out**, not Claude native background-agent orchestration.

Rationale:
- Current runtime uses one synchronous `claude --print` call per task and already centralizes read-only permissions in `claudePrint`.
- Claude native background agents are exposed only through `claude agents --json --cwd` in this plugin today; there is no existing dispatch API in the runtime.
- Role fan-out is easier to test deterministically with fake Claude, keeps output aggregation under Codex control, and avoids changing current commands.

Future work can add a native Claude-agent backend once the dispatch contract is proven.

## Target User Experience

Existing commands stay unchanged:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review --base main
```

New opt-in command:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --roles correctness,security,release --base main
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --role tests --role adversarial --scope working-tree
```

New skill:

```text
claude-multi-review
```

## Role Set

Default roles:
- `correctness`: bugs, regressions, edge cases, behavioral contract breaks.
- `security`: read-only safety, secrets, injection, unsafe command or path handling.
- `tests`: missing, brittle, or overfit tests; release validation gaps.
- `release`: install, marketplace, versioning, docs, upgrade risks.
- `adversarial`: assumptions, simpler alternatives, hidden costs, failure modes.

Optional later roles:
- `performance`
- `docs`
- `api`

## Architecture

### Runtime Additions

Modify `plugins/claude-for-codex/scripts/claude-companion.mjs`.

Add:
- `multi-review` to `VALID_COMMANDS` and switch dispatch.
- `REVIEW_ROLES` registry.
- `DEFAULT_MULTI_REVIEW_ROLES`.
- `--roles <comma,list>` parsing.
- repeatable `--role <name>` parsing.
- role validation with exit code `2` and valid-role list.
- `multiReviewPrompt(role, args, gitContext)` for role-specific prompts.
- `runMultiReview(rawArgs)` for orchestration.

Preserve:
- `claudePrint` as the only Claude invocation function.
- prompt as the last Claude CLI argument.
- read-only flags: `--tools Read,Grep,Glob`, `--disallowedTools Edit,Write,MultiEdit,Bash`.
- existing single-agent command prompt text by default.

### Git Context

Collect git context once per `multi-review` command, then inject the same context into each role prompt. This avoids N-times git overhead and keeps role comparisons consistent.

Implementation note:
- Extract a reusable helper that validates scope/base and returns normalized args.
- Add a variant that can pass precomputed `gitContext` into review prompt construction.

### Execution Strategy

Use sequential execution for v1:

1. Parse and validate args.
2. Resolve role list.
3. Collect git context once.
4. For each role:
   - build role-specific prompt,
   - call `claudePrint`,
   - store status/stdout/stderr.
5. Print deterministic aggregation.
6. Exit `0` only if all roles returned `0`; otherwise print all completed sections and exit non-zero.

Do not abort after the first failed role. This prevents a transient failure from hiding successful role output.

### Aggregated Output

Runtime-side deterministic aggregation:

```markdown
# Claude Multi-Agent Review

## Role: correctness
...

## Role: security
...

## Role: tests
...

## Orchestration Summary
- roles requested: correctness, security, tests
- roles succeeded: ...
- roles failed: ...
- exit policy: non-zero if any role failed
```

No Claude merge pass in v1. A model-based synthesis pass would increase cost and make tests non-deterministic.

## Implementation Tasks

### Task 1: Add Role Parsing And Validation

Files:
- `plugins/claude-for-codex/scripts/claude-companion.mjs`
- `tests/test_claude_for_codex_plugin.py`

Steps:
1. Add `REVIEW_ROLES` and `DEFAULT_MULTI_REVIEW_ROLES`.
2. Parse `--roles` and repeatable `--role`.
3. Reject missing values using existing `readOptionValue`.
4. Reject unknown roles before invoking Claude.
5. Add tests:
   - unknown role exits `2` and fake Claude is not called;
   - `--roles correctness,security` resolves in order;
   - `--role correctness --role tests` accumulates in order;
   - missing `--roles`/`--role` exits `2`.

### Task 2: Add Multi-Review Orchestration

Files:
- `plugins/claude-for-codex/scripts/claude-companion.mjs`
- `tests/test_claude_for_codex_plugin.py`

Steps:
1. Add `multi-review` command.
2. Collect git context once.
3. Build role prompts with role directives and existing read-only output contract.
4. Invoke Claude once per role, sequentially.
5. Aggregate role outputs deterministically.
6. Add tests:
   - default roles run once each;
   - role headers appear in declared order;
   - each call keeps read-only Claude flags;
   - `--model` and `--effort` propagate to every role;
   - scope/base behavior is honored for all role prompts.

### Task 3: Add Partial-Failure Semantics

Files:
- `plugins/claude-for-codex/scripts/claude-companion.mjs`
- `tests/test_claude_for_codex_plugin.py`

Steps:
1. Fake Claude can fail for one selected role.
2. Runtime continues remaining roles.
3. Aggregated output includes successful role sections and failed role diagnostics.
4. Overall process exits non-zero when any role fails.
5. Add tests for one failed role and all-success path.

### Task 4: Add Skill And Documentation

Files:
- `plugins/claude-for-codex/skills/claude-multi-review/SKILL.md`
- `plugins/claude-for-codex/README.md`
- `docs/claude-for-codex-workflow.md`
- `tests/test_claude_for_codex_plugin.py`

Steps:
1. Add `claude-multi-review` skill.
2. Update exact skill set assertion to include the new skill.
3. Document command examples and role list in README.
4. Update workflow guide: use `multi-review` for high-stakes release/security tasks; keep normal `adversarial-review` as default.
5. Validate skill frontmatter.

### Task 5: Release Versioning

Files:
- `plugins/claude-for-codex/.codex-plugin/plugin.json`
- `plugins/claude-for-codex/CHANGELOG.md`
- `tests/test_claude_for_codex_plugin.py`

Steps:
1. Bump version to `0.2.0`.
2. Update version test.
3. Add `CHANGELOG.md` entry:
   - add opt-in `multi-review`;
   - add role-based Claude review fan-out;
   - add deterministic aggregation and partial-failure reporting.

### Task 6: Final Validation And Review

Run:

```bash
python3 -m pytest -q
RUN_CLAUDE_INTEGRATION=1 python3 -m pytest tests/test_claude_for_codex_plugin.py::test_real_claude_permission_mode_when_enabled -q
node --check plugins/claude-for-codex/scripts/claude-companion.mjs
node plugins/claude-for-codex/scripts/claude-companion.mjs setup
python3 "$HOME/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py" plugins/claude-for-codex
for d in plugins/claude-for-codex/skills/*; do python3 "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" "$d"; done
```

Then run:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs multi-review --roles correctness,security,release --path plugins/claude-for-codex --path tests
```

Accept only verified findings before final commit.

## Test Matrix

Required tests:
- existing `review`, `adversarial-review`, `plan`, `status` still pass unchanged;
- `multi-review` command exists and usage includes it;
- default role set invokes fake Claude N times;
- `--roles` comma list selects exact role order;
- repeatable `--role` accumulates exact role order;
- unknown role exits `2` without calling Claude;
- missing `--roles` and missing `--role` exit `2`;
- each role prompt contains the shared git context and role directive;
- read-only flags are present for every Claude invocation;
- prompt remains the last Claude CLI argument;
- one role failure produces non-zero exit but includes successful role sections;
- `--scope branch --base HEAD~1` omits working-tree context in every role prompt;
- repeated `--path` filters every role prompt;
- new `claude-multi-review` skill has valid frontmatter and runtime call;
- manifest version test expects `0.2.0`.

## Risks

- **Terminology risk:** Users may expect Claude native background agents. This plan implements role fan-out. Document this clearly as v1 behavior.
- **Cost/latency:** N roles means N Claude calls. Default role count should stay small.
- **Hangs:** Existing `spawnSync` has no timeout. Add a future `--timeout-ms` if real-world calls hang.
- **Aggregation size:** Large role output can approach the current 20 MB buffer. Keep role count bounded.
- **False consensus:** Multiple role prompts may duplicate the same finding. Runtime aggregation should not claim deduplication; Codex still reconciles.
- **Versioning:** This is a feature release; use `0.2.0`, not a patch bump.

## Non-Goals For v1

- No native Claude background-agent dispatch.
- No parallel execution.
- No Claude merge/synthesis pass.
- No automatic code fixes from any role output.
- No change to existing command defaults.

## Open Question

If the intended meaning of "Claude multi agent" is specifically Claude Code native background sessions rather than role-based multiple `claude --print` calls, implementation should stop before Task 1 and first research the supported native dispatch surface. The recommended v1 remains role fan-out because it is testable and minimally invasive.
