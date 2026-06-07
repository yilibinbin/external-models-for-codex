import json
import os
import pathlib
import shutil
import subprocess
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "antigravity-for-codex"
NODE = os.environ.get("NODE_BINARY") or shutil.which("node") or "/Applications/Codex.app/Contents/Resources/node"


def test_antigravity_manifest_is_valid_json():
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf8"))
    assert manifest["name"] == "antigravity-for-codex"
    assert manifest["version"] == "0.1.0"
    assert manifest["skills"] == "./skills/"
    assert "antigravity" in manifest["keywords"]
    assert "gemini" in manifest["keywords"]
    assert "claude" in manifest["keywords"]
    text = json.dumps(manifest)
    assert "Gemini CLI" not in text
    assert "Claude Code CLI" not in text


def test_marketplace_lists_antigravity_plugin():
    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf8"))
    plugins = {item["name"]: item for item in marketplace["plugins"]}
    assert plugins["antigravity-for-codex"]["source"]["path"] == "./plugins/antigravity-for-codex"
    assert plugins["antigravity-for-codex"]["policy"]["installation"] == "AVAILABLE"
    assert plugins["antigravity-for-codex"]["policy"]["authentication"] == "ON_USE"


def test_marketplace_has_three_distinct_external_model_plugins():
    marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf8"))
    names = [item["name"] for item in marketplace["plugins"]]
    assert "claude-for-codex" in names
    assert "gemini-for-codex" in names
    assert "antigravity-for-codex" in names
    assert len(names) == len(set(names))


def test_antigravity_package_does_not_ship_copied_gemini_plugin_residue():
    forbidden = [
        "{{GEMINI_CONTEXT}}",
        "<gemini_context>",
        "native Gemini",
        "Gemini returned",
        "Gemini output",
        "gemini-companion",
        "plugins/gemini-for-codex",
        "GEMINI_FOR_CODEX",
        "GEMINI_PLUGIN_ROOT",
        "Gemini for Codex",
        "Gemini CLI",
    ]
    paths = [
        PLUGIN / ".codex-plugin" / "plugin.json",
        PLUGIN / "README.md",
        PLUGIN / "CHANGELOG.md",
        *sorted((PLUGIN / "assets").glob("*.svg")),
        *sorted((PLUGIN / "prompts").glob("*.md")),
        *sorted((PLUGIN / "schemas").glob("*.json")),
        *sorted((PLUGIN / "scripts" / "lib").glob("*.mjs")),
    ]
    for path in paths:
        text = path.read_text(encoding="utf8")
        for token in forbidden:
            assert token not in text, f"{token} residue in {path}"


def test_antigravity_package_only_ships_wired_runtime_files():
    expected_libs = {
        "antigravity-runtime.mjs",
        "github-actions.mjs",
        "jobs.mjs",
        "prompt-template.mjs",
        "process.mjs",
        "reports.mjs",
        "role-packs.mjs",
        "state.mjs",
        "structured-output.mjs",
    }
    actual_libs = {path.name for path in (PLUGIN / "scripts" / "lib").glob("*.mjs")}
    assert actual_libs == expected_libs
    expected_prompts = {
        "adversarial-review.md",
        "multi-review-role.md",
        "plan.md",
        "rescue.md",
        "review-gate-role.md",
        "review.md",
    }
    actual_prompts = {path.name for path in (PLUGIN / "prompts").glob("*.md")}
    assert actual_prompts == expected_prompts
    assert {path.name for path in (PLUGIN / "schemas").glob("*.json")} == {"review-output.schema.json"}


def test_antigravity_skills_exist_and_use_antigravity_commands():
    expected = [
        "antigravity-review",
        "antigravity-adversarial-review",
        "antigravity-multi-review",
        "antigravity-plan",
        "antigravity-rescue",
        "antigravity-review-gate",
        "antigravity-github-actions-review",
        "antigravity-role-packs",
        "antigravity-status",
        "antigravity-result",
        "antigravity-cancel",
    ]
    for skill in expected:
        path = PLUGIN / "skills" / skill / "SKILL.md"
        assert path.exists(), f"missing {path}"
        text = path.read_text(encoding="utf8")
        assert f"name: {skill}" in text
        assert "antigravity-companion.mjs" in text
        assert "gemini-companion.mjs" not in text
        assert "claude-companion.mjs" not in text
    unexpected = [
        "antigravity-collaboration-loop",
    ]
    for skill in unexpected:
        assert not (PLUGIN / "skills" / skill).exists()


