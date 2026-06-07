import json
import os
import pathlib
import shutil
import subprocess


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
    }
    actual_libs = {path.name for path in (PLUGIN / "scripts" / "lib").glob("*.mjs")}
    assert actual_libs == expected_libs
    assert not (PLUGIN / "prompts").exists()
    assert not (PLUGIN / "schemas").exists()


def test_antigravity_skills_exist_and_use_antigravity_commands():
    expected = [
        "antigravity-review",
        "antigravity-adversarial-review",
        "antigravity-multi-review",
        "antigravity-plan",
        "antigravity-rescue",
        "antigravity-review-gate",
        "antigravity-github-actions-review",
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
        "antigravity-status",
        "antigravity-result",
        "antigravity-cancel",
        "antigravity-role-packs",
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
        else f"setTimeout(() => {{ process.stdout.write({json.dumps(response)}); process.exit({int(exit_code)}); }}, {int(delay_ms)});\n"
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
