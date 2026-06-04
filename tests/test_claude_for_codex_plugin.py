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
DEFAULT_MULTI_REVIEW_ROLES_FOR_TESTS = ["correctness", "security", "tests", "release", "adversarial"]


def run_fake_claude_review(tmp_path, args, commit_head=False, extra_env=None):
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
print(os.environ.get("FAKE_CLAUDE_STDOUT", "FAKE_CLAUDE_OK"))
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    if extra_env:
        env.update(extra_env)
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


def write_semantic_provider(tmp_path, *, response=None, extra_script="", config_in_repo=None):
    provider_dir = tmp_path / "semantic-bin"
    config_dir = tmp_path / "semantic-config"
    provider_dir.mkdir(exist_ok=True)
    config_dir.mkdir(exist_ok=True)
    provider = provider_dir / "fake-semantic"
    response = response or {
        "version": 1,
        "provider": "fake",
        "items": [
            {
                "path": "sample.txt",
                "symbol": "sample",
                "summary": "semantic summary marker",
                "reason": "related changed file",
            }
        ],
        "warnings": [],
    }
    provider.write_text(
        f"""#!/usr/bin/env python3
import json
import os
import pathlib
import sys

capture = pathlib.Path(os.environ["SEMANTIC_PROVIDER_CAPTURE"])
capture.mkdir(parents=True, exist_ok=True)
(capture / "request.json").write_text(sys.stdin.read())
(capture / "env.json").write_text(json.dumps(dict(os.environ), sort_keys=True))
{extra_script}
print({json.dumps(json.dumps(response))})
"""
    )
    provider.chmod(0o755)

    config_path = (config_in_repo / "providers.json") if config_in_repo else (config_dir / "providers.json")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({
        "providers": {
            "fake": {
                "command": [str(provider)],
                "env": {"SEMANTIC_PROVIDER_CAPTURE": str(tmp_path / "semantic-capture")},
                "timeoutMs": 5000,
                "maxOutputBytes": 32768,
            }
        },
        "defaultProvider": "fake",
    }))
    config_path.chmod(0o600)
    return provider, config_path, tmp_path / "semantic-capture"


def write_fake_claude_sdk(tmp_path, *, stdout="SDK_OK", extra_js=""):
    sdk_dir = tmp_path / "fake-claude-sdk"
    capture = tmp_path / "sdk-capture"
    sdk_dir.mkdir()
    capture.mkdir()
    (sdk_dir / "package.json").write_text(json.dumps({
        "name": "@anthropic-ai/claude-code",
        "version": "9.8.7-test",
        "type": "module",
        "main": "index.mjs",
    }), encoding="utf8")
    (sdk_dir / "index.mjs").write_text(
        f"""
import fs from 'node:fs';
const capture = {json.dumps(str(capture))};
export async function* query(input) {{
  fs.writeFileSync(`${{capture}}/query.json`, JSON.stringify(input, null, 2));
  {extra_js}
  yield {{
    type: 'result',
    subtype: 'success',
    result: {json.dumps(stdout)},
    session_id: 'sdk-session-secret',
    total_cost_usd: 0.01,
    usage: {{ input_tokens: 3, output_tokens: 4 }}
  }};
}}
""",
        encoding="utf8",
    )
    return sdk_dir / "index.mjs", capture


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


def run_fake_claude_multi_review(tmp_path, args, commit_head=False, fail_roles=None, extra_env=None):
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
prompt = sys.argv[-1]
role = "unknown"
for candidate in ["correctness", "security", "tests", "release", "adversarial", "skeptic", "architect", "minimalist"]:
    if f"<role_name>{candidate}</role_name>" in prompt:
        role = candidate
(capture / f"argv-{role}.json").write_text(json.dumps(sys.argv[1:]))
(capture / f"prompt-{role}.txt").write_text(prompt)
fail_roles = [role for role in os.environ.get("FAIL_ROLES", "").split(",") if role]
for fail_role in fail_roles:
    if role == fail_role:
        print(f"FAKE_CLAUDE_FAIL role {role} call {role}")
        print(f"diagnostic for failed role {role}", file=sys.stderr)
        raise SystemExit(17)
json_by_role = json.loads(os.environ.get("JSON_BY_ROLE", "{}") or "{}")
if role in json_by_role:
    print(json.dumps(json_by_role[role]))
    raise SystemExit(0)
