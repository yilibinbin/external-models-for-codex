import json
import os
import pathlib
import shutil
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "gemini-for-codex"
NODE = os.environ.get("NODE_BINARY") or shutil.which("node") or "/Applications/Codex.app/Contents/Resources/node"
NODE_DIR = str(pathlib.Path(NODE).resolve().parent)
FAKE_GEMINI_HELP = (
    "Usage: gemini\n"
    "  -p, --prompt TEXT\n"
    "  --output-format json\n"
    "  --approval-mode plan\n"
    "  --skip-trust\n"
    "  -s, --sandbox\n"
)
FAKE_GEMINI_HELP_WITH_SESSIONS = (
    FAKE_GEMINI_HELP
    + "  --resume latest\n"
    + "  --session-id UUID\n"
    + "  --session-file FILE\n"
    + "  --list-sessions\n"
    + "  --worktree NAME\n"
    + "  --include-directories DIR\n"
)


def write_fake_gemini(script, response="GEMINI_OK", capture_argv=None, first_line=None, help_text=FAKE_GEMINI_HELP, exit_on=None):
    payload = json.dumps({"response": response, "stats": {}})
    if first_line:
        payload = json.dumps({"response": first_line, "stats": {}})
    capture = f"fs.writeFileSync({json.dumps(str(capture_argv))}, JSON.stringify(process.argv.slice(2)));" if capture_argv else ""
    exit_block = ""
    if exit_on:
        exit_block = (
            f"if (process.argv.slice(2).includes({json.dumps(exit_on)})) "
            "{ console.error('fake unsupported or missing session'); process.exit(7); }\n"
        )
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('fs');\n"
        "if (process.argv.slice(2).join(' ') === '--version') { console.log('0.0.0-fake'); process.exit(0); }\n"
        f"if (process.argv.slice(2).join(' ') === '--help') {{ process.stdout.write({json.dumps(help_text)}); process.exit(0); }}\n"
        f"{exit_block}"
        f"{capture}\n"
        f"process.stdout.write({json.dumps(payload)});\n",
        encoding="utf8",
    )
    script.chmod(0o755)
    return script


def init_git_repo(repo):
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)


def fake_gemini(tmp_path, response="GEMINI_OK", capture_argv=None, first_line=None, help_text=FAKE_GEMINI_HELP, exit_on=None):
    return write_fake_gemini(tmp_path / "gemini", response=response, capture_argv=capture_argv, first_line=first_line, help_text=help_text, exit_on=exit_on)


def fake_gemini_jsonl(tmp_path, log_file, delay_ms=0):
    script = tmp_path / "gemini"
    script.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('fs');\n"
        f"const logFile = {json.dumps(str(log_file))};\n"
        f"const delayMs = {int(delay_ms)};\n"
        "const argv = process.argv.slice(2);\n"
        "if (argv.join(' ') === '--version') { console.log('0.0.0-fake'); process.exit(0); }\n"
        f"if (argv.join(' ') === '--help') {{ process.stdout.write({json.dumps(FAKE_GEMINI_HELP)}); process.exit(0); }}\n"
        "const promptIndex = argv.indexOf('--prompt');\n"
        "const prompt = promptIndex >= 0 ? argv[promptIndex + 1] : '';\n"
        "const start = Date.now();\n"
        "setTimeout(() => {\n"
        "  const end = Date.now();\n"
        "  fs.appendFileSync(logFile, JSON.stringify({argv, prompt, cwd: process.cwd(), start, end, agentsDirExists: fs.existsSync('.gemini/agents')}) + '\\n');\n"
        "  process.stdout.write(JSON.stringify({response: `OK ${prompt.match(/<role_name>([^<]+)/)?.[1] || 'native'}`, stats: {}}));\n"
        "}, delayMs);\n",
        encoding="utf8",
    )
    script.chmod(0o755)
    return script


def test_gemini_plugin_manifest_is_valid_json():
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf8"))
    assert manifest["name"] == "gemini-for-codex"
    assert manifest["version"] == "0.3.0"
    assert manifest["skills"] == "./skills/"
    assert "gemini" in manifest["keywords"]
    assert "review" in manifest["keywords"]
    assert "mcp" not in manifest["keywords"]
    assert manifest["homepage"] == "https://github.com/yilibinbin/external-models-for-codex"
    assert manifest["repository"] == "https://github.com/yilibinbin/external-models-for-codex"
    assert manifest["interface"]["displayName"] == "Gemini for Codex"
    assert manifest["interface"]["websiteURL"] == "https://github.com/yilibinbin/external-models-for-codex"
    assert "schema-validated structured review" in manifest["interface"]["longDescription"].lower()
    assert "session lifecycle hooks" in manifest["interface"]["longDescription"].lower()
    assert "Gemini native session and worktree capability gating" in manifest["interface"]["capabilities"]


