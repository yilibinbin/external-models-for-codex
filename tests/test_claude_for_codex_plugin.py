import json
import os
import pathlib
import re
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "claude-for-codex"


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
    assert "--path" in text
    assert "--paths" in text
    assert "pathspec" in text


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
    assert re.search(
        r'headExists\s*\?\s*git\(\[\s*"diff",\s*"--name-only",\s*"HEAD",\s*\.\.\.pathArgs\s*\]\)\s*:\s*changedFilesFromStatus\(status\)',
        text,
        re.DOTALL,
    )
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
    assert "base: main" in prompt
    assert "path: sample.txt" in prompt
    assert "<focus>focus on quoted risk</focus>" in prompt


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
