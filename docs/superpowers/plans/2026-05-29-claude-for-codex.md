# Claude-for-Codex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repo-local Codex plugin named `claude-for-codex` that lets Codex invoke Claude Code for normal review, adversarial review, plan review, and multi-model workflow synthesis.

**Architecture:** The plugin is Codex-native: `.codex-plugin/plugin.json` exposes skills, and each skill delegates to a thin Node runtime that shells out to `claude -p` in non-interactive mode. The runtime mirrors OpenAI's Claude-side `codex-plugin-cc` shape: setup, review, adversarial review, plan, status, and result-style commands, with strict read-only review prompts and explicit output contracts.

**Tech Stack:** Codex plugins, Codex skills, Node.js ESM scripts, Claude Code CLI (`claude -p`, `claude agents --json`, `claude plugin validate`), Python validation, git.

---

## File Structure

- Create: `plugins/claude-for-codex/.codex-plugin/plugin.json`
  - Codex plugin manifest.
- Create: `plugins/claude-for-codex/skills/claude-review/SKILL.md`
  - Normal read-only Claude review from Codex.
- Create: `plugins/claude-for-codex/skills/claude-adversarial-review/SKILL.md`
  - Steerable challenge review focused on assumptions, failure modes, and alternatives.
- Create: `plugins/claude-for-codex/skills/claude-plan/SKILL.md`
  - Claude-generated implementation plan for Codex to compare with its own plan.
- Create: `plugins/claude-for-codex/skills/claude-collaboration-loop/SKILL.md`
  - Full workflow that sequences Codex plan, Claude plan, adversarial review, implementation, and final cross-check.
- Create: `plugins/claude-for-codex/scripts/claude-companion.mjs`
  - Shared runtime for setup, git scope collection, Claude CLI invocation, prompt rendering, and status.
- Create: `plugins/claude-for-codex/README.md`
  - Installation and usage guide.
- Create: `.agents/plugins/marketplace.json`
  - Repo-local marketplace entry for local Codex installation.
- Create: `tests/test_claude_for_codex_plugin.py`
  - Offline structural validation.
- Create: `docs/claude-for-codex-workflow.md`
  - Human workflow guide for model-complementary review and planning.

## Research Baseline

- OpenAI's official `openai/codex-plugin-cc` provides Claude-side `/codex:review`, `/codex:adversarial-review`, `/codex:rescue`, `/codex:status`, `/codex:result`, `/codex:cancel`, and optional Stop review gate behavior.
- Its important review boundary is read-only output: review commands must not auto-fix findings.
- Claude Code CLI currently supports `claude -p`, `claude -c -p`, `claude agents --json`, `claude plugin validate`, `--permission-mode`, `--tools`, `--allowedTools`, `--disallowedTools`, `--output-format`, `--model`, `--effort`, and `--plugin-dir`.
- Codex plugin creation requires `.codex-plugin/plugin.json`; local plugin discovery can use a repo-local `.agents/plugins/marketplace.json`.

### Task 1: Scaffold Plugin Manifest And Marketplace

**Files:**
- Create: `plugins/claude-for-codex/.codex-plugin/plugin.json`
- Create: `.agents/plugins/marketplace.json`

- [ ] **Step 1: Create directories**

Run:

```bash
mkdir -p plugins/claude-for-codex/.codex-plugin plugins/claude-for-codex/skills plugins/claude-for-codex/scripts .agents/plugins
```

Expected: command exits 0.

- [ ] **Step 2: Write plugin manifest**

Write `plugins/claude-for-codex/.codex-plugin/plugin.json`:

```json
{
  "name": "claude-for-codex",
  "version": "0.1.0",
  "description": "Use Claude Code from Codex for read-only review, adversarial review, and planning.",
  "author": {
    "name": "fanghao"
  },
  "license": "MIT",
  "keywords": [
    "claude",
    "codex",
    "review",
    "planning",
    "multi-model"
  ],
  "skills": "./skills/",
  "interface": {
    "displayName": "Claude for Codex",
    "shortDescription": "Claude review and planning workflows inside Codex.",
    "longDescription": "Adds Codex skills that invoke Claude Code for normal code review, adversarial challenge review, plan critique, and multi-model collaboration loops.",
    "developerName": "fanghao",
    "category": "Productivity",
    "capabilities": [
      "Review",
      "Planning",
      "Interactive"
    ],
    "defaultPrompt": [
      "Use Claude to review this branch.",
      "Ask Claude to challenge this plan.",
      "Run a Codex-Claude collaboration loop."
    ],
    "brandColor": "#4F46E5"
  }
}
```

- [ ] **Step 3: Write repo-local marketplace**

