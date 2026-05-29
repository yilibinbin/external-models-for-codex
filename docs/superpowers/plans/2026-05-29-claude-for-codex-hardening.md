# Claude-for-Codex Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed Claude-review bugs and harden the `claude-for-codex` plugin for reliable remote installation, scoped reviews, release upgrades, and safer CLI behavior.

**Architecture:** Keep the current Node runtime as the single command dispatcher, but make option semantics explicit and testable: `--scope` controls target selection, `--path` remains path filtering, and no-HEAD/base conflicts are reported clearly. Add default-discovered pytest coverage for behavior, optional real-Claude integration checks, and release documentation so remote marketplace upgrades are repeatable.

**Tech Stack:** Node.js ESM, Claude Code CLI, Codex plugin manifests, pytest, git/GitHub remote marketplace.

---

## File Structure

- Modify: `plugins/claude-for-codex/scripts/claude-companion.mjs`
  - Implement real `--scope` behavior, no-HEAD/base conflict reporting, stricter argument parsing, and status helpers.
- Modify: `tests/test_claude_for_codex_plugin.py`
  - Add behavior tests for scope modes, no-HEAD base warnings, status command, splitter edge cases, and optional real Claude integration.
- Modify: `plugins/claude-for-codex/README.md`
  - Document scope semantics, remote install/upgrade, release workflow, optional integration test, and no-HEAD behavior.
- Modify: `docs/claude-for-codex-workflow.md`
  - Document how to interpret Claude review findings and release hardening gates.
- Modify: `plugins/claude-for-codex/.codex-plugin/plugin.json`
  - Bump version from `0.1.0` to `0.1.1`.
- Create: `plugins/claude-for-codex/CHANGELOG.md`
  - Track `0.1.1` bugfix release and future upgrade notes.
- Modify: `task_plan.md`, `findings.md`, `progress.md`
  - Record the hardening phase, decisions, errors, and verification results.

## Confirmed Issues To Fix

1. `--scope` is parsed but does not change target selection.
2. No-HEAD repositories with `--base` fall back silently while still showing `base: main`.
3. `status` command is not covered by tests.
4. Real Claude CLI behavior is only manually verified; no opt-in integration test exists.
5. Release/upgrade workflow is under-documented and version is pinned at `0.1.0`.
6. Hand-written argument splitting needs edge-case coverage.
7. Marketplace name `claude-for-codex-local` is stable but semantically awkward for remote use; avoid breaking it now, document it as the stable marketplace id.

### Task 1: Implement Real Scope Semantics

**Files:**
- Modify: `plugins/claude-for-codex/scripts/claude-companion.mjs`
- Test: `tests/test_claude_for_codex_plugin.py`

- [ ] **Step 1: Write failing tests for scope modes**

Add these tests to `tests/test_claude_for_codex_plugin.py`:

```python
def test_scope_working_tree_omits_branch_diff_from_prompt(tmp_path):
    result, prompt = run_fake_claude_review(
        tmp_path,
        ["review", "--base", "main", "--scope", "working-tree"],
        commit_head=True,
    )
    assert result.returncode == 0, result.stderr
    assert "scope: working-tree" in prompt
    assert "branch diff skipped by scope" in prompt
    assert "git diff --stat main...HEAD" not in prompt


def test_scope_branch_omits_working_tree_diff_from_prompt(tmp_path):
    result, prompt = run_fake_claude_review(
        tmp_path,
        ["review", "--base", "HEAD", "--scope", "branch"],
        commit_head=True,
    )
    assert result.returncode == 0, result.stderr
    assert "scope: branch" in prompt
    assert "working tree diff skipped by scope" in prompt
    assert "git diff --cached" not in prompt
    assert "git diff --stat\n" not in prompt
```

Also add this helper if it does not already exist:

