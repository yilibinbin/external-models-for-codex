import json
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import time

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "claude-for-codex"
NODE = os.environ.get("NODE_BINARY") or shutil.which("node") or "/Applications/Codex.app/Contents/Resources/node"


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

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)

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


def run_fake_claude_adversarial_review(tmp_path, args, commit_head=False):
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

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)

capture = pathlib.Path(os.environ["CAPTURE_DIR"])
(capture / "argv.json").write_text(json.dumps(sys.argv[1:]))
(capture / "prompt.txt").write_text(sys.argv[-1])
print("FAKE_CLAUDE_ADVERSARIAL_OK")
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        ["node", str(runtime), "adversarial-review", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    prompt = (capture_dir / "prompt.txt").read_text() if (capture_dir / "prompt.txt").exists() else ""
    argv = json.loads((capture_dir / "argv.json").read_text()) if (capture_dir / "argv.json").exists() else []
    return result, prompt, argv


def run_fake_claude_multi_review(tmp_path, args, commit_head=False, fail_roles=None):
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

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)

capture = pathlib.Path(os.environ["CAPTURE_DIR"])
call_index = len(list(capture.glob("argv-*.json")))
prompt = sys.argv[-1]
(capture / f"argv-{call_index}.json").write_text(json.dumps(sys.argv[1:]))
(capture / f"prompt-{call_index}.txt").write_text(prompt)
fail_roles = [role for role in os.environ.get("FAIL_ROLES", "").split(",") if role]
for role in fail_roles:
    if f"<role_name>{role}</role_name>" in prompt:
        print(f"FAKE_CLAUDE_FAIL role {role} call {call_index}")
        print(f"diagnostic for failed role {role}", file=sys.stderr)
        raise SystemExit(17)
print(f"FAKE_CLAUDE_OK call {call_index}")
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    if fail_roles:
        env["FAIL_ROLES"] = ",".join(fail_roles)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        ["node", str(runtime), "multi-review", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    prompts = [
        path.read_text()
        for path in sorted(capture_dir.glob("prompt-*.txt"), key=lambda p: int(p.stem.split("-")[1]))
    ]
    argvs = [
        json.loads(path.read_text())
        for path in sorted(capture_dir.glob("argv-*.json"), key=lambda p: int(p.stem.split("-")[1]))
    ]
    return result, prompts, argvs


def prepare_gate_repo(tmp_path, *, with_change=True):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    plugin_data = tmp_path / "plugin-data"
    repo.mkdir()
    fake_bin.mkdir()
    capture_dir.mkdir()
    plugin_data.mkdir()

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
    (repo / "sample.txt").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    if with_change:
        (repo / "sample.txt").write_text("base\nchanged\n")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)

capture = pathlib.Path(os.environ["CAPTURE_DIR"])
call_index = len(list(capture.glob("argv-*.json")))
prompt = sys.argv[-1]
(capture / f"argv-{call_index}.json").write_text(json.dumps(sys.argv[1:]))
(capture / f"prompt-{call_index}.txt").write_text(prompt)
fail_roles = [role for role in os.environ.get("FAIL_ROLES", "").split(",") if role]
invalid_roles = [role for role in os.environ.get("INVALID_ROLES", "").split(",") if role]
block_roles = [role for role in os.environ.get("BLOCK_ROLES", "").split(",") if role]
for role in fail_roles:
    if f"<role_name>{role}</role_name>" in prompt:
        print(f"failed role {role}", file=sys.stderr)
        raise SystemExit(17)
for role in invalid_roles:
    if f"<role_name>{role}</role_name>" in prompt:
        print(f"MAYBE: invalid role {role}")
        raise SystemExit(0)
for role in block_roles:
    if f"<role_name>{role}</role_name>" in prompt:
        print(f"BLOCK: blocking issue from {role}")
        raise SystemExit(0)
role = "unknown"
for candidate in ["correctness", "security", "tests", "release", "adversarial"]:
    if f"<role_name>{candidate}</role_name>" in prompt:
        role = candidate
print(f"ALLOW: no blocking issue from {role}")
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return runtime, repo, capture_dir, env


