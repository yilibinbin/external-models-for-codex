import json
import os
import pathlib
import shutil
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "gemini-for-codex"
NODE = os.environ.get("NODE_BINARY") or shutil.which("node") or "/Applications/Codex.app/Contents/Resources/node"
FAKE_GEMINI_HELP = (
    "Usage: gemini\n"
    "  -p, --prompt TEXT\n"
    "  --output-format json\n"
    "  --approval-mode plan\n"
    "  --skip-trust\n"
    "  -s, --sandbox\n"
)


def init_git_repo(repo):
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)


def fake_gemini(tmp_path, response="GEMINI_OK", capture_argv=None, first_line=None):
    script = tmp_path / "gemini"
    payload = json.dumps({"response": response, "stats": {}})
    if first_line:
      payload = json.dumps({"response": first_line, "stats": {}})
    capture = f"fs.writeFileSync({json.dumps(str(capture_argv))}, JSON.stringify(process.argv.slice(2)));" if capture_argv else ""
    script.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('fs');\n"
        "if (process.argv.slice(2).join(' ') === '--version') { console.log('0.0.0-fake'); process.exit(0); }\n"
        f"if (process.argv.slice(2).join(' ') === '--help') {{ process.stdout.write({json.dumps(FAKE_GEMINI_HELP)}); process.exit(0); }}\n"
        f"{capture}\n"
        f"process.stdout.write({json.dumps(payload)});\n",
        encoding="utf8",
    )
    script.chmod(0o755)
    return script


def test_gemini_plugin_manifest_is_valid_json():
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf8"))
    assert manifest["name"] == "gemini-for-codex"
    assert manifest["version"] == "0.1.0"
    assert manifest["skills"] == "./skills/"
    assert "gemini" in manifest["keywords"]
    assert "review" in manifest["keywords"]
    assert "mcp" not in manifest["keywords"]
    assert manifest["interface"]["displayName"] == "Gemini for Codex"
    assert "lifecycle" not in manifest["interface"]["longDescription"].lower()


def test_marketplace_lists_gemini_for_codex():
    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf8"))
    assert marketplace["name"] == "claude-for-codex-local"
    plugins = {item["name"]: item for item in marketplace["plugins"]}
    assert plugins["gemini-for-codex"]["source"]["path"] == "./plugins/gemini-for-codex"
    assert plugins["gemini-for-codex"]["source"]["source"] == "local"
    assert plugins["gemini-for-codex"]["policy"]["installation"] == "AVAILABLE"
    assert plugins["gemini-for-codex"]["policy"]["authentication"] == "ON_USE"
    assert plugins["gemini-for-codex"]["category"] == "Productivity"
    assert len(plugins) == len(marketplace["plugins"])