def test_antigravity_hooks_use_antigravity_env_names():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf8"))
    text = json.dumps(hooks)
    assert "ANTIGRAVITY_PLUGIN_ROOT" in text
    assert "GEMINI_PLUGIN_ROOT" not in text
    assert "CLAUDE_PLUGIN_ROOT" not in text
    stop_hooks = hooks["hooks"]["Stop"][0]["hooks"]
    assert stop_hooks[0]["command"] == 'node "${ANTIGRAVITY_PLUGIN_ROOT:-$CODEX_PLUGIN_ROOT}/hooks/antigravity-review-gate.mjs"'
    assert stop_hooks[0]["timeout"] == 900


FAKE_AGY_HELP = (
    "Usage of agy:\n"
    "  --add-dir\n"
    "  --model\n"
    "  --print\n"
    "  --print-timeout\n"
    "  --prompt\n"
    "  --sandbox\n"
    "Available subcommands:\n"
    "  models\n"
    "  plugin\n"
)

FAKE_AGY_MODELS = "\n".join([
    "Gemini 3.1 Pro (High)",
    "Gemini 3.5 Flash (High)",
    "Claude Sonnet 4.6 (Thinking)",
    "Claude Opus 4.6 (Thinking)",
    "GPT-OSS 120B (Medium)",
]) + "\n"


def write_fake_agy(
    script,
    response="AGY_OK",
    capture_argv=None,
    help_text=FAKE_AGY_HELP,
    models_text=FAKE_AGY_MODELS,
    exit_code=0,
    delay_ms=0,
    ignore_sigterm=False,
    never_exit=False,
):
    capture = (
        f"fs.writeFileSync({json.dumps(str(capture_argv))}, JSON.stringify({{argv: process.argv.slice(2), cwd: process.cwd()}}));\n"
        if capture_argv else ""
    )
    sigterm_handler = 'process.on("SIGTERM", () => {});\n' if ignore_sigterm else ""
    keep_alive = "setInterval(() => {}, 1000);\n" if never_exit else ""
    completion = (
        ""
        if never_exit
        else f"setTimeout(() => {{ fs.writeSync(1, {json.dumps(response)}); process.exit({int(exit_code)}); }}, {int(delay_ms)});\n"
    )
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('fs');\n"
        "const argv = process.argv.slice(2);\n"
        "if (argv.join(' ') === '--version') { console.log('1.0.6-fake'); process.exit(0); }\n"
        f"if (argv.join(' ') === '--help') {{ process.stdout.write({json.dumps(help_text)}); process.exit(0); }}\n"
        f"if (argv.join(' ') === 'models') {{ process.stdout.write({json.dumps(models_text)}); process.exit(0); }}\n"
        f"{capture}"
        "if (!argv.includes('--prompt')) { console.error('missing prompt'); process.exit(9); }\n"
        "if (!argv.includes('--model')) { console.error('missing model'); process.exit(8); }\n"
        "if (argv.includes('--dangerously-skip-permissions')) { console.error('unsafe permissions'); process.exit(7); }\n"
        f"{sigterm_handler}"
        f"{keep_alive}"
        f"{completion}",
        encoding="utf8",
    )
    script.chmod(0o755)
    return script


def fake_agy(tmp_path, **kwargs):
    return write_fake_agy(tmp_path / "agy", **kwargs)