Write `.agents/plugins/marketplace.json`:

```json
{
  "name": "claude-for-codex-local",
  "interface": {
    "displayName": "Claude for Codex Local"
  },
  "plugins": [
    {
      "name": "claude-for-codex",
      "source": {
        "source": "local",
        "path": "./plugins/claude-for-codex"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_USE"
      },
      "category": "Productivity"
    }
  ]
}
```

- [ ] **Step 4: Validate manifest JSON**

Run:

```bash
python3 -m json.tool plugins/claude-for-codex/.codex-plugin/plugin.json >/dev/null
python3 -m json.tool .agents/plugins/marketplace.json >/dev/null
```

Expected: both commands exit 0.

- [ ] **Step 5: Commit**

Run:

```bash
git add plugins/claude-for-codex/.codex-plugin/plugin.json .agents/plugins/marketplace.json
git commit -m "feat: scaffold claude for codex plugin"
```

Expected: commit succeeds if the repository is initialized for commits.

### Task 2: Implement Shared Claude Runtime

**Files:**
- Create: `plugins/claude-for-codex/scripts/claude-companion.mjs`
- Test: `tests/test_claude_for_codex_plugin.py`

- [ ] **Step 1: Write failing validator for runtime**

Create `tests/test_claude_for_codex_plugin.py`:

```python
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "claude-for-codex"


def test_plugin_manifest_is_valid():
    manifest_path = PLUGIN / ".codex-plugin" / "plugin.json"
    data = json.loads(manifest_path.read_text())
    assert data["name"] == "claude-for-codex"
    assert data["version"] == "0.1.0"
    assert data["skills"] == "./skills/"
    assert data["interface"]["displayName"] == "Claude for Codex"


def test_runtime_has_required_commands():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    text = runtime.read_text()
    for command in ["setup", "review", "adversarial-review", "plan", "status"]:
        assert re.search(rf'case "{re.escape(command)}"', text), command
    assert "claude" in text
    assert "--print" in text or "-p" in text


def test_all_skills_have_frontmatter_and_runtime_call():
    skills = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    assert {p.parent.name for p in skills} == {
        "claude-adversarial-review",
        "claude-collaboration-loop",
        "claude-plan",
        "claude-review",
    }
    for skill in skills:
        text = skill.read_text()
        assert text.startswith("---\n")
        assert "\nname:" in text
        assert "\ndescription:" in text
        assert "claude-companion.mjs" in text
```

- [ ] **Step 2: Run validator to verify it fails**

Run:

```bash
python3 -m pytest tests/test_claude_for_codex_plugin.py -q
```

Expected: FAIL because `claude-companion.mjs` and skills do not exist yet.

- [ ] **Step 3: Write runtime**

Create `plugins/claude-for-codex/scripts/claude-companion.mjs`:

