import json
import os
import pathlib
import shutil
import subprocess
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "antigravity-for-codex"
NODE = os.environ.get("NODE_BINARY") or shutil.which("node")
if not NODE:
    raise RuntimeError("node not found; set NODE_BINARY or put node on PATH")


def test_antigravity_manifest_is_valid_json():
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf8"))
    assert manifest["name"] == "antigravity-for-codex"
    assert manifest["version"] == "0.5.4"
    assert manifest["skills"] == "./skills/"
    assert "antigravity" in manifest["keywords"]
    assert "gemini" in manifest["keywords"]
    assert "claude" in manifest["keywords"]
    assert manifest["interface"]["capabilities"] == [
        "Read-only Antigravity CLI review",
        "Explicit Gemini or Claude model selection",
        "Adversarial review",
        "Implementation planning",
        "Read-only rescue diagnosis",
        "Multi-role review orchestration",
        "Structured review output and sanitized reports",
        "Background jobs, status, result, and cancel",
        "Role packs for focused review teams",
        "Advisory mailbox and leases",
        "Session lifecycle and unread-result hooks",
        "GitHub Actions workflow rendering and validation",
        "Release checks and opt-in real smoke",
        "Opt-in Stop hook gate",
        "Codex-Antigravity collaboration",
    ]
    text = json.dumps(manifest)
    assert "Gemini CLI" not in text
    assert "Claude Code CLI" not in text
    assert "Claude Code SDK" not in text
    assert "Gemini native-agent" not in text
    assert "ultrareview" not in text


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
        "leases.mjs",
        "mailbox.mjs",
        "prompt-template.mjs",
        "process.mjs",
        "render-review.mjs",
        "reports.mjs",
        "role-packs.mjs",
        "state.mjs",
        "structured-output.mjs",
        "version.mjs",
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
    contract = json.loads((PLUGIN / "contracts" / "natural-language-routing.json").read_text(encoding="utf8"))
    routed_model_skills = set(contract["routedModelSkills"])
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
        "antigravity-collaboration-loop",
        "antigravity-mailbox",
        "antigravity-leases",
    ]
    for skill in expected:
        path = PLUGIN / "skills" / skill / "SKILL.md"
        assert path.exists(), f"missing {path}"
        text = path.read_text(encoding="utf8")
        assert f"name: {skill}" in text
        assert "antigravity-companion.mjs" in text
        assert "gemini-companion.mjs" not in text
        assert "claude-companion.mjs" not in text
        if skill in routed_model_skills:
            routing_start = text.find("## Natural-Language Model Routing")
            assert routing_start >= 0, f"routing section missing from {skill}"
            run_start = text.find("\nRun:\n")
            assert 0 <= run_start < routing_start, f"primary Run block missing before routing section in {skill}"


def test_antigravity_skills_encode_natural_language_model_routing():
    contract = json.loads((PLUGIN / "contracts" / "natural-language-routing.json").read_text(encoding="utf8"))

    def markdown_section(text, start_marker, end_marker, label):
        routing_start = text.find("## Natural-Language Model Routing")
        assert routing_start >= 0, f"routing section missing from {label}"
        start = text.find(start_marker, routing_start)
        assert start >= 0, f"{start_marker!r} missing from {label}"
        body_start = start + len(start_marker)
        end = text.find(end_marker, body_start)
        assert end >= 0, f"{end_marker!r} missing after {start_marker!r} in {label}"
        return text[body_start:end]

    for skill in contract["routedModelSkills"]:
        text = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf8")
        for anchor in contract["requiredAnchors"]:
            assert anchor in text, f"{anchor!r} missing from {skill}"
        for phrase in contract["requiredPolicyPhrases"]:
            assert phrase in text, f"{phrase!r} missing from {skill}"
        for marker in contract["requiredMarkers"]:
            assert marker in text, f"{marker!r} missing from {skill}"
        for marker in contract["skillMarkers"].get(skill, []):
            assert marker in text, f"{marker!r} missing from {skill}"
        user_examples = markdown_section(text, contract["userExamplesStart"], contract["userExamplesEnd"], skill)
        for forbidden in contract["forbiddenUserExampleSubstrings"]:
            assert forbidden not in user_examples

    for relative in contract["githubActionsInitForbiddenPaths"]:
        text = (ROOT / relative).read_text(encoding="utf8")
        for forbidden in contract["githubActionsInitForbiddenSubstrings"]:
            assert forbidden not in text, f"{forbidden!r} leaked into {relative}"

    readme = (PLUGIN / "README.md").read_text(encoding="utf8")
    docs_en = (ROOT / "docs" / "README.en.md").read_text(encoding="utf8")
    docs_zh = (ROOT / "docs" / "README.zh-CN.md").read_text(encoding="utf8")
    assert "Users can ask for Antigravity in natural language" in readme
    assert "users do not need to write `--model-provider` or `--model`" in readme
    assert "Treat \"strict\", \"deep\", \"advanced\", \"high-confidence\", and \"multi-agent\" as review strength" in readme
    assert "`github-actions init` persists the selected provider into the generated workflow" in readme
    assert "Provider-specific model defaults remain runtime-owned unless `ANTIGRAVITY_FOR_CODEX_MODEL` is explicitly set" in readme
    assert "Natural-language routing rule: users should ask for Antigravity normally" in docs_en
    assert "Gemini is the default provider; Claude-through-Antigravity is used only when" in docs_en
    assert "Workflow generation note: `github-actions init` persists the selected provider" in docs_en
    assert "自然语言路由规则\uFF1A用户只需要正常表达" in docs_zh
    assert "默认使用 Gemini provider\uFF1B只有用户明确要求" in docs_zh
    assert "工作流生成说明\uFF1A`github-actions init` 会把所选 provider 持久化写入生成的 workflow" in docs_zh