def run_node_eval(source, env=None):
    return subprocess.run(
        [NODE, "--input-type=module", "-e", source],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def companion_env(tmp_path, agy=None):
    env = os.environ.copy()
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    if agy is not None:
        env["AGY_CLI_PATH"] = str(agy)
    return env


def run_companion(command, cwd, env):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    return subprocess.run([NODE, str(runtime), *command], cwd=cwd, env=env, capture_output=True, text=True)


def wait_for_job(repo, env, job_id, terminal=True, timeout=5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        result = run_companion(["status", job_id], repo, env)
        assert result.returncode == 0, result.stderr
        last = json.loads(result.stdout)
        if terminal and last["status"] in {"succeeded", "failed", "cancelled", "cancel_failed"}:
            return last
        if not terminal and last["status"] == "running":
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach expected state: {last}")


def test_runtime_defaults_to_gemini_model(tmp_path):
    agy = fake_agy(tmp_path)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "process.stdout.write(JSON.stringify(r.antigravityPreflight(process.env)));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["modelProvider"] == "gemini"
    assert payload["model"] == "Gemini 3.1 Pro (High)"


def test_runtime_prefers_agy_cli_path_over_antigravity_cli_path(tmp_path):
    preferred = fake_agy(tmp_path / "preferred")
    fallback = fake_agy(tmp_path / "fallback")
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(preferred)
    env["ANTIGRAVITY_CLI_PATH"] = str(fallback)
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "process.stdout.write(r.agyCommand(process.env));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(preferred)


def test_runtime_allows_explicit_claude_provider(tmp_path):
    agy = fake_agy(tmp_path)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "process.stdout.write(JSON.stringify(r.antigravityPreflight(process.env)));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["modelProvider"] == "claude"
    assert payload["model"] == "Claude Sonnet 4.6 (Thinking)"


def test_runtime_rejects_bare_anthropic_as_claude_model(tmp_path):
    agy = fake_agy(tmp_path)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"
    env["ANTIGRAVITY_FOR_CODEX_MODEL"] = "Anthropic"
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "process.stdout.write(JSON.stringify(r.antigravityPreflight(process.env)));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "requires a Claude/Sonnet/Opus model" in payload["error"]


def test_runtime_rejects_cross_provider_model(tmp_path):
    agy = fake_agy(tmp_path)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "gemini"
    env["ANTIGRAVITY_FOR_CODEX_MODEL"] = "Claude Sonnet 4.6 (Thinking)"
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "process.stdout.write(JSON.stringify(r.antigravityPreflight(process.env)));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "requires a Gemini model" in payload["error"]


def test_runtime_rejects_gpt_model_for_any_provider(tmp_path):
    agy = fake_agy(tmp_path)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"
    env["ANTIGRAVITY_FOR_CODEX_MODEL"] = "GPT-OSS 120B (Medium)"
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "process.stdout.write(JSON.stringify(r.antigravityPreflight(process.env)));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "does not support GPT/OpenAI models" in payload["error"]


def test_runtime_requires_print_timeout_support(tmp_path):
    help_without_print_timeout = FAKE_AGY_HELP.replace("  --print-timeout\n", "")
    agy = fake_agy(tmp_path, help_text=help_without_print_timeout)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "process.stdout.write(JSON.stringify(r.antigravityPreflight(process.env)));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "--print-timeout" in payload["missing"]


def test_runtime_async_timeout_kills_agy_that_ignores_sigterm(tmp_path):
    agy = fake_agy(tmp_path, ignore_sigterm=True, never_exit=True)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "const started = Date.now();"
        "const result = await r.antigravityPrintAsync('timeout check', "
        "{timeout: 100, timeoutKillGraceMs: 50, timeoutForceResolveGraceMs: 50}, process.env);"
        "result.elapsedMs = Date.now() - started;"
        "process.stdout.write(JSON.stringify(result));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["timedOut"] is True
    assert payload["error"] == "ETIMEDOUT"
    assert payload["errorCode"] == "ETIMEDOUT"
    assert payload["elapsedMs"] < 1500


def init_git_repo(repo):
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)


def test_structured_review_outputs_valid_json_and_report(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    response = json.dumps({
        "verdict": "approve",
        "summary": "ok",
        "findings": [],
        "next_steps": ["ship"]
    })
    argv_file = tmp_path / "agy-argv.json"
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response=response, capture_argv=argv_file))
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")

    result = subprocess.run([NODE, str(runtime), "review", "--structured", "--json"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "approve"
    report = subprocess.run([NODE, str(runtime), "report", "--latest"], cwd=repo, env=env, capture_output=True, text=True)
    assert report.returncode == 0, report.stderr
    report_payload = json.loads(report.stdout)
    assert report_payload["command"] == "review"
    assert report_payload["status"] == 0
    assert "rawOutput" not in report_payload

    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert "Return only one JSON object" in prompt
    assert '"verdict"' in prompt
    assert "approve" in prompt
    assert "Git context:" in prompt
    assert "Do not edit files" in prompt


def test_review_json_implies_structured_output(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    response = json.dumps({"verdict": "approve", "summary": "ok", "findings": [], "next_steps": []})
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response=response))
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    result = subprocess.run([NODE, str(runtime), "review", "--json"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verdict"] == "approve"


def test_structured_review_invalid_json_fails_without_stack_trace(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="not json"))
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    result = subprocess.run([NODE, str(runtime), "review", "--structured"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Structured review output invalid" in result.stderr
    assert "Error:" not in result.stderr


def test_structured_review_rejects_top_level_extra_fields(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    response = json.dumps({
        "verdict": "approve",
        "summary": "ok",
        "findings": [],
        "next_steps": [],
        "rawOutput": "not allowed"
    })
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response=response))
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    result = subprocess.run([NODE, str(runtime), "review", "--json"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Structured review output invalid" in result.stderr
    assert "unsupported key" in result.stderr


def test_structured_review_rejects_finding_extra_fields(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    response = json.dumps({
        "verdict": "needs-attention",
        "summary": "issue",
        "findings": [{
            "severity": "low",
            "title": "Extra field",
            "body": "The finding includes a schema-incompatible property.",
            "file": "file.txt",
            "line_start": 1,
            "line_end": 1,
            "confidence": 0.8,
            "recommendation": "Remove it.",
            "snippet": "not allowed"
        }],
        "next_steps": ["remove extra field"]
    })
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response=response))
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    result = subprocess.run([NODE, str(runtime), "review", "--json"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Structured review output invalid" in result.stderr
    assert "unsupported key" in result.stderr


def test_structured_review_rejects_reversed_line_ranges(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    response = json.dumps({
        "verdict": "needs-attention",
        "summary": "issue",
        "findings": [{
            "severity": "low",
            "title": "Bad range",
            "body": "The range is reversed.",
            "file": "file.txt",
            "line_start": 10,
            "line_end": 9,
            "confidence": 0.8,
            "recommendation": "Fix the range."
        }],
        "next_steps": ["fix range"]
    })
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response=response))
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    result = subprocess.run([NODE, str(runtime), "review", "--json"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Structured review output invalid" in result.stderr
    assert "line_end must be >= line_start" in result.stderr


def test_review_invokes_agy_with_prompt_model_and_no_dangerous_permissions(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "sample.txt").write_text("before\n", encoding="utf8")
    subprocess.run(["git", "add", "sample.txt"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "sample.txt").write_text("after\n", encoding="utf8")
    agy = fake_agy(tmp_path, response="AGY_REVIEW_OK", capture_argv=argv_file)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)

    result = subprocess.run([NODE, str(runtime), "review", "focus"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "AGY_REVIEW_OK" in result.stdout
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    assert "--print" not in argv
    assert "--prompt" in argv
    prompt = argv[argv.index("--prompt") + 1]
    assert "sample.txt" in prompt
    assert "after" in prompt
    assert "<model_provider>" not in prompt
    assert "Model provider: gemini." in prompt
    assert "--model" in argv
    assert argv[argv.index("--model") + 1].startswith("Gemini ")
    assert "--dangerously-skip-permissions" not in argv


def test_review_includes_untracked_text_file_excerpt(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    unique = "UNTRACKED_UNIQUE_CONTEXT_7f6b3cf2"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "new-note.txt").write_text(f"{unique}\n", encoding="utf8")
    agy = fake_agy(tmp_path, response="AGY_REVIEW_OK", capture_argv=argv_file)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)

    result = subprocess.run([NODE, str(runtime), "review", "focus"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert "Untracked file: new-note.txt" in prompt
    assert unique in prompt


def test_github_actions_review_skill_focus_invokes_agy(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    repo.mkdir()
    init_git_repo(repo)
    workflow = repo / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: CI\non: [pull_request]\n", encoding="utf8")
    agy = fake_agy(tmp_path, response="AGY_GHA_OK", capture_argv=argv_file)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)

    result = subprocess.run([
        NODE,
        str(runtime),
        "review",
        "GitHub Actions workflow safety, fork PR behavior, secret exposure, permissions, and immutable plugin refs."
    ], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert "GitHub Actions workflow safety" in prompt
    assert ".github/workflows/ci.yml" in prompt


def test_review_from_subdirectory_includes_root_relative_untracked_excerpt(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    subdir = repo / "subdir"
    argv_file = tmp_path / "agy-argv.json"
    unique = "SUBDIR_UNTRACKED_UNIQUE_CONTEXT_9bb1c9"
    subdir.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)
    (subdir / "new.txt").write_text(f"{unique}\n", encoding="utf8")
    agy = fake_agy(tmp_path, response="AGY_REVIEW_OK", capture_argv=argv_file)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)

    result = subprocess.run([NODE, str(runtime), "review", "focus"], cwd=subdir, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert "Untracked file: subdir/new.txt" in prompt
    assert unique in prompt


def test_review_marks_large_git_diff_as_truncated(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "large.txt").write_text("before\n", encoding="utf8")
    subprocess.run(["git", "add", "large.txt"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "large.txt").write_text("x" * (3 * 1024 * 1024), encoding="utf8")
    agy = fake_agy(tmp_path, response="AGY_REVIEW_OK", capture_argv=argv_file)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)

    result = subprocess.run([NODE, str(runtime), "review", "focus"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert "[git output truncated or timed out for: git diff -- .]" in prompt


def test_review_can_use_explicit_claude_provider(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    repo.mkdir()
    init_git_repo(repo)
    agy = fake_agy(tmp_path, response="CLAUDE_AGY_REVIEW_OK", capture_argv=argv_file)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)

    result = subprocess.run([NODE, str(runtime), "review", "--model-provider", "claude", "focus"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    assert argv[argv.index("--model") + 1].startswith("Claude ")


def test_invalid_model_provider_exits_2(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path))

    result = subprocess.run([NODE, str(runtime), "review", "--model-provider", "openai", "focus"], env=env, capture_output=True, text=True)

    assert result.returncode == 2
    assert "Invalid --model-provider" in result.stderr


def test_background_job_lifecycle_with_fake_agy(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="BACKGROUND_OK", delay_ms=100))

    queued = run_companion(["review", "--background", "focus"], repo, env)

    assert queued.returncode == 0, queued.stderr
    payload = json.loads(queued.stdout)
    assert payload["status"] == "queued"
    job_id = payload["jobId"]

    listing = run_companion(["jobs"], repo, env)
    assert listing.returncode == 0, listing.stderr
    assert job_id in [job["id"] for job in json.loads(listing.stdout)["jobs"]]

    final_status = wait_for_job(repo, env, job_id)
    assert final_status["status"] == "succeeded"

    result = run_companion(["result", job_id], repo, env)
    assert result.returncode == 0, result.stderr
    result_payload = json.loads(result.stdout)
    assert result_payload["id"] == job_id
    assert result_payload["status"] == "succeeded"
    assert result_payload["stdout"] == "BACKGROUND_OK"

    viewed = run_companion(["status", job_id], repo, env)
    assert json.loads(viewed.stdout)["viewed"] is True


def test_internal_run_job_rejects_external_invocation(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = companion_env(tmp_path, fake_agy(tmp_path))

    result = run_companion(["__run-job", "job-external"], repo, env)

    assert result.returncode == 2
    assert "internal dispatch" in result.stderr


def test_job_result_output_is_capped(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    huge_output = "A" * (300 * 1024)
    env = companion_env(tmp_path, fake_agy(tmp_path, response=huge_output))

    queued = run_companion(["review", "--background", "large output"], repo, env)
    job_id = json.loads(queued.stdout)["jobId"]
    wait_for_job(repo, env, job_id)
    result = run_companion(["result", job_id], repo, env)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload["stdout"].encode("utf8")) < 270 * 1024
    assert "truncated" in payload["stdout"].lower()


def test_internal_worker_command_is_not_user_facing(tmp_path):
    env = companion_env(tmp_path, fake_agy(tmp_path))

    capabilities = run_companion(["capabilities"], tmp_path, env)

    assert capabilities.returncode == 0, capabilities.stderr
    payload = json.loads(capabilities.stdout)
    assert "__run-job" not in payload["commands"]
    assert "jobs" in payload["commands"]
    assert "result" in payload["commands"]
    assert "cancel" in payload["commands"]


def test_reserved_job_lifecycle_is_explicit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="RESERVED_OK", delay_ms=100))

    reserved = run_companion(["reserve-job", "review", "reserved focus"], repo, env)

    assert reserved.returncode == 0, reserved.stderr
    reserved_payload = json.loads(reserved.stdout)
    assert reserved_payload["status"] == "reserved"
    job_id = reserved_payload["jobId"]
    status = run_companion(["status", job_id], repo, env)
    assert json.loads(status.stdout)["status"] == "reserved"

    rejected = run_companion(["reserve-job", "review-gate"], repo, env)
    assert rejected.returncode == 2
    assert "cannot be reserved" in rejected.stderr

    started = run_companion(["run-reserved-job", job_id], repo, env)
    assert started.returncode == 0, started.stderr
    assert json.loads(started.stdout)["status"] == "queued"
    final_status = wait_for_job(repo, env, job_id)
    assert final_status["status"] == "succeeded"
    result = run_companion(["result", job_id], repo, env)
    assert json.loads(result.stdout)["stdout"] == "RESERVED_OK"


def test_job_cancellation_uses_process_group_and_preserves_finished_jobs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    env = companion_env(tmp_path, fake_agy(tmp_path, never_exit=True))

    queued = run_companion(["review", "--background", "cancel me"], repo, env)
    job_id = json.loads(queued.stdout)["jobId"]
    running = wait_for_job(repo, env, job_id, terminal=False)
    assert running["worker"]["pid"] > 0

    cancel = run_companion(["cancel", job_id], repo, env)
    assert cancel.returncode == 0, cancel.stderr
    assert json.loads(cancel.stdout)["status"] == "cancelled"

    finished_env = companion_env(tmp_path / "finished", fake_agy(tmp_path / "finished", response="DONE"))
    finished = run_companion(["review", "--background", "finish me"], repo, finished_env)
    finished_id = json.loads(finished.stdout)["jobId"]
    wait_for_job(repo, finished_env, finished_id)
    cancel_finished = subprocess.run([NODE, str(runtime), "cancel", finished_id], cwd=repo, env=finished_env, capture_output=True, text=True)
    assert cancel_finished.returncode == 0, cancel_finished.stderr
    assert json.loads(cancel_finished.stdout)["status"] == "succeeded"


def test_finish_job_preserves_all_terminal_statuses(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    source = (
        "const jobs = await import('./plugins/antigravity-for-codex/scripts/lib/jobs.mjs');"
        f"const cwd = {json.dumps(str(repo))};"
        f"const env = {{ ANTIGRAVITY_FOR_CODEX_STATE_HOME: {json.dumps(str(state))} }};"
        "const succeeded = jobs.createJob({command: 'review', cwd}, env);"
        "jobs.finishJob(succeeded.id, {status: 0, stdout: 'initial success'}, cwd, env);"
        "const preservedSucceeded = jobs.finishJob(succeeded.id, {status: 1, stdout: 'overwritten'}, cwd, env);"
        "const failed = jobs.createJob({command: 'review', cwd}, env);"
        "jobs.finishJob(failed.id, {status: 1, stdout: 'initial failure'}, cwd, env);"
        "const preservedFailed = jobs.finishJob(failed.id, {status: 0, stdout: 'overwritten'}, cwd, env);"
        "process.stdout.write(JSON.stringify({preservedSucceeded, preservedFailed}));"
    )

    result = run_node_eval(source)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["preservedSucceeded"]["status"] == "succeeded"
    assert payload["preservedSucceeded"]["stdout"] == "initial success"
    assert payload["preservedFailed"]["status"] == "failed"
    assert payload["preservedFailed"]["stdout"] == "initial failure"


def test_process_identity_validation_checks_pid_ppid_command_before_group_kill():
    text = (PLUGIN / "scripts" / "lib" / "process.mjs").read_text(encoding="utf8")

    assert "current.pid !== expectedIdentity.pid" in text
    assert "current.ppid !== expectedIdentity.ppid" in text
    assert "current.command !== expectedIdentity.command" in text
    assert 'current.command.includes("antigravity-companion.mjs")' in text
    assert 'process.kill(-numericPid, "SIGTERM")' in text


def test_setup_does_not_emit_command_inventory(tmp_path):
    env = companion_env(tmp_path, fake_agy(tmp_path))

    setup = run_companion(["setup"], tmp_path, env)

    assert setup.returncode == 0, setup.stderr
    payload = json.loads(setup.stdout)
    assert "commands" not in payload


def test_setup_exits_zero_when_agy_is_unavailable():
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    env = os.environ.copy()
    env["PATH"] = ""
    env["ANTIGRAVITY_FOR_CODEX_TEST_DISABLE_CANDIDATE_DISCOVERY"] = "1"
    env.pop("AGY_CLI_PATH", None)
    env.pop("ANTIGRAVITY_CLI_PATH", None)

    result = subprocess.run([NODE, str(runtime), "setup"], env=env, capture_output=True, text=True)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False


def test_multi_review_help_does_not_call_agy(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    argv_file = tmp_path / "agy-argv.json"
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, capture_argv=argv_file))

    result = subprocess.run([NODE, str(runtime), "multi-review", "--help"], env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert "Usage: antigravity-companion.mjs multi-review" in result.stdout
    assert not argv_file.exists()


def test_role_packs_lists_builtin_packs():
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    result = subprocess.run([NODE, str(runtime), "roles", "--json"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "default" in payload["packs"]
    assert payload["packs"]["release"]["roles"] == ["release", "tests", "correctness", "security"]


def test_multi_review_uses_role_pack(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    argv_file = tmp_path / "agy-argv.json"
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="PACK_OK", capture_argv=argv_file))
    result = subprocess.run([NODE, str(runtime), "multi-review", "--role-pack", "security"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "## security" in result.stdout
    assert "## correctness" in result.stdout
    assert "## adversarial" in result.stdout


def test_review_gate_blocks_only_on_explicit_block(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    env = os.environ.copy()
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"

    block_agy = fake_agy(tmp_path / "block", response="BLOCK: stop here\nEvidence")
    env["AGY_CLI_PATH"] = str(block_agy)
    block = subprocess.run([NODE, str(runtime), "review-gate"], env=env, capture_output=True, text=True)
    assert block.returncode == 0
    assert json.loads(block.stdout) == {"decision": "block", "reason": "stop here"}
    assert block.stderr == ""

    allow_agy = fake_agy(tmp_path / "allow", response="ALLOW: ok")
    env["AGY_CLI_PATH"] = str(allow_agy)
    allow = subprocess.run([NODE, str(runtime), "review-gate"], env=env, capture_output=True, text=True)
    assert allow.returncode == 0
    assert allow.stdout == ""
    assert allow.stderr == ""

    embedded_block_agy = fake_agy(tmp_path / "embedded", response="Notes first\nBLOCK: not first")
    env["AGY_CLI_PATH"] = str(embedded_block_agy)
    embedded = subprocess.run([NODE, str(runtime), "review-gate"], env=env, capture_output=True, text=True)
    assert embedded.returncode == 0
    assert embedded.stdout == ""
    assert "invalid output; allowing stop" in embedded.stderr


def test_review_gate_fail_open_on_invalid_output(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    env = os.environ.copy()
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="not a gate verdict"))

    result = subprocess.run([NODE, str(runtime), "review-gate"], env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert result.stdout == ""
    assert "[antigravity-for-codex review-gate] invalid output; allowing stop" in result.stderr


def test_review_gate_uses_inner_timeout_below_hook_timeout(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    argv_file = tmp_path / "agy-argv.json"
    env = os.environ.copy()
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="ALLOW: ok", capture_argv=argv_file))

    result = subprocess.run([NODE, str(runtime), "review-gate"], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    assert argv[argv.index("--print-timeout") + 1] == "840s"


def test_release_check_passes():
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=PLUGIN, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "PASS manifest-name" in result.stdout
    assert "PASS agy-prompt-timeout-argv" in result.stdout
    assert "PASS no-print-argv" in result.stdout
    assert "PASS github-actions-template" in result.stdout
    assert "PASS github-actions-release-ref" in result.stdout
    assert "PASS skill-antigravity-role-packs" in result.stdout
    assert "PASS skill-antigravity-status" in result.stdout
    assert "PASS skill-antigravity-result" in result.stdout
    assert "PASS skill-antigravity-cancel" in result.stdout
    assert "PASS workflow-plugin-install" in result.stdout


def test_real_smoke_is_opt_in(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="ANTIGRAVITY_FOR_CODEX_SMOKE_OK"))
    env.pop("ANTIGRAVITY_FOR_CODEX_REAL_SMOKE", None)

    result = subprocess.run([NODE, str(runtime), "real-smoke", "--quick"], env=env, capture_output=True, text=True)

    assert result.returncode == 2
    assert "real-smoke is opt-in" in result.stderr


def test_real_smoke_runs_fake_agy_for_gemini_and_claude(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    argv_file = tmp_path / "agy-argv.json"
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="ANTIGRAVITY_FOR_CODEX_SMOKE_OK", capture_argv=argv_file))
    env["ANTIGRAVITY_FOR_CODEX_REAL_SMOKE"] = "1"

    result = subprocess.run([NODE, str(runtime), "real-smoke", "--quick", "--timeout-seconds", "5"], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "PASS real-smoke gemini" in result.stdout
    assert "PASS real-smoke claude" in result.stdout
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    assert "--prompt" in argv
    assert "--print" not in argv
    assert "--print-timeout" in argv
    assert "--dangerously-skip-permissions" not in argv