```javascript
#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const VALID_COMMANDS = new Set(["setup", "review", "adversarial-review", "plan", "status"]);

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? process.cwd(),
    env: process.env,
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024
  });
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message ?? result.error) : ""
  };
}

function git(args) {
  return run("git", args);
}

function hasBinary(name) {
  const probe = run(name, ["--version"]);
  return probe.status === 0;
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--base") {
      out.base = argv[++i];
    } else if (arg === "--scope") {
      out.scope = argv[++i];
    } else if (arg === "--model") {
      out.model = argv[++i];
    } else if (arg === "--effort") {
      out.effort = argv[++i];
    } else {
      out._.push(arg);
    }
  }
  return out;
}

function collectGitContext(options) {
  const status = git(["status", "--short", "--untracked-files=all"]);
  const staged = git(["diff", "--cached", "--stat"]);
  const unstaged = git(["diff", "--stat"]);
  const base = options.base;
  const branchDiff = base ? git(["diff", "--stat", `${base}...HEAD`]) : { stdout: "", stderr: "", status: 0 };
  const nameOnly = base
    ? git(["diff", "--name-only", `${base}...HEAD`])
    : git(["diff", "--name-only", "HEAD"]);
  return [
    "<git_context>",
    `cwd: ${process.cwd()}`,
    `scope: ${options.scope ?? "auto"}`,
    `base: ${base ?? ""}`,
    "",
    "git status --short --untracked-files=all:",
    status.stdout.trim() || "(empty)",
    "",
    "git diff --cached --stat:",
    staged.stdout.trim() || "(empty)",
    "",
    "git diff --stat:",
    unstaged.stdout.trim() || "(empty)",
    "",
    base ? `git diff --stat ${base}...HEAD:` : "branch diff:",
    branchDiff.stdout.trim() || "(empty)",
    "",
    "changed files:",
    nameOnly.stdout.trim() || "(empty)",
    "</git_context>"
  ].join("\n");
}

function claudePrint(prompt, options) {
  const args = [
    "--print",
    "--permission-mode",
    "dontAsk",
    "--tools",
    "Read,Grep,Glob,Bash",
    "--disallowedTools",
    "Edit,Write,MultiEdit",
    "--output-format",
    "text"
  ];
  if (options.model) args.push("--model", options.model);
  if (options.effort) args.push("--effort", options.effort);
  args.push(prompt);
  return run("claude", args);
}

function reviewPrompt(kind, args) {
  const focus = args._.join(" ").trim();
  const gitContext = collectGitContext(args);
  const adversarial = kind === "adversarial-review";
  return [
    adversarial ? "<task>Run an adversarial read-only code and design review.</task>" : "<task>Run a read-only code review.</task>",
    gitContext,
    "<rules>",
    "- Do not edit files.",
    "- Do not suggest that you are about to apply fixes.",
    "- Findings must be grounded in changed files or explicit git context.",
    "- Put findings first, ordered by severity.",
    "- Include exact file paths and line numbers when available.",
    "- If there are no findings, say so and list residual risks briefly.",
    adversarial ? "- Challenge the chosen approach, assumptions, tradeoffs, failure modes, and simpler alternatives." : "- Focus on concrete bugs, regressions, missing tests, and maintainability risks.",
    "</rules>",
    focus ? `<focus>${focus}</focus>` : "",
    "<output_format>",
    "## Findings",
    "- [Severity] file:line - issue, evidence, impact, suggested direction",
    "## Open Questions",
    "## Residual Risk",
    "</output_format>"
  ].filter(Boolean).join("\n");
}

function planPrompt(args) {
  const focus = args._.join(" ").trim();
  const gitContext = collectGitContext(args);
  return [
    "<task>Create an independent implementation plan for Codex to compare against its own plan.</task>",
    gitContext,
    "<rules>",
    "- Do not edit files.",
    "- Identify blind spots in the likely Codex implementation.",
    "- Separate observed facts from inferences.",
    "- Prefer small verifiable tasks with tests.",
    "- Include a final reconciliation checklist Codex can use.",
    "</rules>",
    focus ? `<planning_request>${focus}</planning_request>` : "",
    "<output_format>",
    "## Observed Facts",
    "## Plan",
    "## Tests",
    "## Risks And Blind Spots",
    "## Codex Reconciliation Checklist",
    "</output_format>"
  ].filter(Boolean).join("\n");
}

function printSetup() {
  const report = {
    node: process.version,
    claudeAvailable: hasBinary("claude"),
    gitAvailable: hasBinary("git"),
    cwd: process.cwd()
  };
  console.log(JSON.stringify(report, null, 2));
  process.exit(report.claudeAvailable && report.gitAvailable ? 0 : 1);
}

function printStatus() {
  const result = run("claude", ["agents", "--json", "--cwd", process.cwd()]);
  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.error || "claude agents --json failed\n");
    process.exit(result.status);
  }
  process.stdout.write(result.stdout);
}

function runClaudeTask(kind, rawArgs) {
  const args = parseArgs(rawArgs);
  const prompt = kind === "plan" ? planPrompt(args) : reviewPrompt(kind, args);
  const result = claudePrint(prompt, args);
  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.error || "claude -p failed\n");
    process.exit(result.status);
  }
  process.stdout.write(result.stdout);
}

const [command, ...rawArgs] = process.argv.slice(2);
if (!VALID_COMMANDS.has(command)) {
  console.error(`Usage: claude-companion.mjs ${Array.from(VALID_COMMANDS).join("|")} [args]`);
  process.exit(2);
}

switch (command) {
  case "setup":
    printSetup();
    break;
  case "review":
    runClaudeTask("review", rawArgs);
    break;
  case "adversarial-review":
    runClaudeTask("adversarial-review", rawArgs);
    break;
  case "plan":
    runClaudeTask("plan", rawArgs);
    break;
  case "status":
    printStatus();
    break;
}
```

- [ ] **Step 4: Mark runtime executable**

Run:

```bash
chmod +x plugins/claude-for-codex/scripts/claude-companion.mjs
```

Expected: command exits 0.

- [ ] **Step 5: Commit runtime and failing test**

Run:

```bash
git add plugins/claude-for-codex/scripts/claude-companion.mjs tests/test_claude_for_codex_plugin.py
git commit -m "feat: add claude companion runtime"
```

Expected: commit succeeds.

### Task 3: Add Review And Planning Skills