def test_antigravity_hooks_use_antigravity_env_names():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf8"))
    text = json.dumps(hooks)
    assert "ANTIGRAVITY_PLUGIN_ROOT" in text
    assert "GEMINI_PLUGIN_ROOT" not in text
    assert "CLAUDE_PLUGIN_ROOT" not in text
    stop_hooks = hooks["hooks"]["Stop"][0]["hooks"]
    assert stop_hooks[0]["command"] == 'node "${ANTIGRAVITY_PLUGIN_ROOT:-$CODEX_PLUGIN_ROOT}/hooks/antigravity-review-gate.mjs"'
    assert stop_hooks[0]["timeout"] == 900
    assert set(hooks["hooks"]) == {"Stop", "SessionStart", "SessionEnd", "UserPromptSubmit"}
    assert "session-lifecycle.mjs" in hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "session-lifecycle.mjs" in hooks["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
    assert "unread-result.mjs" in hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert "antigravity-review-gate.mjs" in stop_hooks[0]["command"]


FAKE_AGY_HELP = (
    "Usage of agy:\n"
    "  --add-dir\n"
    "  --log-file\n"
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
    capture_pid=None,
    help_text=FAKE_AGY_HELP,
    models_text=FAKE_AGY_MODELS,
    exit_code=0,
    delay_ms=0,
    ignore_sigterm=False,
    never_exit=False,
    smart_smoke=False,
):
    capture = (
        f"const capturePath = {json.dumps(str(capture_argv))};\n"
        "let previous = null;\n"
        "try { previous = JSON.parse(fs.readFileSync(capturePath, 'utf8')); } catch {}\n"
        "let captures = Array.isArray(previous?.calls) ? previous.calls : (previous?.argv ? [previous] : []);\n"
        "captures.push({argv: process.argv.slice(2), cwd: process.cwd()});\n"
        "fs.writeFileSync(capturePath, JSON.stringify({...captures[captures.length - 1], calls: captures}));\n"
        if capture_argv else ""
    )
    pid_capture = (
        f"fs.writeFileSync({json.dumps(str(capture_pid))}, String(process.pid));\n"
        if capture_pid else ""
    )
    sigterm_handler = 'process.on("SIGTERM", () => {});\n' if ignore_sigterm else ""
    keep_alive = "setInterval(() => {}, 1000);\n" if never_exit else ""
    if never_exit:
        completion = ""
    elif smart_smoke:
        completion = (
            "const prompt = argv[argv.indexOf('--prompt') + 1] || '';\n"
            "let output = 'ANTIGRAVITY_FOR_CODEX_SMOKE_OK';\n"
            "if (prompt.includes('ALLOW:') || prompt.includes('stop-gate')) output = 'ALLOW: ANTIGRAVITY_FOR_CODEX_SMOKE_OK';\n"
            "else if (prompt.includes('structured review') || prompt.includes('JSON')) output = '{\"verdict\":\"approve\",\"summary\":\"ANTIGRAVITY_FOR_CODEX_SMOKE_OK\",\"findings\":[],\"next_steps\":[]}';\n"
            f"setTimeout(() => {{ fs.writeSync(1, output); process.exit({int(exit_code)}); }}, {int(delay_ms)});\n"
        )
    else:
        completion = f"setTimeout(() => {{ fs.writeSync(1, {json.dumps(response)}); process.exit({int(exit_code)}); }}, {int(delay_ms)});\n"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('fs');\n"
        "const argv = process.argv.slice(2);\n"
        "if (argv.join(' ') === '--version') { console.log('1.0.6-fake'); process.exit(0); }\n"
        f"if (argv.join(' ') === '--help') {{ process.stdout.write({json.dumps(help_text)}); process.exit(0); }}\n"
        f"if (argv.join(' ') === 'models') {{ process.stdout.write({json.dumps(models_text)}); process.exit(0); }}\n"
        f"{capture}"
        f"{pid_capture}"
        "const logFileIndex = argv.indexOf('--log-file');\n"
        "if (logFileIndex >= 0 && process.env.FAKE_AGY_LOG) { fs.writeFileSync(argv[logFileIndex + 1], process.env.FAKE_AGY_LOG); }\n"
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


def process_is_running(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "pid="], capture_output=True, text=True)
    return result.returncode == 0 and str(pid) in result.stdout


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


def test_runtime_log_diagnostic_collapses_duplicate_resource_errors():
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "const log = 'E0611 agent executor error: RESOURCE_EXHAUSTED (code 429): Individual quota reached. "
        "Resets in 38h51m45s.: RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 38h51m45s.\\n';"
        "process.stdout.write(r.antigravityLogDiagnostic(log));"
    )
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 38h51m45s"
    assert result.stdout.count("RESOURCE_EXHAUSTED") == 1


def test_runtime_empty_output_surfaces_agy_log_diagnostic(tmp_path):
    argv_file = tmp_path / "agy-argv.json"
    agy = fake_agy(tmp_path, response="", capture_argv=argv_file)
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)
    env["FAKE_AGY_LOG"] = (
        "I0611 print mode started\n"
        "E0611 agent executor error: RESOURCE_EXHAUSTED (code 429): Individual quota reached. "
        "Resets in 38h51m45s.\n"
    )
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "const result = r.antigravityPrint('empty check', {}, process.env);"
        "process.stdout.write(JSON.stringify(result));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == 1
    assert payload["errorCode"] == "EEMPTYOUTPUT"
    assert "RESOURCE_EXHAUSTED (code 429)" in payload["stderr"]
    assert "Individual quota reached" in payload["stderr"]
    capture = json.loads(argv_file.read_text())
    assert "--log-file" in capture["argv"]


def test_runtime_async_empty_output_surfaces_agy_log_diagnostic(tmp_path):
    agy = fake_agy(tmp_path, response="")
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)
    env["FAKE_AGY_LOG"] = (
        "E0611 agent executor error: RESOURCE_EXHAUSTED (code 429): Individual quota reached. "
        "Resets in 38h51m45s.\n"
    )
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "const result = await r.antigravityPrintAsync('empty check', {timeout: 1000}, process.env);"
        "process.stdout.write(JSON.stringify(result));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == 1
    assert payload["errorCode"] == "EEMPTYOUTPUT"
    assert "RESOURCE_EXHAUSTED (code 429)" in payload["stderr"]


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


