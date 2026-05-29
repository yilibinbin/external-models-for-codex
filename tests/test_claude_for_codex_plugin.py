import json
import os
import pathlib
import re
import subprocess

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "claude-for-codex"


def run_fake_claude_review(tmp_path, args, commit_head=False):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    repo.mkdir()
    fake_bin.mkdir()
    capture_dir.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    (repo / "branch.txt").write_text("base\n")
    (repo / "working.txt").write_text("base\n")
    (repo / "sample.txt").write_text("base\n")
    (repo / "other.txt").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "base"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    if commit_head:
        (repo / "branch.txt").write_text("base\nbranch change\n")
        subprocess.run(["git", "add", "branch.txt"], cwd=repo, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "commit", "-m", "branch change"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    (repo / "working.txt").write_text("base\nworking tree change\n")
    (repo / "sample.txt").write_text("base\nsample change\n")
    (repo / "other.txt").write_text("base\nother change\n")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

capture = pathlib.Path(os.environ["CAPTURE_DIR"])
(capture / "argv.json").write_text(json.dumps(sys.argv[1:]))
(capture / "prompt.txt").write_text(sys.argv[-1])
print("FAKE_CLAUDE_OK")
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        ["node", str(runtime), "review", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    prompt = (capture_dir / "prompt.txt").read_text() if (capture_dir / "prompt.txt").exists() else ""
    argv = json.loads((capture_dir / "argv.json").read_text()) if (capture_dir / "argv.json").exists() else []
    return result, prompt, argv


def parse_skill_frontmatter(text):
    assert text.startswith("---\n")
    end = text.find("\n---\n", 4)
    assert end != -1
    fields = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        assert not line.startswith((" ", "\t")), line
        key, separator, value = line.partition(":")
        assert separator, line
        key = key.strip()
        value = value.strip()
        assert key, line
        assert value, line
        is_quoted = (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        )
        assert ": " not in value or is_quoted, f"unquoted colon in {key}: {value}"
        fields[key] = value[1:-1] if is_quoted else value
    return fields


def test_plugin_manifest_is_valid():
    manifest_path = PLUGIN / ".codex-plugin" / "plugin.json"
    data = json.loads(manifest_path.read_text())
    assert data["name"] == "claude-for-codex"
    assert data["version"] == "0.1.1"
    assert data["skills"] == "./skills/"
    assert data["interface"]["displayName"] == "Claude for Codex"


def test_runtime_has_required_commands():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    text = runtime.read_text()
    for command in ["setup", "review", "adversarial-review", "plan", "status"]:
        assert re.search(rf'case "{re.escape(command)}"', text), command
    assert "claude" in text
    assert "--print" in text or "-p" in text
    assert "--path" in text
    assert "--paths" in text
    assert "pathArgs" in text


def test_runtime_applies_pathspec_to_git_context_commands():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    text = runtime.read_text()
    command_patterns = {
        "status": r'git\(\[\s*"status",\s*"--short",\s*"--untracked-files=all",\s*\.\.\.pathArgs\s*\]\)',
        "staged stat": r'git\(\[\s*"diff",\s*"--cached",\s*"--stat",\s*\.\.\.pathArgs\s*\]\)',
        "unstaged stat": r'git\(\[\s*"diff",\s*"--stat",\s*\.\.\.pathArgs\s*\]\)',
        "branch stat": r'git\(\[\s*"diff",\s*"--stat",\s*`\$\{base\}\.\.\.HEAD`,\s*\.\.\.pathArgs\s*\]\)',
        "branch names": r'git\(\[\s*"diff",\s*"--name-only",\s*`\$\{base\}\.\.\.HEAD`,\s*\.\.\.pathArgs\s*\]\)',
        "head names": r'git\(\[\s*"diff",\s*"--name-only",\s*"HEAD",\s*\.\.\.pathArgs\s*\]\)',
    }
    for label, pattern in command_patterns.items():
        assert re.search(pattern, text), label


def test_runtime_keeps_claude_tools_read_only():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    text = runtime.read_text()
    allowed = re.search(r'"--tools",\s*"([^"]+)"', text)
    disallowed = re.search(r'"--disallowedTools",\s*"([^"]+)"', text)
    assert allowed
    assert disallowed
    assert "Bash" not in allowed.group(1).split(",")
    assert "Bash" in disallowed.group(1).split(",")


def test_runtime_avoids_head_name_only_diff_without_head():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    text = runtime.read_text()
    assert re.search(
        r'git\(\[\s*"rev-parse",\s*"--verify",\s*"HEAD"\s*\]\)',
        text,
    )
    assert re.search(r'git\(\[\s*"diff",\s*"--name-only",\s*"HEAD",\s*\.\.\.pathArgs\s*\]\)', text)
    assert re.search(r'includeHeadNameOnly\s*&&\s*status\s*\?\s*changedFilesFromStatus\(status\)', text)
    assert "function changedFilesFromStatus" in text
    assert "changed files from git status fallback" in text


def test_review_with_base_in_no_head_repo_uses_status_fallback(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    repo.mkdir()
    fake_bin.mkdir()
    capture_dir.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "sample.txt").write_text("untracked\n")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

capture = pathlib.Path(os.environ["CAPTURE_DIR"])
(capture / "argv.json").write_text(json.dumps(sys.argv[1:]))
(capture / "prompt.txt").write_text(sys.argv[-1])
print("FAKE_CLAUDE_OK")
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        ["node", str(runtime), "review", "--base", "main", "--path", "sample.txt"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "FAKE_CLAUDE_OK" in result.stdout
    argv = json.loads((capture_dir / "argv.json").read_text())
    prompt = (capture_dir / "prompt.txt").read_text()
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert argv[argv.index("--disallowedTools") + 1] == "Edit,Write,MultiEdit,Bash"
    assert "base requested: main" in prompt
    assert "base effective: unavailable (HEAD missing)" in prompt
    assert "base ignored because HEAD is unavailable" in prompt
    assert "base: main" not in prompt
    assert "fatal: bad revision" not in prompt
    assert "main...HEAD" not in prompt or "branch diff skipped" in prompt
    assert "sample.txt" in prompt
    assert "changed files from git status fallback" in prompt


def test_review_splits_single_quoted_arguments_token(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    repo.mkdir()
    fake_bin.mkdir()
    capture_dir.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "sample.txt").write_text("untracked\n")

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
    result = subprocess.run(
        [
            "node",
            str(runtime),
            "review",
            "--base main --path sample.txt focus on 'quoted risk'",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    prompt = (capture_dir / "prompt.txt").read_text()
    assert "base requested: main" in prompt
    assert "base effective: unavailable (HEAD missing)" in prompt
    assert "base ignored because HEAD is unavailable" in prompt
    assert "base: main" not in prompt
    assert "paths: sample.txt" in prompt
    assert "<focus>focus on quoted risk</focus>" in prompt


def test_review_splits_double_quotes_and_escaped_spaces(tmp_path):
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ['--scope working-tree --path "sample.txt" focus on escaped\\ space and "double quoted risk"'],
    )

    assert result.returncode == 0, result.stderr
    assert "paths: sample.txt" in prompt
    assert "<focus>focus on escaped space and double quoted risk</focus>" in prompt


def test_unmatched_quote_exits_2_without_calling_claude(tmp_path):
    result, prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--path sample.txt focus on 'unterminated"],
    )

    assert result.returncode == 2
    assert "Unmatched quote in arguments." in result.stderr
    assert prompt == ""
    assert argv == []


def test_empty_quoted_option_value_exits_2_without_calling_claude(tmp_path):
    result, prompt, argv = run_fake_claude_review(
        tmp_path,
        ['--scope working-tree --path "" focus on empty path'],
    )

    assert result.returncode == 2
    assert "Missing value for --path" in result.stderr
    assert prompt == ""
    assert argv == []


def test_invalid_base_ref_is_not_reported_effective(tmp_path):
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--base", "does-not-exist"],
        commit_head=True,
    )

    assert result.returncode == 0, result.stderr
    assert "base requested: does-not-exist" in prompt
    assert "base effective: unavailable (base ref missing)" in prompt
    assert "base ignored because requested base ref is unavailable" in prompt
    assert "base effective: does-not-exist" not in prompt
    assert "fatal: ambiguous argument" not in prompt


def test_scope_working_tree_omits_branch_diff_from_prompt(tmp_path):
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--scope", "working-tree", "--base", "HEAD~1"],
        commit_head=True,
    )

    assert result.returncode == 0, result.stderr
    assert "FAKE_CLAUDE_OK" in result.stdout
    assert "scope: working-tree" in prompt
    assert "git status --short --untracked-files=all" in prompt
    assert "git diff --cached --stat" in prompt
    assert "git diff --stat" in prompt
    assert "working tree change" in prompt
    assert "git diff --stat HEAD~1...HEAD" not in prompt
    assert "git diff --name-only HEAD~1...HEAD" not in prompt
    assert "branch.txt" not in prompt


def test_scope_branch_omits_working_tree_diff_from_prompt(tmp_path):
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--scope", "branch", "--base", "HEAD~1"],
        commit_head=True,
    )

    assert result.returncode == 0, result.stderr
    assert "FAKE_CLAUDE_OK" in result.stdout
    assert "scope: branch" in prompt
    assert "git diff --stat HEAD~1...HEAD" in prompt
    assert "git diff --name-only HEAD~1...HEAD" in prompt
    assert "branch.txt" in prompt
    assert "git status --short --untracked-files=all" not in prompt
    assert "git diff --cached --stat" not in prompt
    assert "git diff --stat:\n" not in prompt
    assert "working tree change" not in prompt