def run_fake_review_gate(tmp_path, *, enable=True, with_change=True, hook_input=None, extra_env=None):
    runtime, repo, capture_dir, env = prepare_gate_repo(tmp_path, with_change=with_change)
    if extra_env:
        env.update(extra_env)
    if enable:
        setup = subprocess.run(
            ["node", str(runtime), "setup", "--enable-review-gate", "--review-gate-mode", "multi-role"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
        )
        assert setup.returncode == 0, setup.stderr
    input_text = json.dumps(hook_input or {"hook_event_name": "Stop", "cwd": str(repo)})
    result = subprocess.run(
        ["node", str(runtime), "review-gate"],
        cwd=repo,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
    )
    prompts = [
        path.read_text()
        for path in sorted(capture_dir.glob("prompt-*.txt"), key=lambda p: int(p.stem.split("-")[1]))
    ]
    return result, prompts, capture_dir


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
    assert data["version"] == "0.4.0"
    assert data["skills"] == "./skills/"
    assert "hooks" not in data
    assert data["interface"]["displayName"] == "Claude for Codex"


def test_plugin_stop_hook_manifest_is_autodiscoverable():
    hooks_path = PLUGIN / "hooks" / "hooks.json"
    data = json.loads(hooks_path.read_text())
    assert set(data["hooks"]) == {"SessionStart", "SessionEnd", "UserPromptSubmit", "Stop"}
    stop_hooks = data["hooks"]["Stop"]
    command = stop_hooks[0]["hooks"][0]["command"]
    assert "claude-review-gate.mjs" in command
    assert "CLAUDE_PLUGIN_ROOT:-$CODEX_PLUGIN_ROOT" in command
    assert stop_hooks[0]["hooks"][0]["timeout"] == 900
    assert "session-lifecycle.mjs" in data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "session-lifecycle.mjs" in data["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
    assert "unread-result.mjs" in data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]


def test_runtime_has_required_commands():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    text = runtime.read_text()
    for command in ["setup", "review", "adversarial-review", "multi-review", "plan", "status", "review-gate", "jobs", "result", "cancel", "rescue"]:
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


def test_default_multi_review_roles_exist_in_registry():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    text = runtime.read_text()
    registry_match = re.search(r"const REVIEW_ROLES = Object\.freeze\(\{(?P<body>.*?)\n\}\);", text, re.S)
    defaults_match = re.search(
        r"const DEFAULT_MULTI_REVIEW_ROLES = Object\.freeze\(\[(?P<body>.*?)\]\);",
        text,
        re.S,
    )
    assert registry_match
    assert defaults_match
    registry_roles = set(re.findall(r"^\s+([a-z-]+):\s*\{", registry_match.group("body"), re.M))
    default_roles = re.findall(r'"([^"]+)"', defaults_match.group("body"))
    assert default_roles == ["correctness", "security", "tests", "release", "adversarial"]
    assert set(default_roles).issubset(registry_roles)


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


@pytest.mark.parametrize(
    ("args", "marker"),
    [
        (["--scope working-tree HIT_TINY"], "HIT_TINY"),
        (["--scope", "working-tree", "请检查 HIT_CHINESE 是否命中"], "HIT_CHINESE"),
        (['--scope working-tree focus on "HIT QUOTED SPACE" and escaped\\ space'], "HIT QUOTED SPACE"),
        (["--scope", "working-tree", "unicode HIT_UNICODE 中文 αβγ"], "HIT_UNICODE"),
        (["--scope", "working-tree", "HIT_MEDIUM " + ("m" * 8192)], "HIT_MEDIUM"),
        (["--scope", "working-tree", "HIT_LARGE " + ("L" * 65536)], "HIT_LARGE"),
        (["--scope working-tree --path sample.txt --path other.txt HIT_PATHS"], "HIT_PATHS"),
    ],
)
def test_review_focus_hits_across_text_sizes_and_argument_cases(tmp_path, args, marker):
    result, prompt, _argv = run_fake_claude_review(tmp_path, args)

    assert result.returncode == 0, result.stderr
    assert "FAKE_CLAUDE_OK" in result.stdout
    assert marker in prompt


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


def test_unknown_review_role_exits_2_without_calling_claude(tmp_path):
    result, prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--role", "unknown", "--scope", "working-tree"],
    )

    assert result.returncode == 2
    assert 'Unknown review role "unknown"' in result.stderr
    assert (
        "Valid roles: adversarial, architect, correctness, minimalist, release, security, skeptic, tests"
        in result.stderr
    )
    assert prompt == ""
    assert argv == []


def test_comma_separated_review_roles_resolve_in_order(tmp_path):
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--roles", "correctness,security", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert "<review_roles>correctness, security</review_roles>" in prompt


def test_repeated_review_role_options_accumulate_in_order(tmp_path):
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--role", "correctness", "--role", "tests", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert "<review_roles>correctness, tests</review_roles>" in prompt


def test_adversarial_review_prompt_requires_intent_verdict_and_lead_judgment(tmp_path):
    result, prompt, argv = run_fake_claude_adversarial_review(
        tmp_path,
        ["challenge whether the retry design is sufficient"],
    )

    assert result.returncode == 0
    assert "FAKE_CLAUDE_ADVERSARIAL_OK" in result.stdout
    assert "--permission-mode" in argv
    assert "dontAsk" in argv
    assert "--disallowedTools" in argv
    assert "Edit,Write,MultiEdit,Bash" in argv
    assert "<task>Run an adversarial read-only code and design review.</task>" in prompt
    assert "## Intent" in prompt
    assert "## Verdict: PASS | CONTESTED | REJECT" in prompt
    assert "## Findings" in prompt
    assert "## What Went Well" in prompt
    assert "## Lead Judgment" in prompt
    assert "working tree change" in prompt
    assert "challenge whether the retry design is sufficient" in prompt