print(f"FAKE_CLAUDE_OK call {role}")
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    if fail_roles:
        env["FAIL_ROLES"] = ",".join(fail_roles)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["node", str(runtime), "multi-review", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    requested_roles = DEFAULT_MULTI_REVIEW_ROLES_FOR_TESTS.copy()
    if "--roles" in args:
        requested_roles = args[args.index("--roles") + 1].split(",")
    repeated_roles = [args[index + 1] for index, value in enumerate(args[:-1]) if value == "--role"]
    if repeated_roles:
        requested_roles = repeated_roles
    prompts = [
        (capture_dir / f"prompt-{role}.txt").read_text()
        for role in requested_roles
        if (capture_dir / f"prompt-{role}.txt").exists()
    ]
    argvs = [
        json.loads((capture_dir / f"argv-{role}.json").read_text())
        for role in requested_roles
        if (capture_dir / f"argv-{role}.json").exists()
    ]
    return result, prompts, argvs


def test_multi_review_agent_team_flags_are_validated(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"

    invalid_team = subprocess.run(
        [NODE, str(runtime), "multi-review", "--agent-team", "banana"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert invalid_team.returncode == 2
    assert "Invalid --agent-team" in invalid_team.stderr

    sequential_subagents = subprocess.run(
        [NODE, str(runtime), "multi-review", "--agent-team", "sdk-subagents", "--sequential"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert sequential_subagents.returncode == 2
    assert "--agent-team sdk-subagents cannot be combined with --sequential" in sequential_subagents.stderr


def test_review_rejects_native_agent_team_flag_before_claude(tmp_path):
    result, prompt, argv = run_fake_claude_review(tmp_path, ["--agent-team", "banana"])

    assert result.returncode == 2
    assert prompt == ""
    assert argv == []
    assert "Unsupported option --agent-team" in result.stderr
    assert "only valid for multi-review" in result.stderr


@pytest.mark.parametrize("command", ["status", "capabilities", "jobs"])
def test_utility_commands_reject_native_agent_team_flag(tmp_path, command):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"

    result = subprocess.run(
        [NODE, str(runtime), command, "--agent-team", "sdk-subagents"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Unsupported option --agent-team" in result.stderr
    assert "only valid for multi-review" in result.stderr


def test_multi_review_max_budget_usd_requires_decimal(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"

    result = subprocess.run(
        [NODE, str(runtime), "multi-review", "--max-budget-usd", "0x10"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "positive decimal" in result.stderr


def test_multi_review_sdk_backend_rejects_max_budget_usd(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"

    result = subprocess.run(
        [NODE, str(runtime), "multi-review", "--backend", "sdk", "--max-budget-usd", "1.50"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--max-budget-usd" in result.stderr
    assert "CLI-only" in result.stderr or "unsupported for SDK backend" in result.stderr


def test_multi_review_sdk_backend_rejects_fallback_model(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"

    result = subprocess.run(
        [NODE, str(runtime), "multi-review", "--backend", "sdk", "--fallback-model", "claude-sonnet-4-5"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--fallback-model" in result.stderr
    assert "CLI-only" in result.stderr or "unsupported for SDK backend" in result.stderr


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--max-budget-usd", "1.50"),
        ("--fallback-model", "claude-sonnet-4-5"),
    ],
)
def test_multi_review_env_sdk_backend_rejects_cli_only_options(tmp_path, flag, value):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_BACKEND"] = "sdk"

    result = subprocess.run(
        [NODE, str(runtime), "multi-review", flag, value],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert flag in result.stderr
    assert "CLI-only" in result.stderr or "unsupported for SDK backend" in result.stderr


def test_multi_review_forwards_native_budget_and_fallback_model(tmp_path):
    result, _prompts, argvs = run_fake_claude_multi_review(
        tmp_path,
        [
            "--roles",
            "correctness",
            "--scope",
            "working-tree",
            "--max-budget-usd",
            "1.50",
            "--fallback-model",
            "claude-sonnet-4-5",
        ],
    )

    assert result.returncode == 0, result.stderr
    assert len(argvs) == 1
    argv = argvs[0]
    assert argv[argv.index("--max-budget-usd") + 1] == "1.50"
    assert argv[argv.index("--fallback-model") + 1] == "claude-sonnet-4-5"
    assert argv.index("--max-budget-usd") < len(argv) - 1
    assert argv.index("--fallback-model") < len(argv) - 1


def test_multi_review_env_sdk_backend_allows_explicit_cli_budget_flags(tmp_path):
    result, _prompts, argvs = run_fake_claude_multi_review(
        tmp_path,
        [
            "--backend",
            "cli",
            "--roles",
            "correctness",
            "--scope",
            "working-tree",
            "--max-budget-usd",
            "1.50",
            "--fallback-model",
            "claude-sonnet-4-5",
        ],
        extra_env={"CLAUDE_FOR_CODEX_BACKEND": "sdk"},
    )

    assert result.returncode == 0, result.stderr
    assert len(argvs) == 1
    argv = argvs[0]
    assert argv[argv.index("--max-budget-usd") + 1] == "1.50"
    assert argv[argv.index("--fallback-model") + 1] == "claude-sonnet-4-5"


def test_ultrareview_requires_explicit_consent(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"

    result = subprocess.run(
        [NODE, str(runtime), "ultrareview"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--confirm-cost" in result.stderr


def test_multi_review_runs_roles_in_parallel_by_default(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    release_file = capture_dir / "release"
    repo.mkdir()
    fake_bin.mkdir()
    capture_dir.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "sample.txt").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "sample.txt").write_text("base\nchange\n")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import re
import sys
import time

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)

capture = pathlib.Path(os.environ["CAPTURE_DIR"])
release = pathlib.Path(os.environ["RELEASE_FILE"])
prompt = sys.argv[-1]
match = re.search(r"<role_name>([^<]+)</role_name>", prompt)
role = match.group(1) if match else "unknown"
(capture / f"started-{role}").write_text(str(os.getpid()))
deadline = time.time() + 10
while not release.exists() and time.time() < deadline:
    time.sleep(0.05)
if not release.exists():
    print(f"timeout waiting for release {role}", file=sys.stderr)
    raise SystemExit(19)
print(f"FAKE_CLAUDE_OK role {role}")
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["RELEASE_FILE"] = str(release_file)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    proc = subprocess.Popen(
        [NODE, str(runtime), "multi-review", "--roles", "correctness,security,tests", "--scope", "working-tree"],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 5
        while len(list(capture_dir.glob("started-*"))) < 3 and time.time() < deadline:
            time.sleep(0.05)
        assert sorted(path.name for path in capture_dir.glob("started-*")) == [
            "started-correctness",
            "started-security",
            "started-tests",
        ]
        release_file.write_text("go")
        stdout, stderr = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)

    assert proc.returncode == 0, stderr
    assert "execution mode: parallel" in stdout
    assert "FAKE_CLAUDE_OK role correctness" in stdout
    assert "FAKE_CLAUDE_OK role security" in stdout
    assert "FAKE_CLAUDE_OK role tests" in stdout


def test_adversarial_review_parallel_spawns_lens_reviewers(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    release_file = capture_dir / "release"
    repo.mkdir()
    fake_bin.mkdir()
    capture_dir.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "sample.txt").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "sample.txt").write_text("base\nchange\n")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import re
import sys
import time

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)

capture = pathlib.Path(os.environ["CAPTURE_DIR"])
release = pathlib.Path(os.environ["RELEASE_FILE"])
prompt = sys.argv[-1]
match = re.search(r"<role_name>([^<]+)</role_name>", prompt)
lens = match.group(1) if match else "unknown"
(capture / f"started-{lens}").write_text(str(os.getpid()))
deadline = time.time() + 10
while not release.exists() and time.time() < deadline:
    time.sleep(0.05)
if not release.exists():
    print(f"timeout waiting for release {lens}", file=sys.stderr)
    raise SystemExit(19)
print(f"FAKE_CLAUDE_OK lens {lens}")
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["RELEASE_FILE"] = str(release_file)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    proc = subprocess.Popen(
        [NODE, str(runtime), "adversarial-review", "--parallel", "--scope", "working-tree"],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 5
        while len(list(capture_dir.glob("started-*"))) < 3 and time.time() < deadline:
            time.sleep(0.05)
        assert sorted(path.name for path in capture_dir.glob("started-*")) == [
            "started-architect",
            "started-minimalist",
            "started-skeptic",
        ]
        release_file.write_text("go")
        stdout, stderr = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)

    assert proc.returncode == 0, stderr
    assert stdout.startswith("# Claude Parallel Adversarial Review")
    assert "execution mode: parallel" in stdout
    assert "lenses requested: skeptic, architect, minimalist" in stdout
    assert "FAKE_CLAUDE_OK lens skeptic" in stdout
    assert "FAKE_CLAUDE_OK lens architect" in stdout
    assert "FAKE_CLAUDE_OK lens minimalist" in stdout


def test_adversarial_review_parallel_rejects_json_contract(tmp_path):
    result, prompt, _argv = run_fake_claude_adversarial_review(
        tmp_path,
        ["--parallel", "--json"],
    )

    assert result.returncode == 2
    assert "adversarial-review --parallel does not support --json" in result.stderr
    assert prompt == ""


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
if sys.argv[1:] == ["--help"]:
    print("--print --permission-mode --tools --allowedTools --disallowedTools --mcp-config --strict-mcp-config --model --effort --output-format")
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
    assert data["version"] == "0.13.0"
    assert data["skills"] == "./skills/"
    assert "hooks" not in data
    assert data["interface"]["displayName"] == "Claude for Codex"


def test_version_and_docs_describe_forwarding_and_mcp():
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf8"))
    assert manifest["version"] == "0.13.0"

    readme = (PLUGIN / "README.md").read_text(encoding="utf8")
    en = (ROOT / "docs" / "README.en.md").read_text(encoding="utf8")
    zh = (ROOT / "docs" / "README.zh-CN.md").read_text(encoding="utf8")
    changelog = (PLUGIN / "CHANGELOG.md").read_text(encoding="utf8")

    for text in (readme, en, changelog):
        assert "host-forwarded" in text
        assert "MCP" in text
        assert "read-only Git" in text
        assert "review --json" in text
        assert "capabilities" in text
        assert "report --latest" in text
        assert "release-check" in text
        assert "semantic context" in text.lower()
        assert "GitHub Actions" in text
        assert "pull_request_target" in text
        assert "immutable" in text

    assert "转发" in zh
    assert "MCP" in zh
    assert "只读 Git" in zh


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
    for command in ["setup", "capabilities", "review", "adversarial-review", "multi-review", "ultrareview", "plan", "status", "review-gate", "jobs", "result", "cancel", "rescue", "report", "release-check", "github-actions", "roles", "mailbox", "leases", "reserve-job", "run-reserved-job"]:
        assert re.search(rf'case "{re.escape(command)}"', text), command
    assert "claude" in text
    assert "--print" in text or "-p" in text
    assert "--path" in text
    assert "--paths" in text
    assert "pathArgs" in text


def test_prompt_templates_and_review_schema_are_packaged():
    expected_prompts = {
        "review.md",
        "adversarial-review.md",
        "multi-review-role.md",
        "review-gate-role.md",
        "plan.md",
        "rescue.md",
    }
    prompt_dir = PLUGIN / "prompts"
    assert {path.name for path in prompt_dir.glob("*.md")} == expected_prompts
    for prompt in expected_prompts:
        text = (prompt_dir / prompt).read_text(encoding="utf8")
        assert "<task>" in text
        assert "{{" in text
        assert "</output>" not in text
    gate_template = (prompt_dir / "review-gate-role.md").read_text(encoding="utf8")
    assert "{{ROLE_NAME}}" in gate_template
    assert "{{ROLE_DIRECTIVE}}" in gate_template
    assert "{{GIT_CONTEXT}}" in gate_template

    schema = json.loads((PLUGIN / "schemas" / "review-output.schema.json").read_text(encoding="utf8"))
    assert schema["properties"]["verdict"]["enum"] == ["approve", "needs-attention"]
    assert schema["properties"]["findings"]["items"]["properties"]["severity"]["enum"] == [
        "critical",
        "high",
        "medium",
        "low",
    ]


def test_review_prompt_is_loaded_from_template(tmp_path):
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--scope", "working-tree", "template marker"],
    )

    assert result.returncode == 0, result.stderr
    assert "<task>Run a read-only code review.</task>" in prompt
    assert "template marker" in prompt
    assert "## Residual Risk" in prompt


def test_prompt_template_allows_placeholder_like_user_content(tmp_path):
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--scope", "working-tree", "literal {{ROLE_NAME}} should stay in focus"],
    )

    assert result.returncode == 0, result.stderr
    assert "literal {{ROLE_NAME}} should stay in focus" in prompt


def test_review_json_valid_output_is_normalized(tmp_path):
    payload = {
        "verdict": "needs-attention",
        "summary": "Blocking issue found.",
        "findings": [
            {
                "severity": "medium",
                "title": "Second",
                "body": "medium issue",
                "file": "b.js",
                "line_start": 4,
                "line_end": 4,
                "confidence": 0.4,
                "recommendation": "fix b",
            },
            {
                "severity": "critical",
                "title": "First",
                "body": "critical issue",
                "file": "a.js",
                "line_start": 2,
                "line_end": 3,
                "confidence": 0.9,
                "recommendation": "fix a",
            },
        ],
        "next_steps": ["patch"],
    }
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--json", "--scope", "working-tree"],
        extra_env={"FAKE_CLAUDE_STDOUT": json.dumps(payload)},
    )

    assert result.returncode == 0, result.stderr
    assert '"verdict": "approve | needs-attention"' in prompt
    normalized = json.loads(result.stdout)
    assert normalized["verdict"] == "needs-attention"
    assert [finding["title"] for finding in normalized["findings"]] == ["First", "Second"]


def test_review_json_invalid_output_returns_raw_output(tmp_path):
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--json", "--scope", "working-tree"],
        extra_env={"FAKE_CLAUDE_STDOUT": "not json"},
    )

    assert result.returncode == 1
    assert "Invalid structured review output" in result.stderr
    assert "not json" in result.stdout
    assert '"verdict": "approve | needs-attention"' in prompt


def test_review_json_extracts_fenced_mixed_output(tmp_path):
    payload = {
        "verdict": "approve",
        "summary": "ok",
        "findings": [],
        "next_steps": [],
    }
    mixed = f"Here is JSON:\n```json\n{json.dumps(payload)}\n```"
    result, _prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--json", "--scope", "working-tree"],
        extra_env={"FAKE_CLAUDE_STDOUT": mixed},
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == payload


def test_multi_review_json_aggregates_role_tagged_results(tmp_path):
    role_json = {
        "correctness": {
            "verdict": "approve",
            "summary": "correctness ok",
            "findings": [],
            "next_steps": [],
        },
        "security": {
            "verdict": "needs-attention",
            "summary": "security issue",
            "findings": [
                {
                    "severity": "high",
                    "title": "Unsafe path",
                    "body": "path can escape",
                    "file": "runtime.js",
                    "line_start": 10,
                    "line_end": 11,
                    "confidence": 0.8,
                    "recommendation": "validate",
                }
            ],
            "next_steps": ["add validation"],
        },
    }
    result, prompts, _argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--json", "--roles", "correctness,security", "--scope", "working-tree"],
        extra_env={"JSON_BY_ROLE": json.dumps(role_json)},
    )

    assert result.returncode == 0, result.stderr
    assert all('"verdict": "approve | needs-attention"' in prompt for prompt in prompts)
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "needs-attention"
    assert payload["findings"][0]["role"] == "security"
    assert payload["roles"][0]["role"] == "correctness"
    assert payload["roles"][1]["role"] == "security"


def test_multi_review_json_invalid_role_output_fails(tmp_path):
    result, _prompts, _argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--json", "--roles", "correctness", "--scope", "working-tree"],
        extra_env={"JSON_BY_ROLE": json.dumps({"correctness": {"verdict": "maybe"}})},
    )

    assert result.returncode == 1
    assert "Invalid structured multi-review output" in result.stderr
    assert "correctness:" in result.stderr


def test_adversarial_json_keeps_specialized_verdict_vocabulary(tmp_path):
    result, prompt, _argv = run_fake_claude_adversarial_review(
        tmp_path,
        ["--json"],
    )

    assert result.returncode == 1
    assert '"verdict": "PASS | CONTESTED | REJECT"' in prompt
    assert "approve | needs-attention" not in prompt


def test_no_public_review_size_command_ships_without_consumer():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    text = runtime.read_text(encoding="utf8")
    assert '"review-size"' not in text
    assert 'case "review-size"' not in text


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
    disallowed_values = re.findall(r'"--disallowedTools",\s*"([^"]+)"', text)
    assert disallowed_values
    assert '"Bash"' not in re.search(r"READ_ONLY_BUILTIN_TOOLS = Object\.freeze\(\[(.*?)\]\);", text, re.S).group(1)
    assert '"Bash"' not in re.search(r"READ_ONLY_MCP_TOOLS = Object\.freeze\(\[(.*?)\]\);", text, re.S).group(1)
    assert any("Bash" in value.split(",") for value in disallowed_values)


def test_default_multi_review_roles_exist_in_registry():
    runtime = PLUGIN / "scripts" / "lib" / "role-packs.mjs"
    text = runtime.read_text()
    registry_match = re.search(r"export const REVIEW_ROLES = Object\.freeze\(\{(?P<body>.*?)\n\}\);", text, re.S)
    defaults_match = re.search(
        r"export const DEFAULT_MULTI_REVIEW_ROLES = Object\.freeze\(\[(?P<body>.*?)\]\);",
        text,
        re.S,
    )
    assert registry_match
    assert defaults_match
    registry_roles = set(re.findall(r"^\s+([a-z-]+):\s*\{", registry_match.group("body"), re.M))
    default_roles = re.findall(r'"([^"]+)"', defaults_match.group("body"))
    assert default_roles == ["correctness", "security", "tests", "release", "adversarial"]
    assert set(default_roles).issubset(registry_roles)


def test_builtin_role_packs_validate_and_default_derives_from_default_roles():
    role_packs = PLUGIN / "scripts" / "lib" / "role-packs.mjs"
    script = f"""
import {{ DEFAULT_MULTI_REVIEW_ROLES, listRolePacks, resolveRolePack, validateBuiltInRolePacks }} from {json.dumps(role_packs.as_uri())};
const validation = validateBuiltInRolePacks();
if (!validation.ok) {{
  console.error(JSON.stringify(validation));
  process.exit(1);
}}
const packs = listRolePacks().map((pack) => pack.name).sort();
const expected = ["backend", "default", "docs", "frontend", "minimal", "release", "security", "testing"].sort();
if (JSON.stringify(packs) !== JSON.stringify(expected)) {{
  console.error(JSON.stringify({{ packs, expected }}));
  process.exit(2);
}}
const defaultPack = resolveRolePack("default");
if (JSON.stringify(defaultPack.roles) !== JSON.stringify(DEFAULT_MULTI_REVIEW_ROLES)) {{
  console.error(JSON.stringify({{ defaultPack, defaults: DEFAULT_MULTI_REVIEW_ROLES }}));
  process.exit(3);
}}
console.log("ok");
"""
    result = subprocess.run(["node", "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_user_role_pack_schema_rejects_inline_roles_and_forbidden_fields(tmp_path):
    role_packs = PLUGIN / "scripts" / "lib" / "role-packs.mjs"
    pack = tmp_path / "pack.json"
    pack.write_text(json.dumps({
        "schema_version": 1,
        "name": "unsafe",
        "description": "bad",
        "roles": [{"name": "security", "directive": "override"}],
        "tools": ["Bash"],
    }))
    script = f"""
import {{ validateRolePackFile }} from {json.dumps(role_packs.as_uri())};
try {{
  validateRolePackFile({json.dumps(str(pack))}, {{ cwd: {json.dumps(str(tmp_path / "repo"))}, mode: "validate" }});
  process.exit(1);
}} catch (error) {{
  console.log(error.message);
}}
"""
    result = subprocess.run(["node", "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Forbidden role pack field" in result.stdout or "Role pack roles must be strings" in result.stdout


def test_user_role_pack_validation_rejects_workspace_and_symlink_paths(tmp_path):
    repo = tmp_path / "repo"
    external = tmp_path / "external"
    repo.mkdir()
    external.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    pack = repo / "pack.json"
    pack.write_text(json.dumps({
        "schema_version": 1,
        "name": "local",
        "description": "repo local",
        "roles": ["correctness"],
    }))
    link = external / "link.json"
    link.symlink_to(pack)
    role_packs = PLUGIN / "scripts" / "lib" / "role-packs.mjs"
    script = f"""
import {{ validateRolePackFile }} from {json.dumps(role_packs.as_uri())};
for (const file of [{json.dumps(str(pack))}, {json.dumps(str(link))}]) {{
  try {{
    validateRolePackFile(file, {{ cwd: {json.dumps(str(repo))}, mode: "validate" }});
    console.error("accepted " + file);
    process.exit(1);
  }} catch (error) {{
    console.log(error.message);
  }}
}}
"""
    result = subprocess.run(["node", "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("must not live inside the workspace") == 2


def test_mailbox_sanitizer_redacts_paths_secrets_and_caps_utf8(tmp_path):
    sanitizer = PLUGIN / "scripts" / "lib" / "sanitize.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = "ghp_" + ("A" * 24)
    text = f"{secret} /home/alice/project/file.py /tmp/work C:\\\\Users\\\\alice\\\\secret {'测' * 3000}"
    script = f"""
import {{ sanitizeSummary }} from {json.dumps(sanitizer.as_uri())};
const out = sanitizeSummary({json.dumps(text)}, {{ cwd: {json.dumps(str(repo))}, maxBytes: 2048 }});
const bytes = Buffer.byteLength(out, "utf8");
if (bytes > 2048) throw new Error("too many bytes " + bytes);
if (out.includes({json.dumps(secret)})) throw new Error("secret leaked");
if (out.includes("/home/alice") || out.includes("/tmp/work") || out.includes("C:\\\\Users\\\\alice")) throw new Error("path leaked: " + out);
JSON.parse(JSON.stringify({{ out }}));
console.log(bytes);
"""
    result = subprocess.run(["node", "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_mailbox_parallel_posts_are_not_lost(tmp_path):
    mailbox = PLUGIN / "scripts" / "lib" / "mailbox.mjs"
    data = tmp_path / "data"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    script = f"""
import {{ postMailboxMessage, showMailboxThread }} from {json.dumps(mailbox.as_uri())};
const env = {{ ...process.env, CLAUDE_PLUGIN_DATA: {json.dumps(str(data))} }};
await Promise.all(Array.from({{ length: 20 }}, (_, index) => postMailboxMessage({json.dumps(str(repo))}, {{
  threadId: "thread-smoke",
  jobId: "job-smoke",
  role: "correctness",
  command: "multi-review",
  status: "note",
  summary: "message " + index,
  source: "manual"
}}, env)));
const shown = showMailboxThread({json.dumps(str(repo))}, "thread-smoke", env);
if (shown.messages.length !== 20) throw new Error("lost messages " + shown.messages.length);
console.log("ok");
"""
    result = subprocess.run(["node", "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_lease_claim_conflict_and_release(tmp_path):
    leases = PLUGIN / "scripts" / "lib" / "leases.mjs"
    data = tmp_path / "data"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("x")
    script = f"""
import {{ claimLease, listLeases, releaseLease }} from {json.dumps(leases.as_uri())};
const env = {{ ...process.env, CLAUDE_PLUGIN_DATA: {json.dumps(str(data))} }};
const first = claimLease({json.dumps(str(repo))}, {{ path: "file.txt", role: "correctness", ttl: "60s", jobId: "job-a" }}, env);
const second = claimLease({json.dumps(str(repo))}, {{ path: "file.txt", role: "security", ttl: "60s", jobId: "job-b" }}, env);
if (first.status !== "claimed") throw new Error("first not claimed");
if (second.status !== "conflict") throw new Error("second not conflict: " + second.status);
if (listLeases({json.dumps(str(repo))}, env).leases.length !== 1) throw new Error("bad lease count");
const released = releaseLease({json.dumps(str(repo))}, first.lease.id, env, {{ manual: true }});
if (released.status !== "released") throw new Error("not released");
console.log("ok");
"""
    result = subprocess.run(["node", "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_lease_rejects_path_traversal_and_symlink_escape(tmp_path):
    leases = PLUGIN / "scripts" / "lib" / "leases.mjs"
    data = tmp_path / "data"
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (outside / "secret.txt").write_text("secret")
    (repo / "link").symlink_to(outside)
    script = f"""
import {{ claimLease }} from {json.dumps(leases.as_uri())};
const env = {{ ...process.env, CLAUDE_PLUGIN_DATA: {json.dumps(str(data))} }};
for (const path of ["../outside/secret.txt", "link/secret.txt"]) {{
  try {{
    claimLease({json.dumps(str(repo))}, {{ path, role: "correctness", ttl: "60s" }}, env);
    throw new Error("accepted " + path);
  }} catch (error) {{
    if (!String(error.message).includes("inside the workspace")) throw error;
  }}
}}
console.log("ok");
"""
    result = subprocess.run(["node", "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


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
    assert {"Read", "Grep", "Glob"}.issubset(set(argv[argv.index("--tools") + 1].split(",")))
    assert argv[argv.index("--disallowedTools") + 1] == "Edit,Write,MultiEdit,Bash"
    assert "base requested: main" in prompt
    assert "base effective: unavailable (HEAD missing)" in prompt
    assert "base ignored because HEAD is unavailable" in prompt
    assert "base: main" not in prompt
    assert "fatal: bad revision" not in prompt
    assert "main...HEAD" not in prompt or "branch diff skipped" in prompt
    assert "sample.txt" in prompt
    assert "changed files from git status fallback" in prompt


def test_read_only_review_invokes_claude_with_strict_mcp_config(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    argv_file = tmp_path / "argv.json"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("hello\n", encoding="utf8")

    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('fs');\n"
        f"fs.writeFileSync({json.dumps(str(argv_file))}, JSON.stringify(process.argv.slice(2)));\n"
        "process.stdout.write('OK\\n');\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_CODE_PATH"] = str(fake_claude)
    env["CLAUDE_FOR_CODEX_KEEP_MCP_CONFIG"] = "1"

    result = subprocess.run(
        [NODE, str(runtime), "review", "focus"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))
    assert "--mcp-config" in argv
    assert "--strict-mcp-config" in argv
    builtin_tools = argv[argv.index("--tools") + 1].split(",")
    assert builtin_tools == ["Read", "Grep", "Glob"]
    allowed_tools = argv[argv.index("--allowedTools") + 1].split(",")
    assert "mcp__claude-for-codex-git__git_status" in allowed_tools
    assert "mcp__claude-for-codex-git__git_diff" in allowed_tools
    disallowed_tools = argv[argv.index("--disallowedTools") + 1].split(",")
    for tool in ["Bash", "Edit", "Write", "MultiEdit"]:
        assert tool in disallowed_tools
    config_path = pathlib.Path(argv[argv.index("--mcp-config") + 1])
    assert config_path.parent == data / "tmp"
    config = json.loads(config_path.read_text(encoding="utf8"))
    server = config["mcpServers"]["claude-for-codex-git"]
    assert pathlib.Path(server["command"]).name.startswith("node")
    assert server["args"][-1] == "server"
    assert "mcp-git.mjs" in " ".join(server["args"])
    assert server["cwd"] == str(repo)


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
    assert sorted(re.search(r"<role_name>([^<]+)</role_name>", prompt).group(1) for prompt in prompts) == [
        "correctness",
        "security",
        "tests",
    ]
    assert re.findall(r"^## Role: (.+)$", result.stdout, re.M) == ["correctness", "security", "tests"]
    assert "FAKE_CLAUDE_FAIL role security call" in result.stdout
    assert "Role failed with exit status 17." in result.stdout
    assert "stderr: diagnostic for failed role security" in result.stdout
    assert result.stdout.count("FAKE_CLAUDE_OK call") == 2
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
    assert sorted(re.search(r"<role_name>([^<]+)</role_name>", prompt).group(1) for prompt in prompts) == [
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
    prompts_by_role = {
        re.search(r"<role_name>([^<]+)</role_name>", prompt).group(1): prompt
        for prompt in prompts
    }
    assert "Challenge correctness and completeness." in prompts_by_role["skeptic"]
    assert "Challenge structural fitness." in prompts_by_role["architect"]
    assert "Challenge necessity and complexity." in prompts_by_role["minimalist"]


def test_roles_command_lists_and_inspects_builtin_packs():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    listed = subprocess.run(
        [NODE, str(runtime), "roles", "list", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0, listed.stderr
    payload = json.loads(listed.stdout)
    names = {pack["name"] for pack in payload["rolePacks"]}
    assert {"default", "security", "release", "frontend", "backend", "testing", "docs", "minimal"}.issubset(names)

    inspected = subprocess.run(
        [NODE, str(runtime), "roles", "inspect", "minimal", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert inspected.returncode == 0, inspected.stderr
    summary = json.loads(inspected.stdout)
    assert summary["roles"] == ["correctness"]
    assert summary["gate_compatible"] is True
    assert summary["hash"].startswith("sha256:")


def test_multi_review_role_pack_expands_builtin_pack(tmp_path):
    result, prompts, _argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--role-pack", "minimal", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert len(prompts) == 1
    assert "<role_name>correctness</role_name>" in prompts[0]
    assert "roles requested: correctness" in result.stdout


def test_multi_review_role_pack_conflicts_with_roles(tmp_path):
    result, prompts, _argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--role-pack", "minimal", "--roles", "security", "--scope", "working-tree"],
    )

    assert result.returncode == 2
    assert "--role-pack conflicts with --roles/--role." in result.stderr
    assert prompts == []


def test_review_gate_rejects_default_role_pack_but_bare_gate_still_uses_default(tmp_path):
    blocked, prompts, _capture = run_fake_review_gate(
        tmp_path,
        extra_env={},
        hook_input={"hook_event_name": "Stop"},
    )
    assert blocked.returncode == 0, blocked.stderr
    assert blocked.stdout == ""
    assert len(prompts) == 5

    with_pack = tmp_path / "with-pack"
    with_pack.mkdir()
    runtime, repo, _capture_dir, env = prepare_gate_repo(with_pack, with_change=True)
    setup = subprocess.run(
        [NODE, str(runtime), "setup", "--enable-review-gate", "--review-gate-mode", "multi-role"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr
    rejected = subprocess.run(
        [NODE, str(runtime), "review-gate", "--role-pack", "default"],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "Stop", "cwd": str(repo)}),
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 0
    assert json.loads(rejected.stdout)["decision"] == "block"
    assert "not gate-compatible" in rejected.stdout


def test_multi_review_calls_use_read_only_flags_and_prompt_last(tmp_path):
    result, prompts, argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--roles", "correctness,security", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    for prompt, argv in zip(prompts, argvs):
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        assert {"Read", "Grep", "Glob"}.issubset(set(argv[argv.index("--tools") + 1].split(",")))
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
    assert "capabilities" in payload
    assert "claude" in payload["capabilities"]
    assert "semanticProviders" in payload["capabilities"]
    assert payload["hooks"]["manifest"] == "hooks/hooks.json"
    assert payload["hooks"]["events"] == ["SessionStart", "SessionEnd", "UserPromptSubmit", "Stop"]


def test_setup_reports_hooks_and_mcp_diagnostics(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    home = tmp_path / "home"
    repo.mkdir()
    data.mkdir()
    home.mkdir()
    codex_dir = home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        '[hooks.state."claude-for-codex@external-models-for-codex:hooks/hooks.json:stop:0:0"]\n'
        'trusted_hash = "sha256:abc"\n',
        encoding="utf8",
    )

    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["HOME"] = str(home)

    result = subprocess.run(
        [NODE, str(runtime), "setup"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode in (0, 1)
    payload = json.loads(result.stdout)
    assert payload["hooks"]["manifestExists"] is True
    assert "Stop" in payload["hooks"]["events"]
    assert payload["hooks"]["codexConfigChecked"] is True
    assert payload["hooks"]["trustedInCodexConfig"] is True
    assert payload["mcp"]["gitServerExists"] is True
    assert payload["mcp"]["strictConfigSupported"] is True


def test_setup_reports_inline_hook_trust_state_for_existing_codex_config(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    home = tmp_path / "home"
    repo.mkdir()
    data.mkdir()
    home.mkdir()
    codex_dir = home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        "[hooks.state]\n"
        '"claude-for-codex@external-models-for-codex:hooks/hooks.json:stop:0:0" = "trusted"\n',
        encoding="utf8",
    )

    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["HOME"] = str(home)

    result = subprocess.run(
        [NODE, str(runtime), "setup"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode in (0, 1)
    payload = json.loads(result.stdout)
    assert payload["hooks"]["codexConfigChecked"] is True
    assert payload["hooks"]["trustedInCodexConfig"] is True


def test_setup_reports_missing_codex_config_without_throwing(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    home = tmp_path / "home"
    repo.mkdir()
    data.mkdir()
    home.mkdir()

    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["HOME"] = str(home)

    result = subprocess.run(
        [NODE, str(runtime), "setup"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode in (0, 1)
    payload = json.loads(result.stdout)
    assert payload["hooks"]["codexConfigChecked"] is False
    assert payload["hooks"]["codexConfigError"] == ""
    assert payload["hooks"]["trustedInCodexConfig"] is False


def test_capabilities_reports_claude_flags_without_running_semantic_providers(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    fake_bin.mkdir()

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import sys

if sys.argv[1:] == ["--version"]:
    print("Claude Code fake 1.2.3")
    raise SystemExit(0)
if sys.argv[1:] == ["--help"]:
    print("--print --permission-mode --tools --allowedTools --disallowedTools --mcp-config --strict-mcp-config --model --effort --output-format")
    raise SystemExit(0)
print("unexpected", file=sys.stderr)
raise SystemExit(9)
"""
    )
    fake_claude.chmod(0o755)

    provider = fake_bin / "codegraph"
    provider.write_text("#!/usr/bin/env sh\necho SHOULD_NOT_RUN > \"$PROVIDER_MARKER\"\n", encoding="utf8")
    provider.chmod(0o755)
    marker = tmp_path / "provider-ran"

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["PROVIDER_MARKER"] = str(marker)

    result = subprocess.run(
        [NODE, str(runtime), "capabilities"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["claude"]["available"] is True
    assert payload["claude"]["version"] == "Claude Code fake 1.2.3"
    assert payload["claude"]["flags"]["--strict-mcp-config"] is True
    assert payload["semanticProviders"]["codegraph"]["availableOnPath"] is True
    assert not marker.exists()
    assert payload["backend"]["defaultBackend"] == "cli"
    assert payload["backend"]["requestedBackend"] == "cli"


def test_capabilities_prefers_claude_agent_sdk_package(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    sdk_root = tmp_path / "node_modules" / "@anthropic-ai" / "claude-agent-sdk"
    sdk_root.mkdir(parents=True)
    (sdk_root / "package.json").write_text(json.dumps({
        "name": "@anthropic-ai/claude-agent-sdk",
        "version": "0.2.0",
        "type": "module",
        "main": "index.mjs",
        "exports": {
            ".": "./index.mjs",
        },
    }), encoding="utf8")
    (sdk_root / "index.mjs").write_text(
        "export function query() { return [{ type: 'result', result: 'ok' }]; }\n",
        encoding="utf8",
    )

    result = subprocess.run(
        [NODE, str(runtime), "capabilities"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["claudeSdk"]["available"] is True
    assert payload["claudeSdk"]["packageName"] == "@anthropic-ai/claude-agent-sdk"
    assert payload["claudeSdk"]["version"] == "0.2.0"
    assert payload["claudeSdk"]["supportedFeatures"]["agents"] is True
    assert payload["claudeSdk"]["supportedFeatures"]["outputFormat"] is True
    assert payload["claudeSdk"]["supportedFeatures"]["includePartialMessages"] is True


def test_capabilities_falls_back_to_old_claude_code_sdk_package(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    sdk_root = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code"
    sdk_root.mkdir(parents=True)
    (sdk_root / "package.json").write_text(json.dumps({
        "name": "@anthropic-ai/claude-code",
        "version": "0.0.42",
        "type": "module",
        "main": "index.mjs",
    }), encoding="utf8")
    (sdk_root / "index.mjs").write_text(
        "export function query() { return [{ type: 'result', result: 'ok' }]; }\n",
        encoding="utf8",
    )

    result = subprocess.run(
        [NODE, str(runtime), "capabilities"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["claudeSdk"]["available"] is True
    assert payload["claudeSdk"]["packageName"] == "@anthropic-ai/claude-code"
    assert payload["claudeSdk"]["version"] == "0.0.42"


def test_capabilities_resolves_claude_agent_sdk_from_global_npm_root(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    fake_bin = tmp_path / "bin"
    global_root = tmp_path / "global-node-modules"
    sdk_root = global_root / "@anthropic-ai" / "claude-agent-sdk"
    fake_bin.mkdir()
    sdk_root.mkdir(parents=True)
    (sdk_root / "package.json").write_text(json.dumps({
        "name": "@anthropic-ai/claude-agent-sdk",
        "version": "0.3.162",
        "type": "module",
        "main": "index.mjs",
        "exports": {
            ".": "./index.mjs",
        },
    }), encoding="utf8")
    (sdk_root / "index.mjs").write_text(
        "export function query() { return [{ type: 'result', result: 'ok' }]; }\n",
        encoding="utf8",
    )
    fake_npm = fake_bin / "npm"
    fake_npm.write_text(
        "#!/usr/bin/env sh\n"
        "if [ \"$1\" = root ] && [ \"$2\" = -g ]; then\n"
        f"  printf '%s\\n' {str(global_root)!r}\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf8",
    )
    fake_npm.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "capabilities"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["claudeSdk"]["available"] is True
    assert payload["claudeSdk"]["source"] == "global"
    assert payload["claudeSdk"]["packageName"] == "@anthropic-ai/claude-agent-sdk"
    assert payload["claudeSdk"]["version"] == "0.3.162"


def test_capabilities_reports_native_claude_cli_surfaces(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('2.1.162 (Claude Code)')\n"
        "    raise SystemExit(0)\n"
        "if sys.argv[1:] == ['--help']:\n"
        "    print('--agent <agent> --agents <json> --json-schema <schema> --include-partial-messages --fallback-model <model> --max-budget-usd <amount> --resume --continue --session-id --fork-session --output-format')\n"
        "    raise SystemExit(0)\n"
        "if sys.argv[1:] == ['agents', '--help']:\n"
        "    print('Usage: claude agents [options] --json --cwd <path>')\n"
        "    raise SystemExit(0)\n"
        "if sys.argv[1:] == ['ultrareview', '--help']:\n"
        "    print('Usage: claude ultrareview [options] [target] --json --timeout <minutes>')\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(2)\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "capabilities"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["claude"]["nativeAgents"]["agentFlag"] is True
    assert payload["claude"]["nativeAgents"]["agentsJson"] is True
    assert payload["claude"]["nativeAgents"]["agentsCommand"] is True
    assert payload["claude"]["structuredOutput"]["jsonSchema"] is True
    assert payload["claude"]["streaming"]["includePartialMessages"] is True
    assert payload["claude"]["ultrareview"]["available"] is True
    assert payload["claude"]["sessions"]["resume"] is True


def test_sdk_backend_review_uses_fake_sdk_and_read_only_options(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n")
    sdk_module, capture = write_fake_claude_sdk(tmp_path, stdout="SDK_REVIEW_OK")

    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "review", "--backend", "sdk", "--scope", "working-tree", "focus"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "SDK_REVIEW_OK"
    query = json.loads((capture / "query.json").read_text(encoding="utf8"))
    assert "focus" in query["prompt"]
    assert query["permissionMode"] == "dontAsk"
    assert query["options"]["permissionMode"] == "dontAsk"
    assert set(["Read", "Grep", "Glob"]).issubset(set(query["allowedTools"]))
    assert set(["Edit", "Write", "MultiEdit", "Bash"]).issubset(set(query["disallowedTools"]))
    assert "mcp__claude-for-codex-git__git_status" in query["allowedTools"]
    assert "claude-for-codex-git" in query["mcpServers"]


def test_sdk_subagent_review_passes_read_only_agent_definitions(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    sdk_module, capture = write_fake_claude_sdk(tmp_path, stdout="SDK_NATIVE_REVIEW_OK")
    backend_uri = (PLUGIN / "scripts" / "lib" / "claude-backend.mjs").as_uri()
    native_review_uri = (PLUGIN / "scripts" / "lib" / "claude-native-review.mjs").as_uri()

    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    result = subprocess.run(
        [
            NODE,
            "--input-type=module",
            "-e",
            f"""
import {{
  buildNativeReviewAgents,
  nativeAgentName,
  nativeReviewTeamPrompt
}} from {json.dumps(native_review_uri)};
import {{ runSdkNativeReview }} from {json.dumps(backend_uri)};

const roles = [
  {{ name: "Security & Correctness", description: "security review", prompt: "Find correctness and security risks." }},
  "release readiness"
];
const agents = buildNativeReviewAgents(roles, {{ model: "claude-sonnet-4-5", effort: "medium" }});
const literalModel = buildNativeReviewAgents(["literal model role"], {{ model: "sonnet" }});
const inherited = buildNativeReviewAgents(["default model role"]);
const prompt = nativeReviewTeamPrompt(roles, "git diff --stat output", "focus on changed files");
const response = await runSdkNativeReview(prompt, {{ model: "claude-sonnet-4-5" }}, {{
  cwd: {json.dumps(str(repo))},
  agents
}});
console.log(JSON.stringify({{
  response,
  agents,
  literalModel,
  inherited,
  prompt,
  sanitized: nativeAgentName("Security & Correctness")
}}));
""",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["response"]["status"] == 0
    assert payload["response"]["stdout"] == "SDK_NATIVE_REVIEW_OK"
    assert payload["sanitized"] == "cfc_security_correctness"
    assert "cfc_security_correctness" in payload["agents"]
    assert "cfc_release_readiness" in payload["agents"]
    assert "Invoke every listed role agent exactly once" in payload["prompt"]
    assert "\"role_results\"" in payload["prompt"]

    for definition in payload["agents"].values():
        assert definition["tools"] == ["Read", "Grep", "Glob"]
        assert "permissionMode" not in definition
        assert definition["maxTurns"] == 4
        assert definition["model"] == "inherit"
        assert definition["model"] in {"sonnet", "opus", "haiku", "inherit"}
        assert "effort" not in definition
        assert set(["Edit", "Write", "MultiEdit", "Bash", "Agent"]).issubset(
            set(definition["disallowedTools"])
        )
        assert "Agent" not in definition["tools"]

    literal_definition = payload["literalModel"]["cfc_literal_model_role"]
    assert literal_definition["model"] == "sonnet"
    assert "permissionMode" not in literal_definition
    assert "effort" not in literal_definition

    inherited_definition = payload["inherited"]["cfc_default_model_role"]
    assert inherited_definition["model"] == "inherit"
    assert "permissionMode" not in inherited_definition
    assert "effort" not in inherited_definition

    query = json.loads((capture / "query.json").read_text(encoding="utf8"))
    assert query["agents"] == payload["agents"]
    assert query["options"]["agents"] == payload["agents"]
    assert "Agent" in query["allowedTools"]
    assert "Agent" in query["options"]["allowedTools"]
    assert set(["Read", "Grep", "Glob"]).issubset(set(query["allowedTools"]))
    assert set(["Edit", "Write", "MultiEdit", "Bash"]).issubset(set(query["disallowedTools"]))
    assert "claude-for-codex-git" in query["mcpServers"]


def test_sdk_native_review_unavailable_fails_with_native_message(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    backend_uri = (PLUGIN / "scripts" / "lib" / "claude-backend.mjs").as_uri()
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(tmp_path / "missing-sdk.mjs")

    result = subprocess.run(
        [
            NODE,
            "--input-type=module",
            "-e",
            f"""
import {{ runSdkNativeReview }} from {json.dumps(backend_uri)};
const response = await runSdkNativeReview("prompt", {{}}, {{
  cwd: {json.dumps(str(repo))},
  agents: {{}}
}});
console.log(JSON.stringify(response));
""",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == 1
    assert payload["errorCode"] == "SDK_UNAVAILABLE"
    assert payload["stderr"] == "Claude SDK native subagents requested but the Claude Agent SDK is unavailable."


def test_sdk_backend_missing_and_invalid_backend_fail_clearly(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(tmp_path / "missing-sdk.mjs")

    missing = subprocess.run(
        [NODE, str(runtime), "review", "--backend", "sdk", "--scope", "working-tree"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 1
    assert "Claude SDK" in missing.stderr or "SDK" in missing.stderr
    assert "@anthropic-ai/claude-agent-sdk" in missing.stderr
    assert "@anthropic-ai/claude-code" in missing.stderr

    invalid = subprocess.run(
        [NODE, str(runtime), "review", "--backend", "banana"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 2
    assert "Invalid --backend" in invalid.stderr


def test_sdk_backend_structured_review_and_report_metadata(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n")
    sdk_module, _capture = write_fake_claude_sdk(
        tmp_path,
        stdout='{"verdict":"approve","summary":"ok","findings":[],"next_steps":[]}',
    )

    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [NODE, str(runtime), "review", "--backend", "sdk", "--json", "--scope", "working-tree"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verdict"] == "approve"

    latest = subprocess.run(
        [NODE, str(runtime), "report", "--latest"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert latest.returncode == 0, latest.stderr
    report = json.loads(latest.stdout)["report"]
    assert report["backend"] == "sdk"
    assert report["sdkMessageCount"] == 1
    assert report["sdkSessionIdHash"]
    serialized = json.dumps(report)
    assert "sdk-session-secret" not in serialized
    assert "SDK_REVIEW_OK" not in serialized


def test_sdk_backend_review_gate_allows_and_uses_sdk(tmp_path):
    runtime, repo, _capture_dir, env = prepare_gate_repo(tmp_path)
    sdk_module, capture = write_fake_claude_sdk(tmp_path, stdout="ALLOW: sdk gate ok")
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    setup = subprocess.run(
        [NODE, str(runtime), "setup", "--enable-review-gate", "--review-gate-mode", "multi-role"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr

    result = subprocess.run(
        [NODE, str(runtime), "review-gate", "--backend", "sdk"],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "Stop", "cwd": str(repo)}),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    query = json.loads((capture / "query.json").read_text(encoding="utf8"))
    assert "<task>Run a stop-gate review of the current git changes.</task>" in query["prompt"]
    assert query["permissionMode"] == "dontAsk"


def test_sdk_backend_rescue_write_keeps_explicit_write_fingerprint(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "broken.txt").write_text("broken\n")
    sdk_module, capture = write_fake_claude_sdk(tmp_path, stdout="SDK_RESCUE_OK")
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)

    result = subprocess.run(
        [NODE, str(runtime), "rescue", "--backend", "sdk", "--write", "fix failure"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "SDK_RESCUE_OK"
    assert "write-mode fingerprint" in result.stderr
    query = json.loads((capture / "query.json").read_text(encoding="utf8"))
    assert query["permissionMode"] == "bypassPermissions"
    assert query["options"]["permissionMode"] == "bypassPermissions"
    assert "allowedTools" not in query or query["allowedTools"] is None
    assert "mcpServers" not in query or query["mcpServers"] is None


def test_review_semantic_context_defaults_off_even_with_configured_provider(tmp_path):
    _provider, config_path, capture = write_semantic_provider(tmp_path)
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--scope", "working-tree"],
        extra_env={"CLAUDE_FOR_CODEX_SEMANTIC_CONFIG": str(config_path)},
    )

    assert result.returncode == 0, result.stderr
    assert "<semantic_context" not in prompt
    assert not capture.exists()


def test_review_semantic_context_fake_provider_reaches_prompt_and_report(tmp_path):
    _provider, config_path, capture = write_semantic_provider(tmp_path)
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--scope", "working-tree", "--semantic-context", "fake"],
        extra_env={"CLAUDE_FOR_CODEX_SEMANTIC_CONFIG": str(config_path)},
    )

    assert result.returncode == 0, result.stderr
    assert '<semantic_context provider="fake" status="available">' in prompt
    assert "semantic summary marker" in prompt
    request = json.loads((capture / "request.json").read_text())
    assert request["scope"] == "working-tree"

    latest = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "claude-companion.mjs"), "report", "--latest"],
        cwd=tmp_path / "repo",
        env={**os.environ, "CLAUDE_FOR_CODEX_SEMANTIC_CONFIG": str(config_path)},
        capture_output=True,
        text=True,
    )
    report = json.loads(latest.stdout)["report"]
    serialized = json.dumps(report)
    assert report["semanticProvider"] == "fake"
    assert report["semanticStatus"] == "available"
    assert "semantic summary marker" not in serialized


def test_review_semantic_context_unknown_provider_exits_before_claude(tmp_path):
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--semantic-context", "missing"],
    )

    assert result.returncode == 2
    assert "Unknown semantic provider" in result.stderr
    assert prompt == ""


def test_semantic_provider_child_env_is_allowlist_only(tmp_path):
    _provider, config_path, capture = write_semantic_provider(tmp_path)
    result, _prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--semantic-context", "fake"],
        extra_env={
            "CLAUDE_FOR_CODEX_SEMANTIC_CONFIG": str(config_path),
            "ANTHROPIC_API_KEY": "should-not-leak",
        },
    )

    assert result.returncode == 0, result.stderr
    child_env = json.loads((capture / "env.json").read_text())
    assert "SEMANTIC_PROVIDER_CAPTURE" in child_env
    assert "ANTHROPIC_API_KEY" not in child_env
    assert "CLAUDE_FOR_CODEX_SEMANTIC_CONFIG" not in child_env


def test_semantic_provider_symlink_escape_is_degraded_not_persisted(tmp_path):
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret")
    response = {
        "version": 1,
        "provider": "fake",
        "items": [{"path": "escaped", "summary": "outside secret should not appear"}],
    }
    _provider, config_path, _capture = write_semantic_provider(
        tmp_path,
        response=response,
        extra_script=f"pathlib.Path('escaped').symlink_to({json.dumps(str(outside))})\n",
    )
    result, prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--semantic-context", "fake"],
        extra_env={"CLAUDE_FOR_CODEX_SEMANTIC_CONFIG": str(config_path)},
    )

    assert result.returncode == 0, result.stderr
    assert 'status="unavailable"' in prompt
    assert "outside secret should not appear" not in prompt
    latest = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "claude-companion.mjs"), "report", "--latest"],
        cwd=tmp_path / "repo",
        env={**os.environ, "CLAUDE_FOR_CODEX_SEMANTIC_CONFIG": str(config_path)},
        capture_output=True,
        text=True,
    )
    report = json.loads(latest.stdout)["report"]
    assert report["semanticFailed"] is True
    assert report["semanticFailureReason"] == "validation_error"


def test_review_writes_sanitized_report_and_report_latest_omits_raw_content(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "secret_code.py").write_text("print('raw source marker')\n")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
if sys.argv[1:] == ["--help"]:
    print("--print --permission-mode --tools --allowedTools --disallowedTools --mcp-config --strict-mcp-config --model --effort --output-format")
    raise SystemExit(0)
print("RAW_MODEL_OUTPUT_WITH_CODE_SNIPPET print('raw source marker')")
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "review", "--scope", "working-tree", "sensitive focus marker"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    latest = subprocess.run(
        [NODE, str(runtime), "report", "--latest"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert latest.returncode == 0, latest.stderr
    payload = json.loads(latest.stdout)
    report = payload["report"]
    assert report["command"] == "review"
    assert report["stdoutBytes"] > 0
    assert report["workspaceId"]
    serialized = json.dumps(report)
    assert "RAW_MODEL_OUTPUT_WITH_CODE_SNIPPET" not in serialized
    assert "raw source marker" not in serialized
    assert "sensitive focus marker" not in serialized
    assert str(repo) not in serialized

    reports_dir = pathlib.Path(payload["reportsDir"])
    if os.name == "posix":
        assert reports_dir.stat().st_mode & 0o777 == 0o700


def test_no_telemetry_disables_report_writes(tmp_path):
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
    fake_claude.write_text("#!/usr/bin/env sh\n[ \"$1\" = \"--version\" ] && { echo claude fake; exit 0; }\necho OK\n", encoding="utf8")
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_NO_TELEMETRY"] = "1"
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "review", "--scope", "working-tree"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    latest = subprocess.run(
        [NODE, str(runtime), "report", "--latest"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert latest.returncode == 0, latest.stderr
    assert json.loads(latest.stdout)["report"] is None


def test_release_check_passes_with_remote_install_skipped():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    result = subprocess.run(
        [NODE, str(runtime), "release-check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["manifest-version"]["ok"] is True
    assert checks["changelog-unreleased-empty"]["ok"] is True
    assert checks["skill-inventory"]["ok"] is True
    assert checks["docs-immutable-ref-README.md"]["ok"] is True
    assert checks["semantic-fixture-safe"]["ok"] is True
    assert checks["semantic-fixture-unsafe"]["ok"] is True
    assert checks["remote-install-smoke"]["detail"] == "skipped"


def test_release_check_remote_install_uses_requested_immutable_ref(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    fake_bin = tmp_path / "bin"
    log = tmp_path / "codex-calls.jsonl"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys

pathlib.Path({json.dumps(str(log))}).write_text(pathlib.Path({json.dumps(str(log))}).read_text() + json.dumps(sys.argv[1:]) + "\\n" if pathlib.Path({json.dumps(str(log))}).exists() else json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:] == ["--version"]:
    print("codex fake")
    raise SystemExit(0)
if sys.argv[1:3] == ["plugin", "marketplace"] and "--ref" in sys.argv:
    raise SystemExit(0)
if sys.argv[1:3] == ["plugin", "add"]:
    raise SystemExit(0)
raise SystemExit(1)
""",
        encoding="utf8",
    )
    fake_codex.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "release-check", "--remote-install", "--ref", "claude-for-codex-v0.13.0"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf8").splitlines()]
    assert ["plugin", "marketplace", "add", "yilibinbin/external-models-for-codex", "--ref", "claude-for-codex-v0.13.0"] in calls
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["remote-install-smoke"]["detail"] == "installed ref=claude-for-codex-v0.13.0"


def test_github_actions_render_is_safe_and_does_not_write(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()

    result = subprocess.run(
        [NODE, str(runtime), "github-actions", "render"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not (repo / ".github" / "workflows" / "claude-for-codex-review.yml").exists()
    text = result.stdout
    assert "pull_request:" in text
    assert "pull_request_target" not in text
    assert "permissions:" in text
    assert "contents: read" in text
    assert "pull-requests: write" in text
    assert "checks: write" not in text
    assert "codex plugin marketplace add yilibinbin/external-models-for-codex --ref claude-for-codex-v0.13.0" in text
    assert "codex plugin add claude-for-codex@external-models-for-codex" in text
    assert "github.event.pull_request.base.sha" in text
    assert "fetch-depth: 0" in text
    assert "retention-days: 5" in text
    assert "github.*" not in text
    assert "/Users/fanghao" not in text

    run_blocks = re.findall(r"run: \|\n((?:        .+\n)+)", text)
    assert run_blocks
    assert all("${{ github." not in block for block in run_blocks)
    assert "$BASE_SHA" in text
    assert '"$BASE_SHA"' in text
    assert "$HEAD_REPO" in text
    assert "$BASE_REPO" in text


def test_github_actions_init_write_and_force(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()

    no_write = subprocess.run(
        [NODE, str(runtime), "github-actions", "init"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert no_write.returncode == 0, no_write.stderr
    workflow = repo / ".github" / "workflows" / "claude-for-codex-review.yml"
    assert not workflow.exists()

    write = subprocess.run(
        [NODE, str(runtime), "github-actions", "init", "--write"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert write.returncode == 0, write.stderr
    assert workflow.exists()
    assert "claude-for-codex-v0.13.0" in workflow.read_text(encoding="utf8")

    overwrite = subprocess.run(
        [NODE, str(runtime), "github-actions", "init", "--write"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert overwrite.returncode == 1
    assert "already exists" in overwrite.stderr

    forced = subprocess.run(
        [NODE, str(runtime), "github-actions", "init", "--write", "--force", "--annotations"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert forced.returncode == 0, forced.stderr
    workflow_text = workflow.read_text(encoding="utf8")
    assert "checks: write" in workflow_text
    assert "github.rest.checks.create" in workflow_text
    assert "withBackoff" in workflow_text


def test_github_actions_validate_rejects_unsafe_workflows(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    workflow_dir = repo / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow = workflow_dir / "claude-for-codex-review.yml"

    rendered = subprocess.run(
        [NODE, str(runtime), "github-actions", "render"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stderr
    workflow.write_text(rendered.stdout, encoding="utf8")

    valid = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert valid.returncode == 0, valid.stderr
    assert json.loads(valid.stdout)["ok"] is True

    workflow.write_text("on:\n  pull_request_target:\njobs:\n  review:\n    permissions:\n      contents: read\n", encoding="utf8")
    invalid = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 1
    checks = {check["name"]: check for check in json.loads(invalid.stdout)["checks"]}
    assert checks["no-pull-request-target"]["ok"] is False

    unsafe = rendered.stdout.replace('echo "Base SHA: $BASE_SHA"', 'echo "${{ github.event.pull_request.head.ref }}"')
    workflow.write_text(unsafe, encoding="utf8")
    invalid = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 1
    checks = {check["name"]: check for check in json.loads(invalid.stdout)["checks"]}
    assert checks["no-github-context-in-run"]["ok"] is False


def test_github_actions_validate_rejects_mutable_main_and_local_paths(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    workflow_dir = repo / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow = workflow_dir / "claude-for-codex-review.yml"

    rendered = subprocess.run(
        [NODE, str(runtime), "github-actions", "render"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stderr
    workflow.write_text(
        rendered.stdout.replace("--ref claude-for-codex-v0.13.0", "--ref main") + "\n# /Users/fanghao/leak\n",
        encoding="utf8",
    )

    invalid = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert invalid.returncode == 1
    checks = {check["name"]: check for check in json.loads(invalid.stdout)["checks"]}
    assert checks["immutable-release-ref"]["ok"] is False
    assert checks["no-local-absolute-paths"]["ok"] is False


def test_github_actions_comment_and_annotation_sanitization():
    module = PLUGIN / "scripts" / "lib" / "github-actions.mjs"
    review = {
        "verdict": "needs-attention",
        "summary": "<script>alert(1)</script> fix branch `$(rm -rf /)`",
        "findings": [
            {
                "severity": "high",
                "title": "Unsafe <b>path</b>",
                "file": "src/app.js",
                "line": 7,
                "end_line": 8,
                "recommendation": "Quote `BASE_SHA`; do not leak /Users/fanghao/secret",
            },
            {
                "severity": "critical",
                "title": "Traversal",
                "file": "../secret.txt",
                "line": 1,
                "recommendation": "bad",
            },
            {
                "severity": "medium",
                "title": "Windows",
                "file": "C:\\\\secret.txt",
                "line": 2,
                "recommendation": "bad",
            },
        ],
        "raw_output": "must not appear",
        "prompt": "must not appear",
    }
    script = f"""
import {{ renderReviewComment, reviewToAnnotations }} from {json.dumps(module.as_posix())};
const review = {json.dumps(review)};
console.log(JSON.stringify({{ comment: renderReviewComment(review), annotations: reviewToAnnotations(review) }}));
"""
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "<script>" not in payload["comment"]
    assert "&lt;script&gt;" in payload["comment"]
    assert "/Users/fanghao" not in payload["comment"]
    assert "must not appear" not in payload["comment"]
    assert payload["annotations"] == [
        {
            "path": "src/app.js",
            "start_line": 7,
            "end_line": 8,
            "annotation_level": "failure",
            "title": "Unsafe &lt;b&gt;path&lt;/b&gt;",
            "message": "Quote `BASE_SHA`; do not leak [local-path]",
        }
    ]


def test_release_check_ci_simulate_passes():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    result = subprocess.run(
        [NODE, str(runtime), "release-check", "--ci-simulate"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["github-actions-template-safe"]["ok"] is True
    assert checks["github-actions-fork-safe"]["ok"] is True
    assert checks["github-actions-immutable-ref"]["ok"] is True


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


def test_reserve_job_prints_forwarding_worker_command(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("hello\n", encoding="utf8")

    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [NODE, str(runtime), "reserve-job", "review", "--base", "main", "--path", "file.txt"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "reserved"
    assert payload["job"]["status"] == "queued"
    assert payload["job"]["command"] == "review"
    assert payload["workerCommand"][0] == NODE
    assert str(runtime) in payload["workerCommand"]
    assert "run-reserved-job" in payload["workerCommand"]
    assert payload["forwardingInstructions"].startswith("Dispatch exactly one forwarding subagent")


@pytest.mark.parametrize(
    ("command_args", "stderr_marker"),
    [
        (["review", "--agent-team", "banana"], "Unsupported option --agent-team"),
        (["multi-review", "--max-budget-usd", "0x10"], "positive decimal"),
    ],
)
def test_reserve_job_validates_child_native_flags_before_reserving(tmp_path, command_args, stderr_marker):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [NODE, str(runtime), "reserve-job", *command_args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert stderr_marker in result.stderr
    jobs = subprocess.run(
        [NODE, str(runtime), "jobs"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert jobs.returncode == 0, jobs.stderr
    assert json.loads(jobs.stdout)["jobs"] == []


@pytest.mark.parametrize(
    ("command_args", "env_backend", "stderr_marker"),
    [
        (["multi-review", "--backend", "sdk", "--max-budget-usd", "1.50"], None, "--max-budget-usd"),
        (["multi-review", "--backend", "sdk", "--fallback-model", "claude-sonnet-4-5"], None, "--fallback-model"),
        (["multi-review", "--max-budget-usd", "1.50"], "sdk", "--max-budget-usd"),
    ],
)
def test_reserve_job_validates_child_backend_compatibility_before_reserving(
    tmp_path, command_args, env_backend, stderr_marker
):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    if env_backend is not None:
        env["CLAUDE_FOR_CODEX_BACKEND"] = env_backend

    result = subprocess.run(
        [NODE, str(runtime), "reserve-job", *command_args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert stderr_marker in result.stderr
    assert "CLI-only" in result.stderr or "unsupported for SDK backend" in result.stderr
    jobs = subprocess.run(
        [NODE, str(runtime), "jobs"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert jobs.returncode == 0, jobs.stderr
    assert json.loads(jobs.stdout)["jobs"] == []


def test_reserve_job_env_sdk_backend_allows_explicit_cli_budget_flags(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_BACKEND"] = "sdk"

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "reserve-job",
            "multi-review",
            "--backend",
            "cli",
            "--max-budget-usd",
            "1.50",
            "--fallback-model",
            "claude-sonnet-4-5",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "reserved"
    assert payload["job"]["status"] == "queued"
    assert payload["job"]["command"] == "multi-review"
    assert payload["job"]["args"] == [
        "--backend",
        "cli",
        "--max-budget-usd",
        "1.50",
        "--fallback-model",
        "claude-sonnet-4-5",
    ]


def test_run_reserved_job_executes_existing_job_with_fake_claude(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("hello\n", encoding="utf8")

    fake_claude = bin_dir / "claude"
    fake_claude.write_text("#!/bin/sh\nprintf 'CLAUDE RESERVED RESULT\\n'\n", encoding="utf8")
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_CODE_PATH"] = str(fake_claude)

    reserved = subprocess.run(
        [NODE, str(runtime), "reserve-job", "review", "focused"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert reserved.returncode == 0, reserved.stderr
    job_id = json.loads(reserved.stdout)["job"]["id"]

    worker = subprocess.run(
        [NODE, str(runtime), "run-reserved-job", "--job-id", job_id],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert worker.returncode == 0, worker.stderr

    result = subprocess.run(
        [NODE, str(runtime), "result", job_id, "--json"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["job"]["status"] == "succeeded"
    assert "CLAUDE RESERVED RESULT" in payload["job"]["stdout"]


def test_run_reserved_job_refuses_plain_queued_non_reserved_job(tmp_path):
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
    job_file = state_dir / "jobs" / "job-plain.json"
    job_file.write_text(json.dumps({
        "id": "job-plain",
        "status": "queued",
        "command": "review",
        "args": ["plain"],
        "cwd": str(repo),
        "workerCommand": [NODE, str(runtime), "run-reserved-job", "--job-id", "job-plain"]
    }))

    worker = subprocess.run(
        [NODE, str(runtime), "run-reserved-job", "--job-id", "job-plain"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert worker.returncode == 1
    payload = json.loads(worker.stdout)
    assert payload["status"] == "not_claimed"
    assert json.loads(job_file.read_text())["status"] == "queued"


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


def test_cancel_running_host_forwarded_reserved_job_validates_and_terminates_worker(tmp_path):
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
    completion_marker = tmp_path / "reserved-completed"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import os
import sys
import time

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
time.sleep(2)
open(os.environ["FAKE_CLAUDE_COMPLETION_MARKER"], "w", encoding="utf8").write("completed\\n")
print("SHOULD_NOT_FINISH")
"""
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAKE_CLAUDE_COMPLETION_MARKER"] = str(completion_marker)

    reserved = subprocess.run(
        [NODE, str(runtime), "reserve-job", "review", "long reserved review"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert reserved.returncode == 0, reserved.stderr
    job_id = json.loads(reserved.stdout)["job"]["id"]

    worker = subprocess.Popen(
        [NODE, str(runtime), "run-reserved-job", "--job-id", job_id],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
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
        assert "run-reserved-job" in job["pidIdentity"]["command"]

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
        assert cancel_payload["job"]["cancelIdentity"]["pid"] == worker.pid
        assert "run-reserved-job" in cancel_payload["job"]["cancelIdentity"]["command"]
        worker.wait(timeout=5)
        time.sleep(2.5)
        assert not completion_marker.exists()
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=5)


def test_cancel_running_host_forwarded_reserved_job_refuses_prefix_job_id_match(tmp_path):
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
import time

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
time.sleep(30)
"""
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    jobs = subprocess.run(
        [NODE, str(runtime), "jobs"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert jobs.returncode == 0, jobs.stderr
    state_dir = pathlib.Path(json.loads(jobs.stdout)["stateDir"])
    job_file = state_dir / "jobs" / "job-123.json"
    job_file.write_text(json.dumps({
        "id": "job-123",
        "status": "queued",
        "command": "review",
        "args": ["long reserved review"],
        "cwd": str(repo),
        "reservationMode": "host-forwarded",
        "workerCommand": [NODE, str(runtime), "run-reserved-job", "--job-id", "job-123"]
    }))

    worker = subprocess.Popen(
        [NODE, str(runtime), "run-reserved-job", "--job-id", "job-123"],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
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
            job = next(item for item in payload["jobs"] if item["id"] == "job-123")
            if job["status"] == "running":
                break
            time.sleep(0.1)
        assert job["status"] == "running"

        prefix_job = state_dir / "jobs" / "job-1.json"
        prefix_job.write_text(json.dumps({
            "id": "job-1",
            "status": "running",
            "command": "review",
            "args": ["prefix"],
            "cwd": str(repo),
            "workerPid": worker.pid,
            "pidIdentity": job["pidIdentity"]
        }))

        cancel = subprocess.run(
            [NODE, str(runtime), "cancel", "job-1"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
        )
        assert cancel.returncode == 1
        cancel_payload = json.loads(cancel.stdout)
        assert cancel_payload["status"] == "cancel_failed"
        assert "reserved job id" in cancel_payload["reason"]
        assert worker.poll() is None
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=5)


def test_reserved_job_identity_parser_ignores_pre_subcommand_job_id():
    process_module = (PLUGIN / "scripts" / "lib" / "process.mjs").as_uri()
    script = f"""
import assert from "node:assert/strict";
import {{ reservedJobIdFromCommandTokens }} from {json.dumps(process_module)};

const tokens = [
  "node",
  "claude-companion.mjs",
  "--job-id",
  "job-1",
  "run-reserved-job",
  "--job-id",
  "job-123"
];

assert.equal(reservedJobIdFromCommandTokens(tokens), "job-123");
assert.notEqual(reservedJobIdFromCommandTokens(tokens), "job-1");
"""
    result = subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_background_skills_require_forwarding_subagent_contract():
    skill_commands = {
        "claude-review": "review",
        "claude-adversarial-review": "adversarial-review",
        "claude-multi-review": "multi-review",
        "claude-rescue": "rescue",
    }

    for skill_name, command in skill_commands.items():
        text = (PLUGIN / "skills" / skill_name / "SKILL.md").read_text()
        normalized = re.sub(r"\s+", " ", text)
        assert f'reserve-job {command} "$ARGUMENTS"' in text
        assert "run-reserved-job" in text
        assert text.count("forwarding subagent") == 1
        assert "must not inspect or reinterpret the repository" in text
        assert "workerCommand` JSON argv array" in text
        assert "execute that array as argv" in text
        assert "preserving element boundaries" in text
        assert "quote every element" in text
        assert "claude-result <job-id>" in text
        assert "--wait` only applies to direct `--background` runtime use" in text
        assert "not part of the host-forwarded `reserve-job` path" in text
        assert re.search(r"waiting requires .*claude-result <job-id>", normalized)


def test_review_skills_preserve_result_handling_discipline():
    for skill_name in ["claude-review", "claude-multi-review", "claude-adversarial-review", "claude-rescue"]:
        text = (PLUGIN / "skills" / skill_name / "SKILL.md").read_text(encoding="utf8")
        assert "Do not fix" in text or "not apply fixes" in text or "not edit files" in text
        assert "file paths" in text
        assert "line numbers" in text or skill_name == "claude-rescue"
        assert "uncertainty" in text or "Evidence" in text or "evidence" in text
        assert "background" in text

    review = (PLUGIN / "skills" / "claude-review" / "SKILL.md").read_text(encoding="utf8")
    multi = (PLUGIN / "skills" / "claude-multi-review" / "SKILL.md").read_text(encoding="utf8")
    assert "Tiny one-to-two file reviews can run foreground" in review
    assert "broader or unclear reviews should use background" in review
    assert "Tiny one-to-two file reviews can run foreground" in multi


def test_render_review_module_normalizes_review_and_adversarial_shapes():
    module = (PLUGIN / "scripts" / "lib" / "render-review.mjs").as_uri()
    script = f"""
import assert from "node:assert/strict";
import {{ normalizeReviewOutput, normalizeAdversarialOutput, aggregateRoleReviewOutputs }} from {json.dumps(module)};

const review = normalizeReviewOutput({{
  verdict: "needs-attention",
  summary: "sum",
  findings: [
    {{ severity: "low", title: "low", body: "body", file: "b", line_start: 2, line_end: 2, confidence: 0.4, recommendation: "" }},
    {{ severity: "critical", title: "critical", body: "body", file: "a", line_start: 1, line_end: 1, confidence: 0.5, recommendation: "fix" }}
  ],
  next_steps: ["next"]
}}, {{ role: "security" }});
assert.equal(review.findings[0].severity, "critical");
assert.equal(review.findings[0].role, "security");
assert.equal(review.findings[1].confidence, 0.4);
    assert.throws(() => normalizeReviewOutput({{
      verdict: "approve",
      summary: "sum",
      findings: [{{ severity: "unknown", title: "bad", body: "body", file: "a", line_start: 1, line_end: 1, confidence: 0.5, recommendation: "" }}],
      next_steps: []
    }}), /severity/);

const adversarial = normalizeAdversarialOutput({{ verdict: "CONTESTED", summary: "s", findings: [], next_steps: [] }});
assert.equal(adversarial.verdict, "CONTESTED");

const aggregate = aggregateRoleReviewOutputs([{{ role: {{ name: "security" }}, result: review }}]);
assert.equal(aggregate.verdict, "needs-attention");
assert.equal(aggregate.roles[0].role, "security");
"""
    result = subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


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
    assert "--mcp-config" not in write_argv
    assert "--strict-mcp-config" not in write_argv
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


def test_mcp_git_rejects_unsafe_paths_and_refs(tmp_path):
    helper = PLUGIN / "scripts" / "lib" / "mcp-git.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)

    result = subprocess.run(
        [NODE, str(helper), "selftest"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["safePath"]["file.txt"] is True
    assert payload["safePath"]["../secret"] is False
    assert payload["safePath"]["-p"] is False
    assert payload["safePath"]["a;b"] is False
    assert payload["safePath"]["a$(touch x)"] is False
    assert payload["safePath"]["a\nb"] is False
    assert payload["safeRef"]["HEAD"] is True
    assert payload["safeRef"]["main~1"] is True
    assert payload["safeRef"]["main;rm -rf /"] is False
    assert payload["safeRef"]["--help"] is False


def test_mcp_git_server_recovers_after_malformed_and_invalid_requests(tmp_path):
    helper = PLUGIN / "scripts" / "lib" / "mcp-git.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    requests = [
        "{not json",
        "null",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
    ]

    result = subprocess.run(
        [NODE, str(helper), "server"],
        cwd=repo,
        input="\n".join(requests) + "\n",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [response.get("id") for response in responses] == [None, None, 1, 2]
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["error"]["code"] == -32600
    assert responses[2]["result"]["serverInfo"]["name"] == "claude-for-codex-git"
    assert any(tool["name"] == "git_grep" for tool in responses[3]["result"]["tools"])


def test_mcp_git_server_rejects_option_like_grep_pattern(tmp_path):
    helper = PLUGIN / "scripts" / "lib" / "mcp-git.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    request = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "git_grep",
            "arguments": {"pattern": "--cached"},
        },
    }

    result = subprocess.run(
        [NODE, str(helper), "server"],
        cwd=repo,
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["id"] == 7
    assert response["error"]["code"] == -32602
    assert "pattern must not start with '-'" in response["error"]["message"]
    assert "fatal" not in response["error"]["message"].lower()


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


def test_review_gate_semantic_failure_records_degraded_pass(tmp_path):
    runtime, repo, _capture_dir, env = prepare_gate_repo(tmp_path)
    _provider, config_path, _semantic_capture = write_semantic_provider(
        tmp_path,
        extra_script="raise SystemExit(9)\n",
    )
    env["CLAUDE_FOR_CODEX_SEMANTIC_CONFIG"] = str(config_path)
    setup = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr

    result = subprocess.run(
        ["node", str(runtime), "review-gate", "--semantic-context", "fake"],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "Stop", "cwd": str(repo)}),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert "degraded gate" in result.stderr
    latest = subprocess.run(
        [NODE, str(runtime), "report", "--latest"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    report = json.loads(latest.stdout)["report"]
    assert report["command"] == "review-gate"
    assert report["semanticProvider"] == "fake"
    assert report["semanticFailed"] is True
    assert report["semanticVerdict"] == "DEGRADED_PASS"
    assert report["semanticFailureReason"] == "nonzero_exit"


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
        "claude-github-actions-review": "github-actions",
        "claude-leases": "leases",
        "claude-mailbox": "mailbox",
            "claude-multi-review": "multi-review",
        "claude-role-packs": "roles",
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
            "claude-github-actions-review",
        "claude-leases",
        "claude-mailbox",
            "claude-multi-review",
        "claude-role-packs",
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