def test_structured_review_rejects_approve_with_findings_and_parses_balanced_json(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    payload = {
        "verdict": "approve",
        "summary": "summary",
        "findings": [{
            "severity": "low",
            "title": "Finding",
            "body": "Approve must not carry findings.",
            "file": "file.txt",
            "line_start": 1,
            "line_end": 1,
            "confidence": 0.8,
            "recommendation": ""
        }],
        "next_steps": ["ship"]
    }
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response=f"prefix {{not json}}\n{json.dumps(payload)}\ntrailing {{note}}"))
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    result = subprocess.run([NODE, str(runtime), "review", "--json"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Structured review output invalid" in result.stderr
    assert "approve requires an empty findings array" in result.stderr


def test_structured_review_prefers_schema_like_json_candidate(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    payload = {
        "verdict": "approve",
        "summary": "ok",
        "findings": [],
        "next_steps": ["ship"]
    }
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response=f"Example object: {{}}\n{json.dumps(payload)}"))
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")

    result = subprocess.run([NODE, str(runtime), "review", "--json"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["summary"] == "ok"


def test_structured_review_skips_partial_schema_like_json_candidate(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    payload = {
        "verdict": "approve",
        "summary": "ok after partial",
        "findings": [],
        "next_steps": ["ship"]
    }
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response=f'Example: {{"findings":"not review output"}}\n{json.dumps(payload)}'))
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")

    result = subprocess.run([NODE, str(runtime), "review", "--json"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["summary"] == "ok after partial"


def test_structured_review_recovers_after_unclosed_prose_brace(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    payload = {
        "verdict": "approve",
        "summary": "ok after brace",
        "findings": [],
        "next_steps": ["ship"]
    }
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response=f"Intro with unfinished brace {{\n{json.dumps(payload)}"))
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")

    result = subprocess.run([NODE, str(runtime), "review", "--json"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["summary"] == "ok after brace"


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
    env = companion_env(tmp_path, agy)

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
    env = companion_env(tmp_path, agy)

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
    env = companion_env(tmp_path, agy)

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
    env = companion_env(tmp_path, agy)

    result = subprocess.run([NODE, str(runtime), "review", "focus"], cwd=subdir, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert "Untracked file: subdir/new.txt" in prompt
    assert unique in prompt


def test_review_from_subdirectory_includes_root_tracked_diff(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    subdir = repo / "subdir"
    argv_file = tmp_path / "agy-argv.json"
    unique = "ROOT_TRACKED_UNIQUE_CONTEXT_4d4b28"
    subdir.mkdir(parents=True)
    init_git_repo(repo)
    (repo / "tracked.txt").write_text("before\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "tracked.txt").write_text(f"before\n{unique}\n", encoding="utf8")
    agy = fake_agy(tmp_path, response="AGY_REVIEW_OK", capture_argv=argv_file)
    env = companion_env(tmp_path, agy)

    result = subprocess.run([NODE, str(runtime), "review", "focus"], cwd=subdir, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert "tracked.txt" in prompt
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
    env = companion_env(tmp_path, agy)

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
    env = companion_env(tmp_path, agy)

    result = subprocess.run([NODE, str(runtime), "review", "--model-provider", "claude", "focus"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    assert argv[argv.index("--model") + 1].startswith("Claude ")


def test_review_uses_claude_provider_from_environment(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    repo.mkdir()
    init_git_repo(repo)
    agy = fake_agy(tmp_path, response="CLAUDE_AGY_REVIEW_OK", capture_argv=argv_file)
    env = companion_env(tmp_path, agy)
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"

    result = subprocess.run([NODE, str(runtime), "review", "focus with spaces"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    assert argv[argv.index("--model") + 1].startswith("Claude ")
    prompt = argv[argv.index("--prompt") + 1]
    assert "focus with spaces" in prompt


def test_invalid_model_provider_exits_2(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    env = companion_env(tmp_path, fake_agy(tmp_path))

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
    assert "github-actions" in payload["commands"]
    assert "modelCatalog" in payload


def test_preflight_warns_when_selected_model_not_listed(tmp_path):
    agy = fake_agy(tmp_path, models_text="Gemini 3.5 Flash (High)\nClaude Sonnet 4.6 (Thinking)\n")
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(agy)
    env["ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL"] = "Gemini 3.1 Pro (High)"
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "process.stdout.write(JSON.stringify(r.antigravityModelDiagnostics(process.env)));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["modelCatalog"]["available"] is True
    assert payload["modelCatalog"]["selectedModelListed"] is False
    assert payload["modelCatalog"]["count"] == 2


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


def test_reserved_job_duplicate_start_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="RESERVED_ONCE", delay_ms=300))

    reserved = run_companion(["reserve-job", "review", "reserved once"], repo, env)
    job_id = json.loads(reserved.stdout)["jobId"]
    first = run_companion(["run-reserved-job", job_id], repo, env)
    second = run_companion(["run-reserved-job", job_id], repo, env)

    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["status"] == "queued"
    assert second.returncode == 2
    assert "is not reserved" in second.stderr
    assert wait_for_job(repo, env, job_id)["status"] == "succeeded"


def test_reserved_job_concurrent_start_claims_once(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    env = companion_env(tmp_path, fake_agy(tmp_path, response="RESERVED_CONCURRENT", delay_ms=300))

    reserved = run_companion(["reserve-job", "review", "reserved concurrent"], repo, env)
    job_id = json.loads(reserved.stdout)["jobId"]

    starters = [
        subprocess.Popen(
            [NODE, str(runtime), "run-reserved-job", job_id],
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [starter.communicate(timeout=5) + (starter.returncode,) for starter in starters]

    successes = [result for result in results if result[2] == 0]
    failures = [result for result in results if result[2] != 0]
    assert len(successes) == 1, results
    assert json.loads(successes[0][0])["status"] == "queued"
    assert len(failures) == 1, results
    assert "is not reserved" in failures[0][1]
    assert wait_for_job(repo, env, job_id)["status"] == "succeeded"


def test_reserved_job_stale_lock_is_recovered(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="STALE_LOCK_RECOVERED", delay_ms=100))
    env["ANTIGRAVITY_FOR_CODEX_JOB_LOCK_STALE_MS"] = "0"

    reserved = run_companion(["reserve-job", "review", "stale lock"], repo, env)
    job_id = json.loads(reserved.stdout)["jobId"]
    job_file = next((tmp_path / "state").rglob(f"{job_id}.json"))
    lock_file = pathlib.Path(f"{job_file}.lock")
    lock_file.write_text("stale", encoding="utf8")
    old = time.time() - 60
    os.utime(lock_file, (old, old))

    started = run_companion(["run-reserved-job", job_id], repo, env)

    assert started.returncode == 0, started.stderr
    assert json.loads(started.stdout)["status"] == "queued"
    assert not lock_file.exists()
    assert wait_for_job(repo, env, job_id)["status"] == "succeeded"


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


def test_job_cancellation_confirms_sigterm_ignored_worker_exits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    agy_pid_file = tmp_path / "agy.pid"
    env = companion_env(tmp_path, fake_agy(tmp_path, capture_pid=agy_pid_file, ignore_sigterm=True, never_exit=True))

    queued = run_companion(["review", "--background", "ignore sigterm"], repo, env)
    job_id = json.loads(queued.stdout)["jobId"]
    running = wait_for_job(repo, env, job_id, terminal=False)
    worker_pid = running["worker"]["pid"]
    assert process_is_running(worker_pid)

    try:
        deadline = time.time() + 2
        while not agy_pid_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert agy_pid_file.exists()
        agy_pid = int(agy_pid_file.read_text(encoding="utf8"))
        assert process_is_running(agy_pid)

        cancel = run_companion(["cancel", job_id], repo, env)

        assert cancel.returncode == 0, cancel.stderr
        assert json.loads(cancel.stdout)["status"] == "cancelled"
        assert not process_is_running(worker_pid)
        assert not process_is_running(agy_pid)
    finally:
        if agy_pid_file.exists():
            agy_pid = int(agy_pid_file.read_text(encoding="utf8"))
            if process_is_running(agy_pid):
                subprocess.run(["kill", "-KILL", str(agy_pid)], capture_output=True, text=True)


def test_immediate_job_cancellation_does_not_leave_worker_running(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    agy_pid_file = tmp_path / "agy-immediate.pid"
    env = companion_env(tmp_path, fake_agy(tmp_path, capture_pid=agy_pid_file, never_exit=True))

    queued = run_companion(["review", "--background", "cancel immediately"], repo, env)
    job_id = json.loads(queued.stdout)["jobId"]
    cancel = run_companion(["cancel", job_id], repo, env)

    assert cancel.returncode == 0, cancel.stderr
    payload = json.loads(cancel.stdout)
    assert payload["status"] in {"cancelled", "cancel_failed"}
    time.sleep(0.5)
    latest = json.loads(run_companion(["status", job_id], repo, env).stdout)
    assert latest["status"] in {"cancelled", "cancel_failed"}
    if agy_pid_file.exists():
        agy_pid = int(agy_pid_file.read_text(encoding="utf8"))
        assert not process_is_running(agy_pid)


def test_mailbox_and_leases_are_repo_external(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))

    lease = run_companion(["leases", "claim", "--role", "security", "--ttl-seconds", "60"], repo, env)
    assert lease.returncode == 0, lease.stderr
    lease_payload = json.loads(lease.stdout)
    assert lease_payload["status"] == "claimed"
    assert lease_payload["lease"]["role"] == "security"

    mailbox = run_companion(["mailbox", "post", "--thread", "t1", "--message", "hello"], repo, env)
    assert mailbox.returncode == 0, mailbox.stderr
    mailbox_payload = json.loads(mailbox.stdout)
    assert mailbox_payload["status"] == "posted"

    mailbox_list = run_companion(["mailbox", "list"], repo, env)
    assert mailbox_list.returncode == 0, mailbox_list.stderr
    assert json.loads(mailbox_list.stdout)["threads"][0]["thread"] == "t1"

    mailbox_show = run_companion(["mailbox", "show", "--thread", "t1"], repo, env)
    assert mailbox_show.returncode == 0, mailbox_show.stderr
    shown = json.loads(mailbox_show.stdout)
    assert shown["thread"] == "t1"
    assert shown["messages"][0]["message"] == "hello"

    lease_list = run_companion(["leases", "list"], repo, env)
    assert lease_list.returncode == 0, lease_list.stderr
    assert json.loads(lease_list.stdout)["leases"][0]["id"] == lease_payload["leaseId"]

    lease_release = run_companion(["leases", "release", "--id", lease_payload["leaseId"]], repo, env)
    assert lease_release.returncode == 0, lease_release.stderr
    assert json.loads(lease_release.stdout)["status"] == "released"
    assert not (repo / ".antigravity-for-codex").exists()


def test_mailbox_and_leases_preserve_concurrent_writes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"

    posts = [
        subprocess.Popen(
            [NODE, str(runtime), "mailbox", "post", "--thread", "concurrent", "--message", f"msg-{index}"],
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(12)
    ]
    post_results = [proc.communicate(timeout=10) + (proc.returncode,) for proc in posts]
    assert all(result[2] == 0 for result in post_results), post_results
    shown = json.loads(run_companion(["mailbox", "show", "--thread", "concurrent"], repo, env).stdout)
    assert len(shown["messages"]) == 12

    claims = [
        subprocess.Popen(
            [NODE, str(runtime), "leases", "claim", "--role", f"role-{index}", "--ttl-seconds", "60"],
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(12)
    ]
    claim_results = [proc.communicate(timeout=10) + (proc.returncode,) for proc in claims]
    assert all(result[2] == 0 for result in claim_results), claim_results
    leases = json.loads(run_companion(["leases", "list"], repo, env).stdout)
    assert len(leases["leases"]) == 12


def test_mailbox_thread_history_is_bounded(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))

    for index in range(105):
        posted = run_companion(["mailbox", "post", "--thread", "bounded", "--message", f"msg-{index}"], repo, env)
        assert posted.returncode == 0, posted.stderr

    shown = json.loads(run_companion(["mailbox", "show", "--thread", "bounded"], repo, env).stdout)
    messages = [item["message"] for item in shown["messages"]]
    assert len(messages) == 100
    assert messages[0] == "msg-5"
    assert messages[-1] == "msg-104"
    assert shown["truncated"] is True
    assert shown["droppedMessages"] == 5
    assert shown["retainedMessages"] == 100
    listed = json.loads(run_companion(["mailbox", "list"], repo, env).stdout)["threads"][0]
    assert listed["truncated"] is True
    assert listed["droppedMessages"] == 5
    assert listed["retainedMessages"] == 100


def test_invalid_lease_ttl_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))

    invalid_text = run_companion(["leases", "claim", "--role", "security", "--ttl-seconds", "abc"], repo, env)
    invalid_zero = run_companion(["leases", "claim", "--role", "security", "--ttl-seconds", "0"], repo, env)

    assert invalid_text.returncode == 2
    assert "Lease TTL must be between" in invalid_text.stderr
    assert invalid_zero.returncode == 2
    assert "Lease TTL must be between" in invalid_zero.stderr


def test_advisory_state_write_failure_does_not_fail_multi_review(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    bad_state = tmp_path / "state-file"
    bad_state.write_text("not a directory", encoding="utf8")
    env = os.environ.copy()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="ALLOW"))
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(bad_state)

    result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "multi-review", "--use-mailbox", "--advisory-leases"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "## correctness" in result.stdout


def test_result_marks_job_viewed_and_unread_hook_clears(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="UNREAD_DONE", delay_ms=100))
    hook = PLUGIN / "hooks" / "unread-result.mjs"

    queued = run_companion(["review", "--background", "unread result"], repo, env)
    job_id = json.loads(queued.stdout)["jobId"]
    assert wait_for_job(repo, env, job_id)["status"] == "succeeded"

    first = subprocess.run([NODE, str(hook)], cwd=repo, env=env, capture_output=True, text=True)
    assert first.returncode == 0
    assert job_id in first.stderr
    viewed = run_companion(["result", job_id], repo, env)
    assert viewed.returncode == 0, viewed.stderr
    second = subprocess.run([NODE, str(hook)], cwd=repo, env=env, capture_output=True, text=True)
    assert second.returncode == 0
    assert job_id not in second.stderr


def test_session_lifecycle_hook_writes_repo_external_marker(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    hook = PLUGIN / "hooks" / "session-lifecycle.mjs"

    started = subprocess.run([NODE, str(hook), "start"], cwd=repo, env=env, capture_output=True, text=True)
    ended = subprocess.run([NODE, str(hook), "end"], cwd=repo, env=env, capture_output=True, text=True)

    assert started.returncode == 0, started.stderr
    assert ended.returncode == 0, ended.stderr
    assert list((tmp_path / "state").rglob("session-lifecycle.json"))
    assert not (repo / ".antigravity-for-codex").exists()


def test_session_lifecycle_hook_recovers_non_object_state(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    hook = PLUGIN / "hooks" / "session-lifecycle.mjs"
    seeded = subprocess.run([NODE, str(hook), "start"], cwd=repo, env=env, capture_output=True, text=True)
    assert seeded.returncode == 0, seeded.stderr
    marker = next((tmp_path / "state").rglob("session-lifecycle.json"))
    marker.write_text("[]\n", encoding="utf8")

    recovered = subprocess.run([NODE, str(hook), "end"], cwd=repo, env=env, capture_output=True, text=True)

    assert recovered.returncode == 0, recovered.stderr
    payload = json.loads(marker.read_text(encoding="utf8"))
    assert isinstance(payload, dict)
    assert payload["events"][-1]["event"] == "end"


def test_session_lifecycle_hook_preserves_concurrent_events(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    hook = PLUGIN / "hooks" / "session-lifecycle.mjs"

    runs = [
        subprocess.Popen(
            [NODE, str(hook), "start"],
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(12)
    ]
    results = [proc.communicate(timeout=10) + (proc.returncode,) for proc in runs]

    assert all(result[2] == 0 for result in results), results
    marker = next((tmp_path / "state").rglob("session-lifecycle.json"))
    payload = json.loads(marker.read_text(encoding="utf8"))
    assert len(payload["events"]) == 12


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


def test_job_viewed_and_finish_updates_do_not_regress_state(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    source = (
        "const jobs = await import('./plugins/antigravity-for-codex/scripts/lib/jobs.mjs');"
        f"const cwd = {json.dumps(str(repo))};"
        f"const env = {{ ANTIGRAVITY_FOR_CODEX_STATE_HOME: {json.dumps(str(state))} }};"
        "const viewedFirst = jobs.createJob({command: 'review', cwd}, env);"
        "jobs.markJobRunning(viewedFirst.id, {pid: 123, identity: {pid: 123, command: 'antigravity-companion.mjs'}}, cwd, env);"
        "jobs.markJobViewed(viewedFirst.id, cwd, env);"
        "const finishedAfterView = jobs.finishJob(viewedFirst.id, {status: 0, stdout: 'done after view'}, cwd, env);"
        "const finishedFirst = jobs.createJob({command: 'review', cwd}, env);"
        "jobs.finishJob(finishedFirst.id, {status: 0, stdout: 'done before view'}, cwd, env);"
        "const viewedAfterFinish = jobs.markJobViewed(finishedFirst.id, cwd, env);"
        "process.stdout.write(JSON.stringify({finishedAfterView, viewedAfterFinish}));"
    )

    result = run_node_eval(source)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["finishedAfterView"]["status"] == "succeeded"
    assert payload["finishedAfterView"]["stdout"] == "done after view"
    assert payload["finishedAfterView"]["viewed"] is True
    assert payload["viewedAfterFinish"]["status"] == "succeeded"
    assert payload["viewedAfterFinish"]["stdout"] == "done before view"
    assert payload["viewedAfterFinish"]["viewed"] is True


def test_process_identity_validation_checks_pid_ppid_command_before_group_kill():
    text = (PLUGIN / "scripts" / "lib" / "process.mjs").read_text(encoding="utf8")
    jobs_text = (PLUGIN / "scripts" / "lib" / "jobs.mjs").read_text(encoding="utf8")

    assert "current.pid !== expectedIdentity.pid" in text
    assert "current.command !== expectedIdentity.command" in text
    assert 'current.command.includes("antigravity-companion.mjs")' in text
    assert "ppid changed" not in text
    assert 'process.kill(-numericPid, "SIGTERM")' in text
    assert 'process.platform === "win32"' in text
    assert '"taskkill.exe"' in text
    assert "fs.renameSync(lockFile, staleFile)" in jobs_text


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


def test_github_actions_init_validate_and_render(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    rendered = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--model-provider", "gemini", "--timeout-minutes", "15"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "pull_request:" in rendered.stdout
    assert "pull_request_target" not in rendered.stdout
    assert "workflow_dispatch" not in rendered.stdout
    assert "timeout-minutes: 15" in rendered.stdout
    assert "PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}" in rendered.stdout
    assert "PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in rendered.stdout
    assert 'git diff --stat "$PR_BASE_SHA" "$PR_HEAD_SHA"' in rendered.stdout
    assert 'git diff "$PR_BASE_SHA" "$PR_HEAD_SHA" -- .' in rendered.stdout
    assert "codex plugin list --json" in rendered.stdout
    assert "ANTIGRAVITY_PLUGIN_ROOT=$ANTIGRAVITY_PLUGIN_ROOT" in rendered.stdout
    assert 'node "$ANTIGRAVITY_PLUGIN_ROOT/scripts/antigravity-companion.mjs"' in rendered.stdout
    assert "node plugins/antigravity-for-codex/scripts/antigravity-companion.mjs" not in rendered.stdout
    run_blocks = rendered.stdout.split("run: |")
    assert all("${{ github." not in block.split("\n      - name:", 1)[0] for block in run_blocks[1:])

    init = subprocess.run(
        [NODE, str(runtime), "github-actions", "init", "--force", "--model-provider", "gemini"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert init.returncode == 0, init.stderr
    payload = json.loads(init.stdout)
    assert payload["status"] == "written"
    workflow = repo / ".github" / "workflows" / "antigravity-for-codex-review.yml"
    assert workflow.exists()
    text = workflow.read_text(encoding="utf8")
    assert "pull_request_target" not in text
    assert "antigravity-for-codex-v" in text

    validate = subprocess.run([NODE, str(runtime), "github-actions", "validate"], cwd=repo, capture_output=True, text=True)
    assert validate.returncode == 0, validate.stderr
    assert json.loads(validate.stdout)["ok"] is True


def test_github_actions_render_and_init_use_environment_provider(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"
    env["ANTIGRAVITY_FOR_CODEX_MODEL"] = "Claude Sonnet 4.5"

    rendered = subprocess.run(
        [NODE, str(runtime), "github-actions", "render"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert rendered.returncode == 0, rendered.stderr
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER: "claude"' in rendered.stdout
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL: "Claude Sonnet 4.5"' in rendered.stdout

    init = subprocess.run(
        [NODE, str(runtime), "github-actions", "init", "--force"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert init.returncode == 0, init.stderr
    workflow = repo / ".github" / "workflows" / "antigravity-for-codex-review.yml"
    text = workflow.read_text(encoding="utf8")
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER: "claude"' in text
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL: "Claude Sonnet 4.5"' in text


def test_github_actions_rejects_invalid_environment_provider(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "openai"

    result = subprocess.run(
        [NODE, str(runtime), "github-actions", "render"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Valid values: gemini, claude" in result.stderr


def test_github_actions_default_render_keeps_runtime_model_default(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    for name in [
        "ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER",
        "ANTIGRAVITY_FOR_CODEX_MODEL",
        "ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL",
        "ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL",
    ]:
        env.pop(name, None)

    result = subprocess.run(
        [NODE, str(runtime), "github-actions", "render"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER: "gemini"' in result.stdout
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL: ""' in result.stdout


def test_github_actions_claude_provider_without_model_uses_runtime_default(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"
    for name in [
        "ANTIGRAVITY_FOR_CODEX_MODEL",
        "ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL",
    ]:
        env.pop(name, None)

    result = subprocess.run(
        [NODE, str(runtime), "github-actions", "render"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER: "claude"' in result.stdout
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL: ""' in result.stdout


def test_github_actions_provider_default_model_is_not_embedded(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"
    env["ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL"] = "Claude Sonnet 4.5"
    env.pop("ANTIGRAVITY_FOR_CODEX_MODEL", None)

    result = subprocess.run(
        [NODE, str(runtime), "github-actions", "render"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER: "claude"' in result.stdout
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL: ""' in result.stdout


def test_github_actions_provider_default_model_is_not_validated_when_not_embedded(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "gemini"
    env["ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL"] = "Claude Sonnet 4.5"
    env.pop("ANTIGRAVITY_FOR_CODEX_MODEL", None)

    result = subprocess.run(
        [NODE, str(runtime), "github-actions", "render"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER: "gemini"' in result.stdout
    assert 'ANTIGRAVITY_FOR_CODEX_MODEL: ""' in result.stdout


def test_review_gate_prompt_uses_resolved_environment_model(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    repo.mkdir()
    init_git_repo(repo)
    agy = fake_agy(tmp_path, response="ALLOW: ok", capture_argv=argv_file)
    env = companion_env(tmp_path, agy)
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"
    env["ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL"] = "Claude Sonnet 4.5"

    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert "Model provider: claude." in prompt
    assert "Model: Claude Sonnet 4.5" in prompt


def test_github_actions_rejects_mutable_ref_and_validates_path(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    for ref in ["main", "refs/heads/main", "develop", "release/latest"]:
        bad_ref = subprocess.run(
            [NODE, str(runtime), "github-actions", "render", "--ref", ref],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert bad_ref.returncode == 2
        assert "immutable" in bad_ref.stderr or "release tag" in bad_ref.stderr

    custom_ref = tmp_path / "custom-ref.yml"
    custom_render = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--ref", "antigravity-for-codex-v0.2.0"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert custom_render.returncode == 0, custom_render.stderr
    custom_ref.write_text(custom_render.stdout, encoding="utf8")
    custom_validate = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate", "--path", str(custom_ref)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert custom_validate.returncode == 0, custom_validate.stderr

    invalid_timeout = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--timeout-minutes", "abc"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert invalid_timeout.returncode == 2
    assert "--timeout-minutes must be between" in invalid_timeout.stderr

    workflow = tmp_path / "unsafe.yml"
    workflow.write_text(
        "name: bad\n"
        "# contents: read\n"
        "# npm install -g @openai/codex\n"
        "# codex plugin marketplace add yilibinbin/external-models-for-codex\n"
        "# codex plugin add antigravity-for-codex@external-models-for-codex\n"
        "# antigravity-for-codex-v0.5.4\n"
        "# ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER:\n"
        "# antigravity-companion.mjs review\n"
        "on:\n"
        "  pull_request:\n"
        "permissions:\n"
        "  contents: write\n"
        "jobs:\n"
        "  review:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: |\n"
        "          echo contents: read\n",
        encoding="utf8",
    )
    invalid = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate", "--path", str(workflow)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 1
    assert json.loads(invalid.stdout)["ok"] is False

    mutable_ref_workflow = tmp_path / "mutable-ref.yml"
    mutable_ref_workflow.write_text(
        "name: bad\n"
        "on:\n"
        "  pull_request:\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  review:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: |\n"
        "          npm install -g @openai/codex\n"
        "          codex plugin marketplace add yilibinbin/external-models-for-codex --ref develop\n"
        "          echo --ref antigravity-for-codex-v0.5.4\n"
        "          codex plugin add antigravity-for-codex@external-models-for-codex\n"
        "          ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=gemini node plugins/antigravity-for-codex/scripts/antigravity-companion.mjs review\n",
        encoding="utf8",
    )
    mutable_ref = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate", "--path", str(mutable_ref_workflow)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert mutable_ref.returncode == 1
    checks = {check["name"]: check["ok"] for check in json.loads(mutable_ref.stdout)["checks"]}
    assert checks["immutable-release-ref"] is False

    job_permissions_workflow = tmp_path / "job-permissions.yml"
    job_permissions_workflow.write_text(custom_render.stdout.replace("runs-on: ubuntu-latest", "runs-on: ubuntu-latest\n    permissions:\n      contents: write"), encoding="utf8")
    job_permissions = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate", "--path", str(job_permissions_workflow)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert job_permissions.returncode == 1
    job_checks = {check["name"]: check["ok"] for check in json.loads(job_permissions.stdout)["checks"]}
    assert job_checks["minimal-contents-permission"] is False

    extra_write_workflow = tmp_path / "extra-write.yml"
    extra_write_workflow.write_text(custom_render.stdout.replace("  contents: read", "  contents: read\n  id-token: write # request oidc"), encoding="utf8")
    extra_write = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate", "--path", str(extra_write_workflow)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert extra_write.returncode == 1
    extra_checks = {check["name"]: check["ok"] for check in json.loads(extra_write.stdout)["checks"]}
    assert extra_checks["minimal-contents-permission"] is False

    linux_local_path_workflow = tmp_path / "linux-local-path.yml"
    linux_local_path_workflow.write_text(
        custom_render.stdout.replace(
            'ANTIGRAVITY_FOR_CODEX_MODEL: ""',
            'ANTIGRAVITY_FOR_CODEX_MODEL: ""\n          LEAKED_LOCAL_PATH: "/home/example/project/plugins/antigravity-for-codex"',
        ),
        encoding="utf8",
    )
    linux_local_path = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate", "--path", str(linux_local_path_workflow)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert linux_local_path.returncode == 1
    linux_local_path_checks = {check["name"]: check["ok"] for check in json.loads(linux_local_path.stdout)["checks"]}
    assert linux_local_path_checks["no-local-absolute-paths"] is False


def test_github_actions_rejects_invalid_shell_like_model(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    marker = tmp_path / "SHOULD_NOT_RUN"
    rendered = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--model", f"gemini $(touch {marker})"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert rendered.returncode == 2
    assert "Invalid Antigravity model value" in rendered.stderr
    assert rendered.stdout == ""
    assert not marker.exists()


def test_render_review_comment_and_annotations_helper():
    source = (
        "const rr = await import('./plugins/antigravity-for-codex/scripts/lib/render-review.mjs');"
        "const comment = rr.renderPullRequestComment({command: 'multi-review', provider: 'gemini', model: 'gemini-pro', body: 'Body'});"
        "const annotations = rr.renderGithubAnnotations(["
        "{path: 'a.js', line: 3, severity: 'high', message: 'Fix it'},"
        "{path: 'b.js', line: 'abc', end_line: -1, severity: 'critical', summary: 'Bad'}"
        "]);"
        "process.stdout.write(JSON.stringify({comment, annotations}));"
    )
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "## Antigravity for Codex Review" in payload["comment"]
    assert "- Command: multi-review" in payload["comment"]
    assert payload["annotations"][0]["annotation_level"] == "failure"
    assert payload["annotations"][0]["start_line"] == 3
    assert payload["annotations"][1]["annotation_level"] == "failure"
    assert payload["annotations"][1]["start_line"] == 1
    assert payload["annotations"][1]["end_line"] == 1


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
    prompt = argv[argv.index("--prompt") + 1]
    assert "<role_name>stop-gate</role_name>" in prompt
    assert "<task>Run a stop-gate review of the current git changes.</task>" in prompt


def test_review_gate_hook_does_not_block_on_open_stdin_pipe(tmp_path):
    hook = PLUGIN / "hooks" / "antigravity-review-gate.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = os.environ.copy()
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="ALLOW: ok"))
    text = hook.read_text(encoding="utf8")
    assert "readFileSync(0" not in text

    proc = subprocess.Popen(
        [NODE, str(hook)],
        cwd=repo,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        proc.wait(timeout=10)
    finally:
        if proc.stdin:
            proc.stdin.close()
    stdout = proc.stdout.read() if proc.stdout else ""
    stderr = proc.stderr.read() if proc.stderr else ""
    assert proc.returncode == 0
    assert stdout == ""
    assert stderr == ""


def test_release_check_passes():
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=PLUGIN, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "PASS manifest-name" in result.stdout
    assert "PASS manifest-version" in result.stdout
    assert "PASS docs-version-aligned" in result.stdout
    assert "PASS marketplace-docs-release-ref" in result.stdout
    assert "PASS manifest-model-policy" in result.stdout
    assert "PASS skills-natural-language-routing-paths" in result.stdout
    assert "PASS skills-natural-language-routing" in result.stdout
    assert "PASS agy-prompt-timeout-argv" in result.stdout
    assert "PASS no-print-argv" in result.stdout
    assert "PASS github-actions-template" in result.stdout
    assert "PASS github-actions-release-ref" in result.stdout
    assert "PASS github-actions-plugin-root-resolved" in result.stdout
    assert "PASS github-actions-no-repo-relative-runtime-path" in result.stdout
    assert "PASS skill-antigravity-role-packs" in result.stdout
    assert "PASS skill-antigravity-status" in result.stdout
    assert "PASS skill-antigravity-result" in result.stdout
    assert "PASS skill-antigravity-cancel" in result.stdout
    assert "PASS workflow-plugin-install" in result.stdout
    assert "PASS version-helper" in result.stdout
    assert "PASS release-ref-derived" in result.stdout
    assert "PASS docs-maturity-boundary" in result.stdout
    assert "PASS docs-no-unsupported-parity" in result.stdout
    assert "PASS docs-claude-through-antigravity-boundary" in result.stdout
    assert "PASS docs-real-smoke-opt-in" in result.stdout
    assert "PASS docs-ci-authenticated-agy" in result.stdout
    assert "PASS model-catalog-not-in-hot-path" in result.stdout
    assert "PASS all-mature-commands" in result.stdout


def test_release_check_passes_from_installed_plugin_layout(tmp_path):
    installed = tmp_path / "plugins" / "cache" / "external-models-for-codex" / "antigravity-for-codex" / "0.5.4"
    shutil.copytree(PLUGIN, installed)
    runtime = installed / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=installed, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "PASS marketplace-docs-release-ref - skipped repo-level docs in installed plugin layout" in result.stdout
    assert "PASS skills-natural-language-routing" in result.stdout
    assert "PASS github-actions-no-repo-relative-runtime-path" in result.stdout


def test_release_check_enforces_repo_docs_when_source_layout_has_docs(tmp_path):
    repo = tmp_path / "repo"
    plugin = repo / "plugins" / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    (repo / "docs").mkdir(parents=True)
    (repo / "README.md").write_text("codex plugin marketplace add yilibinbin/external-models-for-codex --ref antigravity-for-codex-v0.0.0\n", encoding="utf8")
    (repo / "docs" / "README.en.md").write_text("missing current release ref\n", encoding="utf8")
    (repo / "docs" / "README.zh-CN.md").write_text("missing current release ref\n", encoding="utf8")
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, capture_output=True, text=True)

    assert result.returncode == 1
    assert "release-check failed: marketplace-docs-release-ref" in result.stderr


def test_version_helper_matches_manifest():
    source = (
        "const version = await import('./plugins/antigravity-for-codex/scripts/lib/version.mjs');"
        "process.stdout.write(JSON.stringify(version));"
    )
    result = run_node_eval(source)
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf8"))
    payload = json.loads(result.stdout)
    assert payload["PLUGIN_VERSION"] == manifest["version"]
    assert payload["RELEASE_REF"] == f"antigravity-for-codex-v{manifest['version']}"


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


def test_real_smoke_full_runs_review_shapes(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    argv_file = tmp_path / "agy-full-argv.json"
    env = companion_env(tmp_path, fake_agy(tmp_path, smart_smoke=True, capture_argv=argv_file))
    env["ANTIGRAVITY_FOR_CODEX_REAL_SMOKE"] = "1"

    result = subprocess.run(
        [NODE, str(runtime), "real-smoke", "--full", "--model-provider", "gemini", "--model", "Gemini Custom (High)", "--timeout-seconds", "5"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "PASS real-smoke gemini direct" in result.stdout
    assert "PASS real-smoke gemini structured" in result.stdout
    assert "PASS real-smoke gemini multi-review" in result.stdout
    assert "PASS real-smoke gemini review-gate" in result.stdout
    assert "PASS real-smoke gemini report" in result.stdout
    assert "modelCatalog" in result.stdout
    assert list((tmp_path / "state").rglob("reports/*.json"))
    calls = json.loads(argv_file.read_text(encoding="utf8"))["calls"]
    model_values = [call["argv"][call["argv"].index("--model") + 1] for call in calls if "--prompt" in call["argv"]]
    assert model_values
    assert all(value == "Gemini Custom (High)" for value in model_values)