**Files:**
- Create: `plugins/claude-for-codex/skills/claude-review/SKILL.md`
- Create: `plugins/claude-for-codex/skills/claude-adversarial-review/SKILL.md`
- Create: `plugins/claude-for-codex/skills/claude-plan/SKILL.md`
- Create: `plugins/claude-for-codex/skills/claude-collaboration-loop/SKILL.md`

- [ ] **Step 1: Write normal review skill**

Create `plugins/claude-for-codex/skills/claude-review/SKILL.md`:

```markdown
---
name: claude-review
description: Use Claude Code from Codex for a read-only code review of local git changes or a branch diff.
---

# Claude Review

Use this skill when Codex needs an independent Claude Code review before shipping.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" review "$ARGUMENTS"
```

Rules:
- Treat the output as review findings, not implementation instructions.
- Do not fix findings in the same turn unless the user explicitly asks.
- Preserve Claude's file paths, line numbers, uncertainty markers, and residual-risk notes.
- If Claude reports no findings, still report any residual risks it listed.

Arguments:
- `--base <ref>` reviews `ref...HEAD`.
- `--scope auto|working-tree|branch` is passed to the runtime for prompt context.
- `--model <model>` and `--effort <level>` are passed to Claude CLI.
```

- [ ] **Step 2: Write adversarial review skill**

Create `plugins/claude-for-codex/skills/claude-adversarial-review/SKILL.md`:

```markdown
---
name: claude-adversarial-review
description: Use Claude Code to challenge Codex's implementation approach, assumptions, tradeoffs, and failure modes.
---

# Claude Adversarial Review

Use this skill for high-risk changes, architecture decisions, reliability-sensitive code, security-sensitive code, migrations, rollback-sensitive changes, or when Codex may be overconfident.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" adversarial-review "$ARGUMENTS"
```

Rules:
- This is read-only.
- Ask Claude to challenge the direction, not just inspect code style.
- Preserve all findings exactly enough that the user can decide whether to act.
- Do not apply fixes until the user chooses which findings to adopt.

Useful focus examples:
- `--base main challenge the retry and rollback design`
- `look for race conditions and hidden data-loss paths`
- `question whether this abstraction is simpler than the existing pattern`
```

- [ ] **Step 3: Write planning skill**

Create `plugins/claude-for-codex/skills/claude-plan/SKILL.md`:

```markdown
---
name: claude-plan
description: Ask Claude Code for an independent implementation plan that Codex can reconcile before editing.
---

# Claude Plan

Use this skill before substantial implementation work when a second model's decomposition could expose missed tests, hidden constraints, or a safer order of operations.

Run:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" plan "$ARGUMENTS"
```

Rules:
- Treat Claude's plan as a competing design artifact, not an authority.
- Reconcile Claude's plan with Codex's local repo evidence before editing.
- Keep the final Codex plan in local planning files when the task uses file-backed planning.
- Do not let Claude's plan override explicit user instructions.

Output usage:
- Extract observed facts.
- Compare task order against Codex's task plan.
- Add missing tests or risk checks when Claude found real gaps.
- Reject unsupported suggestions with a short reason.
```

- [ ] **Step 4: Write collaboration loop skill**

Create `plugins/claude-for-codex/skills/claude-collaboration-loop/SKILL.md`:

```markdown
---
name: claude-collaboration-loop
description: Run a full Codex-Claude collaboration workflow: Codex plans, Claude plans, Codex reconciles, Codex implements, Claude reviews, Codex reports.
---

# Claude Collaboration Loop

Use this skill for complex, high-stakes, or ambiguous tasks where Codex and Claude should cover each other's blind spots.

Workflow:
1. Codex reads repo state and writes or updates `task_plan.md`, `findings.md`, and `progress.md` when file-backed planning applies.
2. Codex runs:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" plan "$ARGUMENTS"
```

3. Codex reconciles Claude's plan against local evidence:
   - adopt concrete missing tests,
   - adopt safer task ordering when justified,
   - reject unsupported speculation,
   - record the reconciliation in `findings.md`.
4. Codex implements the reconciled plan.
5. Codex runs:

```bash
node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" adversarial-review "$ARGUMENTS"
```

6. Codex reports:
   - implemented files,
   - verification commands,
   - Claude findings adopted,
   - Claude findings rejected,
   - residual risk.

Hard boundaries:
- Claude review output is not self-executing.
- Codex must not claim a Claude finding is fixed unless it applied and verified the fix.
- If Claude CLI is unavailable, fall back to a Codex-only workflow and report that the cross-model pass was skipped.
```

- [ ] **Step 5: Run validator**

Run:

```bash
python3 -m pytest tests/test_claude_for_codex_plugin.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit skills**

Run:

```bash
git add plugins/claude-for-codex/skills tests/test_claude_for_codex_plugin.py
git commit -m "feat: add claude review and planning skills"
```

Expected: commit succeeds.

### Task 4: Add Documentation And Workflow Guide

**Files:**
- Create: `plugins/claude-for-codex/README.md`
- Create: `docs/claude-for-codex-workflow.md`

- [ ] **Step 1: Write plugin README**

Create `plugins/claude-for-codex/README.md`:

```markdown
# Claude for Codex

Codex plugin that invokes Claude Code for independent read-only review, adversarial review, and implementation planning.

## Requirements

- Codex CLI with plugin support
- Claude Code CLI available as `claude`
- Git repository for review scope collection
- Node.js 18 or newer

## Setup Check

```bash
node scripts/claude-companion.mjs setup
```

Expected output includes:

```json
{
  "claudeAvailable": true,
  "gitAvailable": true
}
```

## Install From This Repository

```bash
codex plugin marketplace add .agents/plugins/marketplace.json
```

Then install or enable `claude-for-codex` from the Codex plugin UI.

## Skills

- `claude-review`: normal read-only review of current changes or `--base <ref>`.
- `claude-adversarial-review`: steerable challenge review for design assumptions and failure modes.
- `claude-plan`: independent Claude implementation plan for Codex reconciliation.
- `claude-collaboration-loop`: full plan, reconcile, implement, adversarial review, report workflow.

## Direct Runtime Commands

```bash
node scripts/claude-companion.mjs review --base main
node scripts/claude-companion.mjs adversarial-review --base main challenge the rollback design
node scripts/claude-companion.mjs plan build the plugin and include tests
node scripts/claude-companion.mjs status
```
```

- [ ] **Step 2: Write workflow guide**

Create `docs/claude-for-codex-workflow.md`:

```markdown
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
```

- [ ] **Step 3: Commit docs**

Run:

```bash
git add plugins/claude-for-codex/README.md docs/claude-for-codex-workflow.md
git commit -m "docs: document claude for codex workflow"
```

Expected: commit succeeds.

### Task 5: Validate Plugin And Runtime Smoke Paths

**Files:**
- Modify: `progress.md`
- Modify: `findings.md`

- [ ] **Step 1: Run Python structural tests**

Run:

```bash
python3 -m pytest tests/test_claude_for_codex_plugin.py -q
```

Expected: PASS.

- [ ] **Step 2: Run runtime setup check**

Run:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs setup
```

Expected: JSON with `"claudeAvailable": true` and `"gitAvailable": true`. If Claude is unavailable, record the failure in `progress.md` and keep structural validation as the completed gate.

- [ ] **Step 3: Run Claude plugin validator when available**

Run:

```bash
claude plugin validate research/codex-plugin-cc/plugins/codex
```

Expected: validates the official reference plugin or reports warnings only. This confirms the local Claude CLI validator is available; it is not expected to validate Codex `.codex-plugin` manifests.

- [ ] **Step 4: Run Codex plugin validator**

Run:

```bash
python3 /Users/fanghao/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/claude-for-codex
```

Expected: PASS.

- [ ] **Step 5: Update planning files**

Append to `progress.md`:

```markdown
### Verification
- Python structural tests: PASS
- Runtime setup check: PASS or documented failure
- Codex plugin validator: PASS
```

Append to `findings.md`:

```markdown
## Implementation Findings
- Claude CLI availability was verified with `claude --help` and runtime `setup`.
- The plugin intentionally keeps review skills read-only; fixes require a separate user-approved Codex step.
```

- [ ] **Step 6: Commit validation updates**

Run:

```bash
git add progress.md findings.md
git commit -m "test: validate claude for codex plugin"
```

Expected: commit succeeds.

## Self-Review

Spec coverage:
- Official `codex-plugin-cc` reference: covered by research baseline and mirrored command shape.
- Claude Code command research: covered by runtime flags, `claude -p`, `claude agents --json`, and plugin validation.
- Stronger adversarial workflow: covered by `claude-adversarial-review` and collaboration loop.
- Claude planning workflow: covered by `claude-plan` and collaboration loop.
- Multi-model complementary system: covered by reconciliation rules and reporting contract.

Placeholder scan:
- No unresolved placeholder markers are used.
- Every created file has concrete content.

Type and command consistency:
- Runtime command names match skill calls: `review`, `adversarial-review`, `plan`, `status`, `setup`.
- Test expectations match skill directory names.
- Manifest plugin name, marketplace plugin name, and plugin folder are all `claude-for-codex`.