def test_adversarial_review_accepts_custom_lens_subset(tmp_path):
    result, prompt, _argv = run_fake_claude_adversarial_review(
        tmp_path,
        ["--adversarial-lenses", "skeptic,minimalist", "focus on complexity"],
    )

    assert result.returncode == 0
    assert '<lens name="skeptic" label="Skeptic">' in prompt
    assert '<lens name="minimalist" label="Minimalist">' in prompt
    assert '<lens name="architect" label="Architect">' not in prompt
    assert "focus on complexity" in prompt


def test_adversarial_review_accepts_repeated_lens_flags(tmp_path):
    result, prompt, _argv = run_fake_claude_adversarial_review(
        tmp_path,
        ["--adversarial-lens", "architect", "--adversarial-lens", "skeptic"],
    )

    assert result.returncode == 0
    assert '<lens name="architect" label="Architect">' in prompt
    assert '<lens name="skeptic" label="Skeptic">' in prompt
    assert '<lens name="minimalist" label="Minimalist">' not in prompt


def test_adversarial_review_rejects_unknown_lens_before_claude(tmp_path):
    result, prompt, _argv = run_fake_claude_adversarial_review(
        tmp_path,
        ["--adversarial-lens", "optimist"],
    )

    assert result.returncode == 2
    assert 'Unknown adversarial lens "optimist"' in result.stderr
    assert prompt == ""


def test_adversarial_review_rejects_multi_review_roles_before_claude(tmp_path):
    result, prompt, _argv = run_fake_claude_adversarial_review(
        tmp_path,
        ["--roles", "skeptic,architect"],
    )

    assert result.returncode == 2
    assert "--roles is only valid for multi-review; use --adversarial-lenses for adversarial-review." in result.stderr
    assert prompt == ""


def test_multi_review_default_roles_run_once_each(tmp_path):
    result, prompts, argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert len(prompts) == 5
    assert len(argvs) == 5
    assert result.stdout.startswith("# Claude Multi-Agent Review")
    for role in ["correctness", "security", "tests", "release", "adversarial"]:
        assert f"## Role: {role}" in result.stdout
        assert result.stdout.count(f"## Role: {role}") == 1
        assert f"<role_name>{role}</role_name>" in "\n".join(prompts)
    assert "roles requested: correctness, security, tests, release, adversarial" in result.stdout
    assert "roles succeeded: correctness, security, tests, release, adversarial" in result.stdout
    assert "roles failed: (none)" in result.stdout


def test_multi_review_failed_role_preserves_partial_results_and_continues(tmp_path):
    result, prompts, _argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--roles", "correctness,security,tests", "--scope", "working-tree"],
        fail_roles=["security"],
    )

    assert result.returncode == 1
    assert [re.search(r"<role_name>([^<]+)</role_name>", prompt).group(1) for prompt in prompts] == [
        "correctness",
        "security",
        "tests",
    ]
    assert re.findall(r"^## Role: (.+)$", result.stdout, re.M) == ["correctness", "security", "tests"]
    assert "FAKE_CLAUDE_OK call 0" in result.stdout
    assert "FAKE_CLAUDE_FAIL role security call 1" in result.stdout
    assert "Role failed with exit status 17." in result.stdout
    assert "stderr: diagnostic for failed role security" in result.stdout
    assert "FAKE_CLAUDE_OK call 2" in result.stdout
    assert "roles requested: correctness, security, tests" in result.stdout
    assert "roles succeeded: correctness, tests" in result.stdout
    assert "roles failed: security" in result.stdout
    assert "exit policy: exits non-zero if any role fails; completed role output remains visible." in result.stdout


def test_multi_review_roles_subset_and_headers_keep_order(tmp_path):
    result, prompts, _argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--roles", "correctness,security", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert [re.search(r"<role_name>([^<]+)</role_name>", prompt).group(1) for prompt in prompts] == [
        "correctness",
        "security",
    ]
    assert re.findall(r"^## Role: (.+)$", result.stdout, re.M) == ["correctness", "security"]
    assert "roles requested: correctness, security" in result.stdout


def test_multi_review_supports_adversarial_lens_roles(tmp_path):
    result, prompts, _argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--roles", "skeptic,architect,minimalist"],
    )

    assert result.returncode == 0
    assert len(prompts) == 3
    assert "<role_name>skeptic</role_name>" in prompts[0]
    assert "<role_name>architect</role_name>" in prompts[1]
    assert "<role_name>minimalist</role_name>" in prompts[2]
    assert "Challenge correctness and completeness." in prompts[0]
    assert "Challenge structural fitness." in prompts[1]
    assert "Challenge necessity and complexity." in prompts[2]