```python
def run_fake_claude_review(tmp_path, args, commit_head=False):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    repo.mkdir()
    fake_bin.mkdir()
    capture_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "sample.txt").write_text("initial\n")
    if commit_head:
        subprocess.run(["git", "add", "sample.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)
        (repo / "sample.txt").write_text("changed\n")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import sys
capture = pathlib.Path(os.environ["CAPTURE_DIR"])
(capture / "prompt.txt").write_text(sys.argv[-1])
print("FAKE_CLAUDE_OK")
"""
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(["node", str(runtime), *args], cwd=repo, env=env, capture_output=True, text=True)
    prompt = (capture_dir / "prompt.txt").read_text() if (capture_dir / "prompt.txt").exists() else ""
    return result, prompt
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m pytest tests/test_claude_for_codex_plugin.py::test_scope_working_tree_omits_branch_diff_from_prompt tests/test_claude_for_codex_plugin.py::test_scope_branch_omits_working_tree_diff_from_prompt -q
```

Expected: FAIL because current runtime prints both working-tree and branch sections regardless of `--scope`.

- [ ] **Step 3: Implement scope normalization and conditional context**

In `plugins/claude-for-codex/scripts/claude-companion.mjs`, add:

```javascript
const VALID_SCOPES = new Set(["auto", "working-tree", "branch"]);

function normalizeScope(scope) {
  const value = scope ?? "auto";
  if (!VALID_SCOPES.has(value)) {
    throw new Error(`Unsupported scope "${value}". Use auto, working-tree, or branch.`);
  }
  return value;
}

function shouldIncludeWorkingTree(scope, base) {
  return scope === "auto" || scope === "working-tree" || !base;
}

function shouldIncludeBranch(scope, base) {
  return Boolean(base) && (scope === "auto" || scope === "branch");
}
```

Update `runClaudeTask` so parse errors are user-visible:

```javascript
let args;
try {
  args = parseArgs(rawArgs);
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exit(2);
}
```

Update `collectGitContext(options)` to compute:

```javascript
const scope = normalizeScope(options.scope);
const includeWorkingTree = shouldIncludeWorkingTree(scope, base);
const includeBranch = shouldIncludeBranch(scope, base);
```

Use `safeResult("(working tree diff skipped by scope)")` for staged/unstaged sections when `includeWorkingTree` is false, and `safeResult("(branch diff skipped by scope)")` when `includeBranch` is false.

- [ ] **Step 4: Run scope tests**

Run:

```bash
python3 -m pytest tests/test_claude_for_codex_plugin.py::test_scope_working_tree_omits_branch_diff_from_prompt tests/test_claude_for_codex_plugin.py::test_scope_branch_omits_working_tree_diff_from_prompt -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add plugins/claude-for-codex/scripts/claude-companion.mjs tests/test_claude_for_codex_plugin.py
git commit -m "fix: implement claude review scope modes"
```

Expected: commit succeeds.

### Task 2: Make No-HEAD Base Behavior Explicit

**Files:**
- Modify: `plugins/claude-for-codex/scripts/claude-companion.mjs`
- Test: `tests/test_claude_for_codex_plugin.py`

- [ ] **Step 1: Write failing no-HEAD base test**

Add:

```python
def test_no_head_base_prompt_explicitly_says_base_ignored(tmp_path):
    result, prompt = run_fake_claude_review(
        tmp_path,
        ["review", "--base", "main", "--path", "sample.txt"],
        commit_head=False,
    )
    assert result.returncode == 0, result.stderr
    assert "base requested: main" in prompt
    assert "base effective: unavailable (HEAD missing)" in prompt
    assert "base ignored because HEAD is unavailable" in prompt
    assert "fatal: bad revision" not in prompt
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
python3 -m pytest tests/test_claude_for_codex_plugin.py::test_no_head_base_prompt_explicitly_says_base_ignored -q
```

Expected: FAIL because current prompt only prints `base: main`.

- [ ] **Step 3: Implement explicit base metadata**

In `collectGitContext(options)`, replace the single base line:

```javascript
`base: ${base ?? ""}`,
```

with:

```javascript
`base requested: ${base ?? ""}`,
`base effective: ${base && !headExists ? "unavailable (HEAD missing)" : (base ?? "")}`,
base && !headExists ? "base ignored because HEAD is unavailable; using working tree/status context instead." : "",
```

Filter blank lines before joining or keep the empty string out of the array via `.filter((line) => line !== null)`.

- [ ] **Step 4: Run no-HEAD tests**

Run:

```bash
python3 -m pytest tests/test_claude_for_codex_plugin.py::test_no_head_base_prompt_explicitly_says_base_ignored tests/test_claude_for_codex_plugin.py::test_review_with_base_in_no_head_repo_uses_status_fallback -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add plugins/claude-for-codex/scripts/claude-companion.mjs tests/test_claude_for_codex_plugin.py
git commit -m "fix: clarify no-head base review context"
```

Expected: commit succeeds.

### Task 3: Harden Argument Parsing Tests

**Files:**
- Modify: `plugins/claude-for-codex/scripts/claude-companion.mjs`
- Test: `tests/test_claude_for_codex_plugin.py`

- [ ] **Step 1: Add edge-case splitter tests**

Add:

```python
def test_review_splits_escaped_spaces_and_double_quotes(tmp_path):
    result, prompt = run_fake_claude_review(
        tmp_path,
        ["review", '--path sample.txt focus on "double quoted risk" and escaped\\ space'],
        commit_head=False,
    )
    assert result.returncode == 0, result.stderr
    assert "path: sample.txt" in prompt
    assert "<focus>focus on double quoted risk and escaped space</focus>" in prompt


def test_unmatched_quote_returns_parse_error(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    result = subprocess.run(
        ["node", str(runtime), "review", "--path sample.txt 'unterminated"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Unterminated quote" in result.stderr
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m pytest tests/test_claude_for_codex_plugin.py::test_unmatched_quote_returns_parse_error tests/test_claude_for_codex_plugin.py::test_review_splits_escaped_spaces_and_double_quotes -q
```

Expected: `test_unmatched_quote_returns_parse_error` FAILS because current splitter accepts unmatched quotes.

- [ ] **Step 3: Make splitter reject unmatched quotes**

In `splitArgumentString(value)`, after the loop and before appending `current`, add:

```javascript
if (quote) {
  throw new Error(`Unterminated quote ${quote} in arguments.`);
}
```

Ensure `runClaudeTask` catches parser errors as described in Task 1.

- [ ] **Step 4: Run splitter tests**

Run:

```bash
python3 -m pytest tests/test_claude_for_codex_plugin.py::test_unmatched_quote_returns_parse_error tests/test_claude_for_codex_plugin.py::test_review_splits_escaped_spaces_and_double_quotes -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add plugins/claude-for-codex/scripts/claude-companion.mjs tests/test_claude_for_codex_plugin.py
git commit -m "test: cover quoted claude argument parsing"
```

Expected: commit succeeds.

### Task 4: Test Status Command And Real Claude Integration Gate

**Files:**
- Modify: `tests/test_claude_for_codex_plugin.py`
- Modify: `plugins/claude-for-codex/README.md`

- [ ] **Step 1: Add fake status test**

Add:

```python
def test_status_invokes_claude_agents_json_with_cwd(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    repo.mkdir()
    fake_bin.mkdir()
    capture_dir.mkdir()
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
capture = pathlib.Path(os.environ["CAPTURE_DIR"])
(capture / "argv.json").write_text(json.dumps(sys.argv[1:]))
print("[]")
"""
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(["node", str(runtime), "status"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
    argv = json.loads((capture_dir / "argv.json").read_text())
    assert argv == ["agents", "--json", "--cwd", str(repo)]
```

- [ ] **Step 2: Add opt-in real Claude integration test**

Add:

```python
def test_real_claude_permission_mode_when_enabled():
    if os.environ.get("RUN_CLAUDE_INTEGRATION") != "1":
        import pytest
        pytest.skip("Set RUN_CLAUDE_INTEGRATION=1 to run real Claude CLI check.")
    result = subprocess.run(
        [
            "claude",
            "--print",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Read,Grep,Glob",
            "--disallowedTools",
            "Edit,Write,MultiEdit,Bash",
            "--output-format",
            "text",
            "Return exactly: CLAUDE_DONTASK_OK",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "CLAUDE_DONTASK_OK" in result.stdout
```

- [ ] **Step 3: Document integration test**

In `plugins/claude-for-codex/README.md`, add:

```markdown
## Verification

```bash
python3 -m pytest -q
RUN_CLAUDE_INTEGRATION=1 python3 -m pytest tests/test_claude_for_codex_plugin.py::test_real_claude_permission_mode_when_enabled -q
```
```

- [ ] **Step 4: Run tests**

Run:

```bash
python3 -m pytest -q
RUN_CLAUDE_INTEGRATION=1 python3 -m pytest tests/test_claude_for_codex_plugin.py::test_real_claude_permission_mode_when_enabled -q
```

Expected: default pytest PASS with one skipped real-Claude test; integration test PASS with `CLAUDE_DONTASK_OK`.

- [ ] **Step 5: Commit**

Run:

```bash
git add tests/test_claude_for_codex_plugin.py plugins/claude-for-codex/README.md
git commit -m "test: add status and claude cli integration checks"
```

Expected: commit succeeds.

### Task 5: Add Release And Upgrade Documentation

**Files:**
- Modify: `plugins/claude-for-codex/.codex-plugin/plugin.json`
- Modify: `tests/test_claude_for_codex_plugin.py`
- Create: `plugins/claude-for-codex/CHANGELOG.md`
- Modify: `plugins/claude-for-codex/README.md`
- Modify: `docs/claude-for-codex-workflow.md`

- [ ] **Step 1: Bump plugin version**

Change `plugins/claude-for-codex/.codex-plugin/plugin.json`:

```json
"version": "0.1.1"
```

Change test expectation in `tests/test_claude_for_codex_plugin.py`:

```python
assert data["version"] == "0.1.1"
```

- [ ] **Step 2: Add changelog**

Create `plugins/claude-for-codex/CHANGELOG.md`:

```markdown
# Changelog

## 0.1.1

- Implement real `--scope auto|working-tree|branch` behavior.
- Clarify no-HEAD `--base` handling in review context.
- Add status command coverage.
- Add opt-in real Claude CLI integration test.
- Document remote marketplace install, upgrade, and release checks.

## 0.1.0

- Initial Claude-for-Codex plugin with read-only Claude review, adversarial review, planning, and collaboration loop skills.
```

- [ ] **Step 3: Add release docs to README**

Append:

```markdown
## Remote Install

```bash
codex plugin marketplace add git@github.com:yilibinbin/claude-for-codex.git --ref main
codex plugin add claude-for-codex@claude-for-codex-local
```

`claude-for-codex-local` is the stable marketplace id for this repository, even when installed from GitHub.

## Upgrade

```bash
codex plugin marketplace upgrade claude-for-codex-local
codex plugin remove claude-for-codex
codex plugin add claude-for-codex@claude-for-codex-local
```

## Release Checklist

1. Update `.codex-plugin/plugin.json` version.
2. Update `CHANGELOG.md`.
3. Run `python3 -m pytest -q`.
4. Run `python3 /Users/fanghao/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/claude-for-codex`.
5. Run all skill validators.
6. Commit, tag, and push.
```

- [ ] **Step 4: Add workflow release gate**

Append to `docs/claude-for-codex-workflow.md`:

```markdown
## Release Gate

Before pushing a new marketplace version:

- run default pytest,
- run Codex plugin validation,
- run all skill validators,
- run the opt-in Claude integration test when changing Claude CLI flags,
- update `CHANGELOG.md` and plugin version together.
```