def test_marketplace_lists_gemini_for_codex():
    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf8"))
    assert marketplace["name"] == "external-models-for-codex"
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
    assert payload["geminiCapabilities"]["resume"] is False


def test_setup_discovers_gemini_from_node_manager_paths_when_path_is_reduced(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    home = tmp_path / "home"
    fake = write_fake_gemini(home / ".nvm" / "versions" / "node" / "v22.0.0" / "bin" / "gemini")
    env = os.environ.copy()
    env.pop("GEMINI_CLI_PATH", None)
    env["HOME"] = str(home)
    env["PATH"] = f"{NODE_DIR}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["GEMINI_FOR_CODEX_DATA"] = str(tmp_path / "data")

    result = subprocess.run([NODE, str(runtime), "setup"], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["geminiAvailable"] is True
    assert payload["geminiCommand"] == str(fake)
    assert payload["geminiPreflight"]["ok"] is True


def test_setup_reports_gemini_session_capabilities(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    fake = fake_gemini(tmp_path, help_text=FAKE_GEMINI_HELP_WITH_SESSIONS)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake)
    env["GEMINI_FOR_CODEX_DATA"] = str(tmp_path / "data")

    result = subprocess.run([NODE, str(runtime), "setup"], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["geminiCapabilities"]["resume"] is True
    assert payload["geminiCapabilities"]["sessionId"] is True
    assert payload["geminiCapabilities"]["worktree"] is True


def test_setup_discovers_gemini_from_package_manager_prefix(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    prefix = tmp_path / "prefix"
    fake = write_fake_gemini(prefix / "bin" / "gemini")
    env = os.environ.copy()
    env.pop("GEMINI_CLI_PATH", None)
    env["HOME"] = str(tmp_path / "home")
    env["PATH"] = f"{NODE_DIR}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["HOMEBREW_PREFIX"] = str(prefix)
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


def test_review_structured_validates_and_renders_json(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    response = (
        "Here is the review:\n```json\n"
        + json.dumps({
            "verdict": "needs-attention",
            "summary": "One issue.",
            "findings": [{
                "severity": "high",
                "title": "Broken edge case",
                "body": "The changed path can fail.",
                "file": "file.txt",
                "line_start": 1,
                "line_end": 2,
                "confidence": 0.9,
                "recommendation": "Add a guard."
            }],
            "next_steps": ["Fix the guard."]
        })
        + "\n```"
    )
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path, response=response))

    result = subprocess.run([NODE, str(runtime), "review", "--structured", "focus"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "## Verdict: needs-attention" in result.stdout
    assert "[high] file.txt:1-2 - Broken edge case" in result.stdout


def test_review_structured_invalid_output_fails_nonzero(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path, response="No JSON here"))

    result = subprocess.run([NODE, str(runtime), "review", "--structured"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 1
    assert "Invalid structured review output" in result.stderr


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


def test_session_flags_are_capability_gated_and_forwarded(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "argv.json"
    repo.mkdir()
    init_git_repo(repo)

    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path, capture_argv=argv_file, help_text=FAKE_GEMINI_HELP_WITH_SESSIONS))
    ok = subprocess.run(
        [NODE, str(runtime), "rescue", "--resume=latest", "--session-id", "abc-123", "--worktree=scratch", "focus"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0, ok.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))
    assert "--resume" in argv and argv[argv.index("--resume") + 1] == "latest"
    assert "--session-id" in argv and argv[argv.index("--session-id") + 1] == "abc-123"
    assert "--worktree" in argv and argv[argv.index("--worktree") + 1] == "scratch"
    assert "--prompt" in argv and "<rescue_request>focus</rescue_request>" in argv[argv.index("--prompt") + 1]
    assert "--approval-mode" in argv and argv[argv.index("--approval-mode") + 1] == "plan"
    assert "yolo" not in argv

    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path / "limited"))
    blocked = subprocess.run([NODE, str(runtime), "rescue", "--resume=latest"], cwd=repo, env=env, capture_output=True, text=True)
    assert blocked.returncode == 2
    assert "does not report support for --resume" in blocked.stderr


def test_resume_failure_is_reported_without_fake_success(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path, help_text=FAKE_GEMINI_HELP_WITH_SESSIONS, exit_on="--resume"))

    result = subprocess.run([NODE, str(runtime), "rescue", "--resume=latest"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 7
    assert "fake unsupported or missing session" in result.stderr


def test_sessions_command_is_capability_gated(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path / "limited"))
    blocked = subprocess.run([NODE, str(runtime), "sessions"], cwd=repo, env=env, capture_output=True, text=True)
    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["available"] is False

    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path, help_text=FAKE_GEMINI_HELP_WITH_SESSIONS, response="session-list"))
    ok = subprocess.run([NODE, str(runtime), "sessions"], cwd=repo, env=env, capture_output=True, text=True)
    assert ok.returncode == 0
    payload = json.loads(ok.stdout)
    assert payload["available"] is True
    assert "session-list" in payload["stdout"]


def test_gemini_rejects_roles_outside_multi_review(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path))

    for command in ["adversarial-review", "plan", "rescue", "review"]:
        result = subprocess.run([NODE, str(runtime), command, "--roles", "security"], cwd=repo, env=env, capture_output=True, text=True)
        assert result.returncode == 2
        assert "--roles is only valid for multi-review" in result.stderr