def test_multi_review_calls_use_read_only_flags_and_prompt_last(tmp_path):
    result, prompts, argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--roles", "correctness,security", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    for prompt, argv in zip(prompts, argvs):
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
        assert argv[argv.index("--disallowedTools") + 1] == "Edit,Write,MultiEdit,Bash"
        assert argv[argv.index("--output-format") + 1] == "text"
        assert argv[-1] == prompt


def test_multi_review_model_and_effort_apply_to_each_role(tmp_path):
    result, _prompts, argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--roles", "correctness,security", "--model", "sonnet-test", "--effort", "high"],
    )

    assert result.returncode == 0, result.stderr
    for argv in argvs:
        assert argv[argv.index("--model") + 1] == "sonnet-test"
        assert argv[argv.index("--effort") + 1] == "high"


def test_multi_review_scope_branch_omits_working_tree_context_in_every_prompt(tmp_path):
    result, prompts, _argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--roles", "correctness,security", "--scope", "branch", "--base", "HEAD~1"],
        commit_head=True,
    )

    assert result.returncode == 0, result.stderr
    for prompt in prompts:
        assert "scope: branch" in prompt
        assert "git diff --stat HEAD~1...HEAD" in prompt
        assert "git diff --name-only HEAD~1...HEAD" in prompt
        assert "branch.txt" in prompt
        assert "git status --short --untracked-files=all" not in prompt
        assert "git diff --cached --stat" not in prompt
        assert "working tree change" not in prompt


def test_multi_review_repeated_path_options_filter_every_prompt(tmp_path):
    result, prompts, _argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--roles", "correctness,security", "--scope", "working-tree", "--path", "sample.txt", "--path", "other.txt"],
    )

    assert result.returncode == 0, result.stderr
    for prompt in prompts:
        assert "paths: sample.txt other.txt" in prompt
        assert "git status --short --untracked-files=all -- sample.txt other.txt" in prompt
        assert "sample.txt" in prompt
        assert "sample change" in prompt
        assert "other.txt" in prompt
        assert "other change" in prompt
        assert "working.txt" not in prompt


@pytest.mark.parametrize(
    "roles",
    ["correctness,,security", "correctness,", ",correctness"],
)
def test_empty_comma_separated_review_role_exits_2_without_calling_claude(tmp_path, roles):
    result, prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--roles", roles, "--scope", "working-tree"],
    )

    assert result.returncode == 2
    assert "Missing role in --roles" in result.stderr
    assert prompt == ""
    assert argv == []


@pytest.mark.parametrize(
    "args",
    [
        ["--role", "security", "--role", "security"],
        ["--roles", "security,security"],
    ],
)
def test_duplicate_review_role_exits_2_without_calling_claude(tmp_path, args):
    result, prompt, argv = run_fake_claude_review(
        tmp_path,
        [*args, "--scope", "working-tree"],
    )

    assert result.returncode == 2
    assert 'Duplicate review role "security".' in result.stderr
    assert prompt == ""
    assert argv == []


@pytest.mark.parametrize("option", ["--roles", "--role"])
def test_missing_review_role_option_value_exits_2_without_calling_claude(tmp_path, option):
    result, prompt, argv = run_fake_claude_review(tmp_path, [option, "--scope", "auto"])

    assert result.returncode == 2
    assert f"Missing value for {option}" in result.stderr
    assert prompt == ""
    assert argv == []


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
calls_path = capture / "argv.json"
calls = json.loads(calls_path.read_text()) if calls_path.exists() else []
calls.append(sys.argv[1:])
calls_path.write_text(json.dumps(calls))
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
    payload = json.loads(result.stdout)
    assert payload["claudeAgents"] == []
    assert payload["reviewGate"]["enabled"] is False
    assert payload["reviewGate"]["mode"] == "multi-role"
    argvs = json.loads((capture_dir / "argv.json").read_text())
    assert ["agents", "--json", "--cwd", str(repo)] in argvs