def test_scope_branch_without_base_exits_2_without_calling_claude(tmp_path):
    result, prompt, argv = run_fake_claude_review(tmp_path, ["--scope", "branch"])

    assert result.returncode == 2
    assert "--scope branch requires --base <ref>." in result.stderr
    assert prompt == ""
    assert argv == []


def test_repeated_path_options_accumulate_pathspecs(tmp_path):
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--scope", "working-tree", "--path", "sample.txt", "--path", "other.txt"],
    )

    assert result.returncode == 0, result.stderr
    assert "FAKE_CLAUDE_OK" in result.stdout
    assert "paths: sample.txt other.txt" in prompt
    assert "git status --short --untracked-files=all -- sample.txt other.txt" in prompt
    assert "sample.txt" in prompt
    assert "sample change" in prompt
    assert "other.txt" in prompt
    assert "other change" in prompt
    assert "working.txt" not in prompt


def test_invalid_scope_exits_2_without_calling_claude(tmp_path):
    result, prompt, argv = run_fake_claude_review(tmp_path, ["--scope", "everything"])

    assert result.returncode == 2
    assert 'Invalid --scope "everything"' in result.stderr
    assert "Valid scopes: auto, working-tree, branch" in result.stderr
    assert prompt == ""
    assert argv == []


def test_missing_scope_value_exits_2_without_calling_claude(tmp_path):
    result, prompt, argv = run_fake_claude_review(tmp_path, ["--scope"])

    assert result.returncode == 2
    assert "Missing value for --scope" in result.stderr
    assert prompt == ""
    assert argv == []


@pytest.mark.parametrize("option", ["--base", "--path", "--paths", "--model", "--effort"])
def test_missing_option_value_exits_2_without_calling_claude(tmp_path, option):
    result, prompt, argv = run_fake_claude_review(tmp_path, [option, "--scope", "auto"])

    assert result.returncode == 2
    assert f"Missing value for {option}" in result.stderr
    assert prompt == ""
    assert argv == []


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
    result = subprocess.run(
        ["node", str(runtime), "status"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
    argv = json.loads((capture_dir / "argv.json").read_text())
    assert argv == ["agents", "--json", "--cwd", str(repo)]


def test_real_claude_permission_mode_when_enabled():
    if os.environ.get("RUN_CLAUDE_INTEGRATION") != "1":
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
        frontmatter = parse_skill_frontmatter(text)
        assert frontmatter["name"] == skill.parent.name
        assert frontmatter["description"]
        assert "claude-companion.mjs" in text