def test_gemini_rejects_structured_outside_review_and_resume_with_fresh(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path, help_text=FAKE_GEMINI_HELP_WITH_SESSIONS))

    structured = subprocess.run([NODE, str(runtime), "plan", "--structured"], cwd=repo, env=env, capture_output=True, text=True)
    assert structured.returncode == 2
    assert "--structured is only valid for review" in structured.stderr

    resume_fresh = subprocess.run([NODE, str(runtime), "rescue", "--resume=latest", "--fresh"], cwd=repo, env=env, capture_output=True, text=True)
    assert resume_fresh.returncode == 2
    assert "Choose either --resume or --fresh" in resume_fresh.stderr


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


def test_multi_review_runs_role_invocations_in_parallel(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    log_file = tmp_path / "gemini-calls.jsonl"
    repo.mkdir()
    init_git_repo(repo)
    fake = fake_gemini_jsonl(tmp_path, log_file, delay_ms=300)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake)

    result = subprocess.run(
        [NODE, str(runtime), "multi-review", "--roles", "correctness,security", "focus"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log_file.read_text(encoding="utf8").splitlines()]
    assert len(calls) == 2
    prompts = [call["prompt"] for call in calls]
    assert any("<role_name>correctness</role_name>" in prompt for prompt in prompts)
    assert any("<role_name>security</role_name>" in prompt for prompt in prompts)
    assert max(call["start"] for call in calls) < min(call["end"] for call in calls)
    assert "orchestration: parallel Gemini CLI role fan-out" in result.stdout


def test_multi_review_native_agents_uses_gemini_subagent_prompt_and_workspace(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    log_file = tmp_path / "gemini-native.jsonl"
    repo.mkdir()
    init_git_repo(repo)
    fake = fake_gemini_jsonl(tmp_path, log_file)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake)

    result = subprocess.run(
        [NODE, str(runtime), "multi-review", "--native-agents", "--roles", "correctness,security", "focus"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log_file.read_text(encoding="utf8").splitlines()]
    assert len(calls) == 1
    call = calls[0]
    assert call["agentsDirExists"] is True
    assert "@gfc_correctness" in call["prompt"]
    assert "@gfc_security" in call["prompt"]
    assert "--include-directories" in call["argv"]
    assert str(repo) in call["argv"]
    assert "orchestration: Gemini native subagents" in result.stdout


def test_recommend_execution_mode_handles_small_large_and_invalid_git(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    clean = subprocess.run([NODE, str(runtime), "recommend-execution-mode"], cwd=repo, capture_output=True, text=True)
    assert clean.returncode == 0
    clean_payload = json.loads(clean.stdout)
    assert clean_payload["recommendation"] == "foreground"
    assert clean_payload["reviewable"] is False

    (repo / "one.txt").write_text("one\n", encoding="utf8")
    small = subprocess.run([NODE, str(runtime), "recommend-execution-mode"], cwd=repo, capture_output=True, text=True)
    small_payload = json.loads(small.stdout)
    assert small_payload["recommendation"] == "background"
    assert small_payload["hasUntracked"] is True

    nongit = tmp_path / "nongit"
    nongit.mkdir()
    not_repo = subprocess.run([NODE, str(runtime), "recommend-execution-mode"], cwd=nongit, capture_output=True, text=True)
    not_repo_payload = json.loads(not_repo.stdout)
    assert not_repo_payload["git"]["repository"] is False


def test_recommend_execution_mode_uses_changed_line_threshold(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("\n".join(f"line {i}" for i in range(80)) + "\n", encoding="utf8")

    result = subprocess.run([NODE, str(runtime), "recommend-execution-mode"], cwd=repo, capture_output=True, text=True)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["recommendation"] == "background"
    assert payload["changedLineEstimate"] > 50


def test_recommend_execution_mode_invalid_base_fails_safe(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)

    result = subprocess.run([NODE, str(runtime), "recommend-execution-mode", "--base", "missing"], cwd=repo, capture_output=True, text=True)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["recommendation"] == "background"
    assert payload["git"]["branch"]["available"] is False


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


def test_lifecycle_hooks_track_session_cancel_only_same_session_and_unread_results(tmp_path):
    runtime = PLUGIN / "scripts" / "gemini-companion.mjs"
    lifecycle = PLUGIN / "hooks" / "session-lifecycle.mjs"
    unread = PLUGIN / "hooks" / "unread-result.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = os.environ.copy()
    env["GEMINI_CLI_PATH"] = str(fake_gemini(tmp_path))
    env["GEMINI_FOR_CODEX_DATA"] = str(tmp_path / "data")

    start_payload = {"cwd": str(repo), "session_id": "s1", "hook_event_name": "SessionStart", "extra": "kept"}
    start = subprocess.run([NODE, str(lifecycle), "SessionStart"], cwd=repo, env=env, input=json.dumps(start_payload), capture_output=True, text=True)
    assert start.returncode == 0
    current_files = list((tmp_path / "data" / "state").glob("*/current-session.json"))
    assert current_files
    current = json.loads(current_files[0].read_text(encoding="utf8"))
    assert current["sessionId"] == "s1"
    assert "extra" in current["hookPayloadKeys"]

    reserved_same = subprocess.run([NODE, str(runtime), "reserve-job", "review", "--background"], cwd=repo, env=env, capture_output=True, text=True)
    same_job = json.loads(reserved_same.stdout)["job"]["id"]
    # Create a second sessionless job after removing the current session file.
    current_files[0].unlink()
    reserved_other = subprocess.run([NODE, str(runtime), "reserve-job", "review", "--background"], cwd=repo, env=env, capture_output=True, text=True)
    other_job = json.loads(reserved_other.stdout)["job"]["id"]

    no_session_end = subprocess.run([NODE, str(lifecycle), "SessionEnd"], cwd=repo, env=env, input=json.dumps({"cwd": str(repo)}), capture_output=True, text=True)
    assert no_session_end.returncode == 0
    assert "missing session id" in no_session_end.stderr

    end = subprocess.run([NODE, str(lifecycle), "SessionEnd"], cwd=repo, env=env, input=json.dumps({"cwd": str(repo), "session_id": "s1"}), capture_output=True, text=True)
    assert end.returncode == 0
    jobs = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True)
    job_map = {job["id"]: job for job in json.loads(jobs.stdout)["jobs"]}
    assert job_map[same_job]["status"] == "cancelled"
    assert job_map[other_job]["status"] == "queued"

    # Mark the untouched job as a terminal unread result and verify the prompt hook reminds without corrupting baseline JSON.
    job_file = next((tmp_path / "data" / "state").glob(f"*/jobs/{other_job}.json"))
    job_data = json.loads(job_file.read_text(encoding="utf8"))
    job_data.update({"status": "succeeded", "sessionId": ""})
    job_file.write_text(json.dumps(job_data), encoding="utf8")
    prompt = subprocess.run([NODE, str(unread)], cwd=repo, env=env, input=json.dumps({"cwd": str(repo), "hook_event_name": "UserPromptSubmit"}), capture_output=True, text=True)
    assert prompt.returncode == 0
    assert "Unread Gemini job result" in prompt.stderr
    baseline_files = list((tmp_path / "data" / "state").glob("*/turn-baseline.json"))
    assert baseline_files
    json.loads(baseline_files[0].read_text(encoding="utf8"))


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


def test_gemini_hooks_manifest_registers_required_hooks():
    manifest = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf8"))
    assert sorted(manifest["hooks"].keys()) == ["SessionEnd", "SessionStart", "Stop", "UserPromptSubmit"]
    assert "GEMINI_PLUGIN_ROOT" in json.dumps(manifest)
    assert "CLAUDE_" not in json.dumps(manifest)


def test_gemini_prompt_templates_and_schema_are_packaged():
    for name in ["review", "adversarial-review", "multi-review-role", "native-multi-agent", "stop-review-gate"]:
        text = (PLUGIN / "prompts" / f"{name}.md").read_text(encoding="utf8")
        assert "{{" in text
        assert "<task>" in text
    schema = json.loads((PLUGIN / "schemas" / "review-output.schema.json").read_text(encoding="utf8"))
    assert schema["required"] == ["verdict", "summary", "findings", "next_steps"]
    assert schema["properties"]["verdict"]["enum"] == ["approve", "needs-attention"]


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
            text = text.replace("claude-for-codex@external-models-for-codex", "")
            text = text.replace("external-models-for-codex", "")
        if path.name == "plugin.json":
            text = text.replace("https://github.com/yilibinbin/external-models-for-codex", "")
        for token in forbidden:
            assert token not in text, f"{token} residue in {path}"