def test_setup_state_file_is_outside_repo_and_corruption_is_reported(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()

    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    enabled = subprocess.run(
        [NODE, str(runtime), "setup", "--enable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert enabled.returncode == 0, enabled.stderr
    state_file = pathlib.Path(json.loads(enabled.stdout)["reviewGate"]["stateFile"])
    assert data in state_file.parents
    assert repo not in state_file.parents

    state_file.write_text("{not json")
    status = subprocess.run(
        [NODE, str(runtime), "setup"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert status.returncode == 1
    payload = json.loads(status.stdout)
    assert payload["reviewGate"]["stateReadable"] is False
    assert "corrupt" in payload["reviewGate"]["stateError"].lower()


def test_setup_reports_lifecycle_hook_support_and_job_commands(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    fake_claude = fake_bin / "claude"
    fake_claude.write_text("#!/usr/bin/env sh\necho claude fake\n")
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "setup"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["jobCommands"] == ["jobs", "result", "cancel", "rescue"]
    assert payload["hooks"]["manifest"] == "hooks/hooks.json"
    assert payload["hooks"]["events"] == ["SessionStart", "SessionEnd", "UserPromptSubmit", "Stop"]


def test_empty_jobs_result_and_cancel_are_isolated_to_temp_plugin_data(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    jobs = subprocess.run(
        [NODE, str(runtime), "jobs"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert jobs.returncode == 0, jobs.stderr
    jobs_payload = json.loads(jobs.stdout)
    assert jobs_payload["jobs"] == []
    assert data in pathlib.Path(jobs_payload["stateDir"]).parents

    result = subprocess.run(
        [NODE, str(runtime), "result", "missing"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "not_found"

    cancel = subprocess.run(
        [NODE, str(runtime), "cancel", "missing"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert cancel.returncode == 1
    assert json.loads(cancel.stdout)["status"] == "not_found"


def test_result_marks_viewed_and_cancel_persists_queued_job_state(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    jobs = subprocess.run(
        [NODE, str(runtime), "jobs"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert jobs.returncode == 0, jobs.stderr
    state_dir = pathlib.Path(json.loads(jobs.stdout)["stateDir"])
    jobs_dir = state_dir / "jobs"
    job_file = jobs_dir / "job-1.json"
    job_file.write_text(json.dumps({
        "id": "job-1",
        "status": "queued",
        "createdAt": "2026-05-30T00:00:00.000Z",
        "result": "ready"
    }))

    result = subprocess.run(
        [NODE, str(runtime), "result", "job-1"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result_payload = json.loads(result.stdout)
    assert result_payload["status"] == "ok"
    assert result_payload["job"]["resultViewedAt"]
    assert json.loads(job_file.read_text())["resultViewedAt"]

    cancel = subprocess.run(
        [NODE, str(runtime), "cancel", "job-1"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert cancel.returncode == 0, cancel.stderr
    cancel_payload = json.loads(cancel.stdout)
    assert cancel_payload["status"] == "cancelled"
    persisted = json.loads(job_file.read_text())
    assert persisted["status"] == "cancelled"
    assert persisted["cancelledAt"]


def test_background_review_wait_records_succeeded_job_result(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
print("BACKGROUND_REVIEW_OK")
"""
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    started = subprocess.run(
        [NODE, str(runtime), "review", "--background", "--wait", "check background"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert started.returncode == 0, started.stderr
    payload = json.loads(started.stdout)
    assert payload["status"] == "succeeded"
    assert payload["job"]["command"] == "review"
    assert payload["job"]["stdout"].strip() == "BACKGROUND_REVIEW_OK"

    result = subprocess.run(
        [NODE, str(runtime), "result", payload["job"]["id"]],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result_payload = json.loads(result.stdout)
    assert result_payload["job"]["resultViewedAt"]
    assert result_payload["job"]["stdout"].strip() == "BACKGROUND_REVIEW_OK"


def test_background_multi_review_failure_is_recorded_as_failed_job(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
print("role failed", file=sys.stderr)
raise SystemExit(17)
"""
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    started = subprocess.run(
        [NODE, str(runtime), "multi-review", "--background", "--wait", "--roles", "correctness"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert started.returncode == 1
    payload = json.loads(started.stdout)
    assert payload["status"] == "failed"
    assert payload["job"]["command"] == "multi-review"
    assert payload["job"]["exitStatus"] == 1
    assert "Role failed with exit status 17." in payload["job"]["stdout"]


def test_internal_job_worker_rejects_unsupported_job_command(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    jobs = subprocess.run(
        [NODE, str(runtime), "jobs"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert jobs.returncode == 0, jobs.stderr
    state_dir = pathlib.Path(json.loads(jobs.stdout)["stateDir"])
    job_file = state_dir / "jobs" / "job-bad.json"
    job_file.write_text(json.dumps({
        "id": "job-bad",
        "status": "queued",
        "command": "status",
        "args": [],
        "cwd": str(repo)
    }))

    worker = subprocess.run(
        [NODE, str(runtime), "__run-job", "job-bad"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert worker.returncode == 2
    payload = json.loads(job_file.read_text())
    assert payload["status"] == "failed"
    assert "Unsupported background job command" in payload["stderr"]


def test_running_cancel_refuses_unvalidated_pid_and_corrupt_result_is_reported(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    jobs = subprocess.run(
        [NODE, str(runtime), "jobs"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert jobs.returncode == 0, jobs.stderr
    state_dir = pathlib.Path(json.loads(jobs.stdout)["stateDir"])
    jobs_dir = state_dir / "jobs"
    (jobs_dir / "job-running.json").write_text(json.dumps({
        "id": "job-running",
        "status": "running",
        "workerPid": 999999,
        "createdAt": "2026-05-30T00:00:00.000Z"
    }))
    (jobs_dir / "job-corrupt.json").write_text("{bad json")

    cancel = subprocess.run(
        [NODE, str(runtime), "cancel", "job-running"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert cancel.returncode == 1
    cancel_payload = json.loads(cancel.stdout)
    assert cancel_payload["status"] == "cancel_failed"
    assert "identity validation" in cancel_payload["reason"]

    result = subprocess.run(
        [NODE, str(runtime), "result", "job-corrupt"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    result_payload = json.loads(result.stdout)
    assert result_payload["status"] == "corrupt"
    assert result_payload["job"]["stateError"]


def test_running_cancel_persists_failed_state_for_missing_worker_pid(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    jobs = subprocess.run(
        [NODE, str(runtime), "jobs"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    state_dir = pathlib.Path(json.loads(jobs.stdout)["stateDir"])
    job_file = state_dir / "jobs" / "job-no-pid.json"
    job_file.write_text(json.dumps({
        "id": "job-no-pid",
        "status": "running",
        "createdAt": "2026-05-30T00:00:00.000Z"
    }))

    cancel = subprocess.run(
        [NODE, str(runtime), "cancel", "job-no-pid"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert cancel.returncode == 1
    payload = json.loads(cancel.stdout)
    assert payload["status"] == "cancel_failed"
    assert "workerPid" in payload["reason"]
    assert json.loads(job_file.read_text())["status"] == "cancel_failed"


def test_cancel_running_background_job_validates_and_terminates_worker(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import time
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
time.sleep(30)
print("SHOULD_NOT_FINISH")
"""
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    started = subprocess.run(
        [NODE, str(runtime), "review", "--background", "long review"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert started.returncode == 0, started.stderr
    job_id = json.loads(started.stdout)["job"]["id"]

    job = {}
    for _ in range(50):
        jobs = subprocess.run(
            [NODE, str(runtime), "jobs"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
        )
        payload = json.loads(jobs.stdout)
        job = next(item for item in payload["jobs"] if item["id"] == job_id)
        if job["status"] == "running":
            break
        time.sleep(0.1)
    assert job["status"] == "running"
    assert job["pidIdentity"]["command"]

    cancel = subprocess.run(
        [NODE, str(runtime), "cancel", job_id],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert cancel.returncode == 0, cancel.stderr
    cancel_payload = json.loads(cancel.stdout)
    assert cancel_payload["status"] == "cancelled"
    assert cancel_payload["job"]["cancelIdentity"]["pid"] == job["workerPid"]


def test_session_and_user_prompt_hooks_write_state_and_report_unread_results(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    session_hook = PLUGIN / "hooks" / "session-lifecycle.mjs"
    prompt_hook = PLUGIN / "hooks" / "unread-result.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n")
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    started = subprocess.run(
        [NODE, str(session_hook), "SessionStart"],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "SessionStart", "cwd": str(repo), "session_id": "s1"}),
        capture_output=True,
        text=True,
    )
    assert started.returncode == 0, started.stderr

    jobs = subprocess.run(
        [NODE, str(runtime), "jobs"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert jobs.returncode == 0, jobs.stderr
    state_dir = pathlib.Path(json.loads(jobs.stdout)["stateDir"])
    assert json.loads((state_dir / "current-session.json").read_text())["sessionId"] == "s1"
    jobs_dir = state_dir / "jobs"
    (jobs_dir / "job-other-session.json").write_text(json.dumps({
        "id": "job-other-session",
        "status": "queued",
        "sessionId": "other",
        "createdAt": "2026-05-30T00:00:00.000Z"
    }))
    (jobs_dir / "job-2.json").write_text(json.dumps({
        "id": "job-2",
        "status": "succeeded",
        "sessionId": "s1",
        "createdAt": "2026-05-30T00:00:00.000Z",
        "stdout": "done"
    }))
    (jobs_dir / "job-other-finished.json").write_text(json.dumps({
        "id": "job-other-finished",
        "status": "succeeded",
        "sessionId": "other",
        "createdAt": "2026-05-30T00:00:00.000Z",
        "stdout": "done"
    }))

    prompted = subprocess.run(
        [NODE, str(prompt_hook)],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(repo), "session_id": "s1"}),
        capture_output=True,
        text=True,
    )
    assert prompted.returncode == 0
    assert "job-2 (succeeded)" in prompted.stderr
    assert "job-other-finished" not in prompted.stderr
    baseline = json.loads((state_dir / "turn-baseline.json").read_text())
    assert baseline["sessionId"] == "s1"
    assert baseline["workingTreeFingerprint"]

    ended = subprocess.run(
        [NODE, str(session_hook), "SessionEnd"],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "SessionEnd", "cwd": str(repo), "session_id": "s1"}),
        capture_output=True,
        text=True,
    )
    assert ended.returncode == 0, ended.stderr
    assert json.loads((jobs_dir / "job-other-session.json").read_text())["status"] == "queued"


def test_rescue_defaults_read_only_and_write_mode_requires_explicit_flag(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    repo.mkdir()
    fake_bin.mkdir()
    capture_dir.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "broken.txt").write_text("broken\n")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)

capture = pathlib.Path(os.environ["CAPTURE_DIR"])
(capture / "argv.json").write_text(json.dumps(sys.argv[1:]))
(capture / "prompt.txt").write_text(sys.argv[-1])
print("RESCUE_OK")
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "rescue", "diagnose failure"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    argv = json.loads((capture_dir / "argv.json").read_text())
    prompt = (capture_dir / "prompt.txt").read_text()
    assert "--disallowedTools" in argv
    assert "Edit,Write,MultiEdit,Bash" in argv
    assert "Diagnose a stuck or failed Codex implementation" in prompt

    write_mode = subprocess.run(
        [NODE, str(runtime), "rescue", "--write", "fix failure"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert write_mode.returncode == 0, write_mode.stderr
    write_argv = json.loads((capture_dir / "argv.json").read_text())
    write_prompt = (capture_dir / "prompt.txt").read_text()
    assert write_argv[write_argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--disallowedTools" not in write_argv
    assert "explicitly requested rescue --write" in write_prompt
    assert "write-mode fingerprint" in write_mode.stderr


def test_adversarial_review_json_output_extracts_and_validates_mixed_claude_output(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
print('Here is JSON:')
print('```json')
print('{"verdict":"PASS","summary":"ok","findings":[],"next_steps":[]}')
print('```')
"""
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "adversarial-review", "--json", "check structured output"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {"verdict": "PASS", "summary": "ok", "findings": [], "next_steps": []}


def test_read_only_git_helper_rejects_unknown_commands(tmp_path):
    helper = PLUGIN / "scripts" / "lib" / "mcp-git.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)

    ok = subprocess.run(
        [NODE, str(helper), "status"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["status"] == 0

    rejected = subprocess.run(
        [NODE, str(helper), "commit"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 1
    assert json.loads(rejected.stdout)["status"] == 2


def test_setup_uses_claude_code_path_when_path_omits_claude(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    fake_bin.mkdir()

    fake_claude = fake_bin / "claude-custom"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import sys

if sys.argv[1:] == ["--version"]:
    print("claude custom")
    raise SystemExit(0)

print("unexpected", file=sys.stderr)
raise SystemExit(2)
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CLAUDE_CODE_PATH"] = str(fake_claude)
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    result = subprocess.run(
        [NODE, str(runtime), "setup"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["claudeAvailable"] is True
    assert payload["claudeCommand"] == str(fake_claude)


def test_setup_falls_back_to_home_local_bin_claude_when_path_omits_claude(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    repo.mkdir()
    local_bin.mkdir(parents=True)

    fake_claude = local_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import sys

if sys.argv[1:] == ["--version"]:
    print("claude home fallback")
    raise SystemExit(0)

print("unexpected", file=sys.stderr)
raise SystemExit(2)
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env.pop("CLAUDE_CODE_PATH", None)
    env["HOME"] = str(home)
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    result = subprocess.run(
        [NODE, str(runtime), "setup"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["claudeAvailable"] is True
    assert payload["claudeCommand"] == str(fake_claude)


def test_setup_can_enable_and_disable_review_gate(tmp_path):
    runtime, repo, _capture_dir, env = prepare_gate_repo(tmp_path)

    enabled = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate", "--review-gate-mode", "multi-role"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert enabled.returncode == 0, enabled.stderr
    enabled_payload = json.loads(enabled.stdout)
    assert enabled_payload["reviewGate"]["enabled"] is True
    assert enabled_payload["reviewGate"]["mode"] == "multi-role"
    assert pathlib.Path(enabled_payload["reviewGate"]["stateFile"]).exists()

    disabled = subprocess.run(
        ["node", str(runtime), "setup", "--disable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert disabled.returncode == 0, disabled.stderr
    assert json.loads(disabled.stdout)["reviewGate"]["enabled"] is False


def test_review_gate_disabled_does_not_call_claude(tmp_path):
    result, prompts, _capture_dir = run_fake_review_gate(tmp_path, enable=False)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert prompts == []


def test_review_gate_skips_without_git_changes(tmp_path):
    result, prompts, _capture_dir = run_fake_review_gate(tmp_path, with_change=False)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert prompts == []


def test_review_gate_skips_when_stop_hook_already_active(tmp_path):
    runtime, repo, _capture_dir, env = prepare_gate_repo(tmp_path)
    setup = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr
    result = subprocess.run(
        ["node", str(runtime), "review-gate"],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "Stop", "cwd": str(repo), "stop_hook_active": True}),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_review_gate_skips_when_turn_baseline_matches_current_diff(tmp_path):
    runtime, repo, capture_dir, env = prepare_gate_repo(tmp_path)
    setup = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr
    state_file = pathlib.Path(json.loads(setup.stdout)["reviewGate"]["stateFile"])
    state_dir = state_file.parent
    status = subprocess.run(["git", "status", "--short", "--untracked-files=all"], cwd=repo, check=True, capture_output=True, text=True).stdout
    staged = subprocess.run(["git", "diff", "--cached"], cwd=repo, check=True, capture_output=True, text=True).stdout
    unstaged = subprocess.run(["git", "diff"], cwd=repo, check=True, capture_output=True, text=True).stdout
    diff_hash = hashlib.sha256("\n--- claude-for-codex ---\n".join([status, staged, unstaged]).encode()).hexdigest()
    (state_dir / "turn-baseline.json").write_text(json.dumps({
        "sessionId": "s1",
        "workingTreeFingerprint": diff_hash
    }))

    result = subprocess.run(
        ["node", str(runtime), "review-gate"],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "Stop", "cwd": str(repo)}),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert list(capture_dir.glob("prompt-*.txt")) == []


def test_review_gate_all_allow_exits_without_block(tmp_path):
    result, prompts, _capture_dir = run_fake_review_gate(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert len(prompts) == 5
    for role in ["correctness", "security", "tests", "release", "adversarial"]:
        assert f"<role_name>{role}</role_name>" in "\n".join(prompts)
        assert "Your first line must be exactly one of:" in "\n".join(prompts)


def test_review_gate_skips_unchanged_diff_after_allow(tmp_path):
    runtime, repo, capture_dir, env = prepare_gate_repo(tmp_path)
    setup = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr
    hook_input = json.dumps({"hook_event_name": "Stop", "cwd": str(repo)})

    first = subprocess.run(
        ["node", str(runtime), "review-gate"],
        cwd=repo,
        env=env,
        input=hook_input,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        ["node", str(runtime), "review-gate"],
        cwd=repo,
        env=env,
        input=hook_input,
        capture_output=True,
        text=True,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    prompts = list(capture_dir.glob("prompt-*.txt"))
    assert len(prompts) == 5
    assert second.stdout == ""
    assert second.stderr == ""


def test_review_gate_block_outputs_stop_decision_json(tmp_path):
    result, prompts, _capture_dir = run_fake_review_gate(
        tmp_path,
        extra_env={"BLOCK_ROLES": "security,tests"},
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "security: blocking issue from security" in payload["reason"]
    assert "tests: blocking issue from tests" in payload["reason"]
    assert len(prompts) == 5


def test_review_gate_wrapper_forwards_block_and_exits_zero(tmp_path):
    runtime, repo, _capture_dir, env = prepare_gate_repo(tmp_path)
    setup = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr
    env["BLOCK_ROLES"] = "security"
    wrapper = PLUGIN / "hooks" / "claude-review-gate.mjs"
    result = subprocess.run(
        ["node", str(wrapper)],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "Stop", "cwd": str(repo)}),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "security: blocking issue from security" in payload["reason"]


def test_review_gate_claude_failure_warns_but_does_not_block(tmp_path):
    result, prompts, _capture_dir = run_fake_review_gate(
        tmp_path,
        extra_env={"FAIL_ROLES": "security"},
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert "role security failed; allowing stop" in result.stderr
    assert len(prompts) == 5


def test_review_gate_invalid_claude_output_warns_but_does_not_block(tmp_path):
    result, prompts, _capture_dir = run_fake_review_gate(
        tmp_path,
        extra_env={"INVALID_ROLES": "release"},
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert "role release returned invalid gate output; allowing stop" in result.stderr
    assert len(prompts) == 5


def test_review_gate_env_bypass_skips_claude(tmp_path):
    result, prompts, _capture_dir = run_fake_review_gate(
        tmp_path,
        extra_env={"CLAUDE_FOR_CODEX_REVIEW_GATE": "off"},
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert prompts == []


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
            "--effort",
            "low",
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
    expected_commands = {
        "claude-adversarial-review": "adversarial-review",
        "claude-cancel": "cancel",
        "claude-collaboration-loop": "plan",
        "claude-multi-review": "multi-review",
        "claude-plan": "plan",
        "claude-rescue": "rescue",
        "claude-result": "result",
        "claude-review-gate": "review-gate",
        "claude-review": "review",
        "claude-status": "jobs",
    }
    assert {p.parent.name for p in skills} == {
        "claude-adversarial-review",
        "claude-cancel",
        "claude-collaboration-loop",
        "claude-multi-review",
        "claude-plan",
        "claude-rescue",
        "claude-result",
        "claude-review-gate",
        "claude-review",
        "claude-status",
    }
    for skill in skills:
        text = skill.read_text()
        frontmatter = parse_skill_frontmatter(text)
        assert frontmatter["name"] == skill.parent.name
        assert frontmatter["description"]
        assert "claude-companion.mjs" in text
        assert f'claude-companion.mjs" {expected_commands[skill.parent.name]}' in text
