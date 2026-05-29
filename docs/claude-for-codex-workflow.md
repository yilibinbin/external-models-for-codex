# Claude-for-Codex Review And Planning Workflow

## Default Loop

1. Codex reconstructs local state from files, git, and planning artifacts.
2. Codex writes its own plan.
3. Codex invokes Claude planning with `claude-plan`.
4. Codex reconciles the two plans:
   - adopt concrete missing tests,
   - adopt real risk checks,
   - reject unsupported assumptions,
   - log decisions in `findings.md`.
5. Codex implements.
6. Codex runs local verification.
7. Codex invokes `claude-adversarial-review`.
8. Codex either fixes user-approved findings or reports findings as pending.

## When To Use Normal Review

Use `claude-review` for ordinary patch review after implementation, especially when the change is small and the main question is correctness.

## When To Use Adversarial Review

Use `claude-adversarial-review` when the main risk is direction:

- security-sensitive code,
- data loss or migration risk,
- concurrency,
- rollback strategy,
- major abstraction changes,
- performance claims,
- tests that may be overfit to implementation.

## When To Use Claude Planning

Use `claude-plan` before editing when:

- the request spans multiple modules,
- the repo conventions are unclear,
- there are multiple plausible designs,
- tests are hard to choose,
- previous Codex attempts got stuck.

## Reporting Contract

Final Codex reports should include:

- files changed,
- tests run,
- Claude findings adopted,
- Claude findings rejected with reasons,
- residual risks.

## Release Gate

Before pushing a new marketplace version:

- run default pytest,
- run Codex plugin validation,
- run all skill validators,
- run the opt-in Claude integration test when changing Claude CLI flags,
- update `CHANGELOG.md` and plugin version together.