- [ ] **Step 5: Run documentation and version tests**

Run:

```bash
python3 -m pytest tests/test_claude_for_codex_plugin.py::test_plugin_manifest_is_valid -q
python3 -m pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add plugins/claude-for-codex/.codex-plugin/plugin.json tests/test_claude_for_codex_plugin.py plugins/claude-for-codex/CHANGELOG.md plugins/claude-for-codex/README.md docs/claude-for-codex-workflow.md
git commit -m "docs: add plugin release and upgrade process"
```

Expected: commit succeeds.

### Task 6: Final Validation, Review, Tag, And Push

**Files:**
- Modify: `task_plan.md`
- Modify: `findings.md`
- Modify: `progress.md`

- [ ] **Step 1: Run complete validation**

Run:

```bash
python3 -m pytest -q
node --check plugins/claude-for-codex/scripts/claude-companion.mjs
node plugins/claude-for-codex/scripts/claude-companion.mjs setup
python3 /Users/fanghao/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/claude-for-codex
for d in plugins/claude-for-codex/skills/*; do python3 /Users/fanghao/.codex/skills/.system/skill-creator/scripts/quick_validate.py "$d"; done
```

Expected:
- pytest: PASS with one skipped integration test unless `RUN_CLAUDE_INTEGRATION=1`.
- node check: PASS.
- setup: JSON with `claudeAvailable: true`, `gitAvailable: true`.
- plugin validator: PASS.
- skill validators: all `Skill is valid!`.

- [ ] **Step 2: Run real Claude integration**

Run:

```bash
RUN_CLAUDE_INTEGRATION=1 python3 -m pytest tests/test_claude_for_codex_plugin.py::test_real_claude_permission_mode_when_enabled -q
```

Expected: PASS.

- [ ] **Step 3: Run final Claude review**

Run:

```bash
node plugins/claude-for-codex/scripts/claude-companion.mjs adversarial-review "--path plugins/claude-for-codex --path tests review the hardening patch for remaining release, scope, and read-only safety issues"
```

Expected: Claude returns no high-severity findings. If it reports valid high-severity findings, fix them before proceeding.

- [ ] **Step 4: Update planning files**

Append to `progress.md`:

```markdown
### Hardening Validation
- Default pytest: PASS
- Real Claude integration: PASS
- Codex plugin validator: PASS
- Skill validators: PASS
- Final Claude adversarial review: PASS or documented findings fixed
```

Add to `findings.md`:

```markdown
## Hardening Findings
- `--scope` now controls git context sections.
- No-HEAD `--base` now explicitly reports that base is unavailable.
- `claude-for-codex-local` remains the stable marketplace id for compatibility.
```

Update `task_plan.md` current phase to complete.

- [ ] **Step 5: Commit planning files**

Run:

```bash
git add task_plan.md findings.md progress.md
git commit -m "docs: record claude plugin hardening results"
```

Expected: commit succeeds.

- [ ] **Step 6: Tag and push**

Run:

```bash
git tag v0.1.1
git push origin main
git push origin v0.1.1
```

Expected: remote GitHub repository receives new commits and tag.

## Self-Review

Spec coverage:
- `--scope` empty behavior: Task 1.
- No-HEAD `--base` ambiguity: Task 2.
- Splitter robustness: Task 3.
- Status and real Claude tests: Task 4.
- Version/release/upgrade docs: Task 5.
- Final validation/tag/push: Task 6.
- Marketplace name awkwardness: Task 5 documents stable compatibility instead of a breaking rename.

Placeholder scan:
- No unresolved placeholder markers are used.
- Every code-changing step includes exact code or exact command text.

Type and command consistency:
- Runtime commands remain `setup`, `review`, `adversarial-review`, `plan`, `status`.
- Test file path is `tests/test_claude_for_codex_plugin.py`.
- Plugin version after hardening is consistently `0.1.1`.