def test_setup_reports_gemini_availability_with_fake_binary(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    fake = fake_gemini(tmp_path)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake)
    env["GEMINI_FOR_CODEX_DATA"] = str(tmp_path / "data")

    result = subprocess.run([NODE, str(runtime), "setup"], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["geminiAvailable"] is True
    assert payload["geminiCommand"] == str(fake)
    assert payload["geminiPreflight"]["ok"] is True


def test_review_invokes_gemini_plan_mode_json_with_fake_binary(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "argv.json"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "file.txt").write_text("hello\n", encoding="utf8")
    fake = fake_gemini(tmp_path, capture_argv=argv_file)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake)

    result = subprocess.run([NODE, str(runtime), "review", "focus"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "GEMINI_OK" in result.stdout
    argv = json.loads(argv_file.read_text(encoding="utf8"))
    assert "--approval-mode" in argv
    assert argv[argv.index("--approval-mode") + 1] == "plan"
    assert "--skip-trust" in argv
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--prompt" in argv
    assert "--settings" not in argv
    assert "--mcp-config" not in argv


def test_gemini_rejects_write_mode_for_all_commands(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path))

    for command in ["review", "adversarial-review", "multi-review", "plan", "rescue"]:
        result = subprocess.run([NODE, str(runtime), command, "--write"], cwd=repo, env=env, capture_output=True, text=True)
        assert result.returncode == 2
        assert "--write is not supported" in result.stderr


def test_review_prompt_includes_bounded_git_context(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "argv.json"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "file.txt").write_text("old\n", encoding="utf8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("new\n", encoding="utf8")
    fake = fake_gemini(tmp_path, response="OK", capture_argv=argv_file)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake)

    result = subprocess.run([NODE, str(runtime), "review", "--path", "file.txt", "focus"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))
    prompt = argv[argv.index("--prompt") + 1]
    assert "git status" in prompt
    assert "git diff" in prompt
    assert "file.txt" in prompt
    assert "-old" in prompt
    assert "+new" in prompt


def test_gemini_review_branch_scope_uses_base_ref_and_pathspec(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "argv.json"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("feature\n", encoding="utf8")
    subprocess.run(["git", "commit", "-am", "feature"], cwd=repo, check=True, capture_output=True, text=True)
    fake = fake_gemini(tmp_path, response="OK", capture_argv=argv_file)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake)

    result = subprocess.run([NODE, str(runtime), "review", "--scope", "branch", "--base", "main", "--path", "file.txt"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))
    prompt = argv[argv.index("--prompt") + 1]
    assert "main...HEAD" in prompt
    assert "file.txt" in prompt
    assert "+feature" in prompt


def test_setup_review_gate_enable_disable_persists_state(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path))
    env["GEMINI_FOR_CODEX_DATA"] = str(tmp_path / "data")

    enabled = subprocess.run([NODE, str(runtime), "setup", "--enable-review-gate"], cwd=repo, env=env, capture_output=True, text=True)
    assert enabled.returncode == 0, enabled.stderr
    assert json.loads(enabled.stdout)["reviewGate"]["enabled"] is True

    disabled = subprocess.run([NODE, str(runtime), "setup", "--disable-review-gate"], cwd=repo, env=env, capture_output=True, text=True)
    assert disabled.returncode == 0, disabled.stderr
    assert json.loads(disabled.stdout)["reviewGate"]["enabled"] is False


def test_review_gate_blocks_only_explicit_block(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("changed\n", encoding="utf8")
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path, first_line="BLOCK: stop here\nEvidence"))
    env["GEMINI_FOR_CODEX_DATA"] = str(tmp_path / "data")
    subprocess.run([NODE, str(runtime), "setup", "--enable-review-gate"], cwd=repo, env=env, check=True, capture_output=True, text=True)

    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "BLOCK" not in result.stderr


def test_hook_wrapper_forwards_stdin_and_fails_open(tmp_path):
    hook = PLUGIN / "hooks" / "gemini-review-gate.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = os.environ.copy()
    env["GEMINI_FOR_CODEX_REVIEW_GATE"] = "off"
    result = subprocess.run([NODE, str(hook)], cwd=repo, env=env, input=json.dumps({"stop_hook_active": True}), capture_output=True, text=True)
    assert result.returncode == 0


def test_gemini_hooks_manifest_only_registers_stop_hook():
    manifest = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf8"))
    assert sorted(manifest["hooks"].keys()) == ["Stop"]
    assert "GEMINI_PLUGIN_ROOT" in json.dumps(manifest)
    assert "CLAUDE_" not in json.dumps(manifest)


def test_gemini_skills_have_frontmatter_and_runtime_calls():
    expected = {
        "gemini-review": "review",
        "gemini-adversarial-review": "adversarial-review",
        "gemini-plan": "plan",
        "gemini-multi-review": "multi-review",
        "gemini-rescue": "rescue",
        "gemini-status": "jobs",
        "gemini-result": "result",
        "gemini-cancel": "cancel",
        "gemini-review-gate": "setup --enable-review-gate",
        "gemini-collaboration-loop": "plan",
    }
    for skill, command in expected.items():
        text = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf8")
        assert text.startswith("---\n")
        assert f'node "${{CODEX_PLUGIN_ROOT}}/scripts/gemini-companion.mjs" {command}' in text
        if skill in {"gemini-review", "gemini-adversarial-review", "gemini-multi-review", "gemini-rescue"}:
            assert "reserve-job" in text
            assert "run-reserved-job" in text
            assert "workerCommand" in text


def test_gemini_plugin_files_do_not_ship_claude_residue():
    forbidden = ["CLAUDE_", "claude-companion", "claude-for-codex"]
    for path in PLUGIN.rglob("*"):
        if not path.is_file() or path.suffix in {".svg"}:
            continue
        text = path.read_text(encoding="utf8")
        if path.name == "README.md":
            text = text.replace("claude-for-codex-local", "")
        for token in forbidden:
            assert token not in text, f"{token} residue in {path}"
