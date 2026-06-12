import json
import os
import pathlib
import shlex
import shutil
import subprocess
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "antigravity-for-codex"
NODE = os.environ.get("NODE_BINARY") or shutil.which("node")
if not NODE:
    raise RuntimeError("node not found; set NODE_BINARY or put node on PATH")

def sanitized_env():
    env = dict(os.environ)
    for name in list(env):
        if name in {"AGY_CLI_PATH", "ANTIGRAVITY_CLI_PATH"} or name.startswith("ANTIGRAVITY_FOR_CODEX_"):
            env.pop(name, None)
    return env


def test_sanitized_env_removes_antigravity_runtime_flags(monkeypatch):
    polluted = {
        "AGY_CLI_PATH": "/polluted/agy",
        "ANTIGRAVITY_CLI_PATH": "/polluted/antigravity",
        "ANTIGRAVITY_FOR_CODEX_DISABLE_LOG_CAPTURE": "1",
        "ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER": "openai",
        "ANTIGRAVITY_FOR_CODEX_MODEL": "bad-model",
        "ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL": "Claude Sonnet 4.6 (Thinking)",
        "ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL": "Gemini 3.1 Pro (High)",
        "ANTIGRAVITY_FOR_CODEX_STATE_HOME": "/polluted/state",
        "ANTIGRAVITY_FOR_CODEX_REVIEW_GATE": "on",
        "ANTIGRAVITY_FOR_CODEX_REAL_SMOKE": "1",
        "ANTIGRAVITY_FOR_CODEX_TEST_DISABLE_CANDIDATE_DISCOVERY": "1",
    }
    for name, value in polluted.items():
        monkeypatch.setenv(name, value)

    env = sanitized_env()

    for name in polluted:
        assert name not in env


def test_antigravity_manifest_is_valid_json():
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf8"))
    assert manifest["name"] == "antigravity-for-codex"
    assert manifest["version"] == "0.6.0"
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
    assert len(manifest["interface"]["defaultPrompt"]) <= 3
    assert manifest["interface"]["composerIcon"].startswith("./assets/")
    assert manifest["interface"]["logo"].startswith("./assets/")
    assert all(item.startswith("./assets/") for item in manifest["interface"]["screenshots"])
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
        "agy-capabilities.mjs",
        "agy-outcome.mjs",
        "antigravity-runtime.mjs",
        "doctor.mjs",
        "github-actions.mjs",
        "hook-compat.mjs",
        "job-lifecycle.mjs",
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
        "worktree-fingerprint.mjs",
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


def test_antigravity_hook_compatibility_report():
    source = (
        "const h = await import('./plugins/antigravity-for-codex/scripts/lib/hook-compat.mjs');"
        "process.stdout.write(JSON.stringify(h.antigravityHookCompatibility()));"
    )
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["supportedEvents"] == ["SessionStart", "SessionEnd", "UserPromptSubmit", "Stop"]
    assert payload["unsupportedEvents"] == ["PreToolUse", "PostToolUse", "PermissionRequest", "Notification"]
    assert [item["event"] for item in payload["supported"]] == ["SessionStart", "SessionEnd", "UserPromptSubmit", "Stop"]
    assert [item["event"] for item in payload["unsupported"]] == ["PreToolUse", "PostToolUse", "PermissionRequest", "Notification"]
    for item in payload["supported"]:
        assert item["behavior"]
        assert item["failOpen"] is True
    assert any(item["event"] == "PreToolUse" for item in payload["unsupported"])
    for item in payload["unsupported"]:
        assert item["reason"]
    for event in payload["supportedEvents"]:
        assert payload["events"][event]["supported"] is True
        assert payload["events"][event]["behavior"]
        assert payload["events"][event]["failOpen"] is True
    for event in payload["unsupportedEvents"]:
        assert payload["events"][event]["supported"] is False
        assert payload["events"][event]["reason"]


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
    help_exit_code=0,
    help_stderr="",
    models_exit_code=0,
    models_stderr="",
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
            "if (prompt.includes('<role_name>stop-gate</role_name>') || prompt.includes('Run a stop-gate review')) output = 'ALLOW: ANTIGRAVITY_FOR_CODEX_SMOKE_OK';\n"
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
        f"if (argv.join(' ') === '--help') {{ process.stdout.write({json.dumps(help_text)}); process.stderr.write({json.dumps(help_stderr)}); process.exit({int(help_exit_code)}); }}\n"
        f"if (argv.join(' ') === 'models') {{ process.stdout.write({json.dumps(models_text)}); process.stderr.write({json.dumps(models_stderr)}); process.exit({int(models_exit_code)}); }}\n"
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


def descendant_spawning_agy(tmp_path, descendant_pid_file):
    agy = tmp_path / "agy"
    agy.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('fs');\n"
        "const { spawn } = require('child_process');\n"
        "const argv = process.argv.slice(2);\n"
        "if (argv.join(' ') === '--version') { console.log('1.0.6-fake'); process.exit(0); }\n"
        f"if (argv.join(' ') === '--help') {{ process.stdout.write({json.dumps(FAKE_AGY_HELP)}); process.exit(0); }}\n"
        f"if (argv.join(' ') === 'models') {{ process.stdout.write({json.dumps(FAKE_AGY_MODELS)}); process.exit(0); }}\n"
        "if (!argv.includes('--prompt')) { console.error('missing prompt'); process.exit(9); }\n"
        "if (!argv.includes('--model')) { console.error('missing model'); process.exit(8); }\n"
        "process.on('SIGTERM', () => {});\n"
        "const child = spawn(process.execPath, ['-e', 'process.on(\"SIGTERM\", () => {}); setInterval(() => {}, 1000)'], { stdio: 'ignore' });\n"
        "child.unref();\n"
        f"fs.writeFileSync({json.dumps(str(descendant_pid_file))}, String(child.pid));\n"
        "setInterval(() => {}, 1000);\n",
        encoding="utf8",
    )
    agy.chmod(0o755)
    return agy


def run_node_eval(source, env=None):
    return subprocess.run(
        [NODE, "--input-type=module", "-e", source],
        cwd=ROOT,
        env=env or sanitized_env(),
        capture_output=True,
        text=True,
    )


def test_antigravity_process_probe_timeout_is_fail_closed_for_cancel(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ps = fake_bin / "ps"
    ps.write_text("#!/bin/sh\nsleep 2\n", encoding="utf8")
    ps.chmod(0o755)
    env = sanitized_env()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["ANTIGRAVITY_FOR_CODEX_PS_TIMEOUT_MS"] = "50"
    source = """
import { captureProcessIdentityProbe, psProbeDiagnostics } from './plugins/antigravity-for-codex/scripts/lib/process.mjs';
const identity = captureProcessIdentityProbe(999999, process.env);
const diagnostics = psProbeDiagnostics();
if (!identity.inconclusive) throw new Error('expected inconclusive identity');
if (!diagnostics.lastFailure) throw new Error('missing ps diagnostic');
console.log(JSON.stringify(diagnostics));
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr


def test_antigravity_process_probe_nonzero_ps_for_live_process_is_inconclusive(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ps = fake_bin / "ps"
    ps.write_text("#!/bin/sh\necho 'fake ps failed' >&2\nexit 2\n", encoding="utf8")
    ps.chmod(0o755)
    env = sanitized_env()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    source = """
import { spawn } from 'node:child_process';
import process from 'node:process';
import { captureProcessIdentityProbe } from './plugins/antigravity-for-codex/scripts/lib/process.mjs';

function sleepMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
if (!child.pid) throw new Error('failed to spawn test child');

const deadline = Date.now() + 1000;
let running = false;
while (Date.now() < deadline) {
  try {
    process.kill(child.pid, 0);
    running = true;
    break;
  } catch {}
  sleepMs(25);
}
if (!running) throw new Error('test child did not become observable');

try {
  const probe = captureProcessIdentityProbe(child.pid, process.env);
  if (!probe.inconclusive) throw new Error(`expected inconclusive probe: ${JSON.stringify(probe)}`);
  if (probe.notRunning) throw new Error(`live process was classified as not running: ${JSON.stringify(probe)}`);
  if (!String(probe.diagnostic?.stderr || '').includes('fake ps failed')) {
    throw new Error(`expected ps diagnostic stderr: ${JSON.stringify(probe)}`);
  }
  console.log(JSON.stringify(probe));
} finally {
  try {
    process.kill(child.pid, 'SIGKILL');
  } catch {}
}
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr


def test_antigravity_cancel_probe_timeout_does_not_signal_worker(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ps = fake_bin / "ps"
    ps.write_text("#!/bin/sh\nsleep 2\n", encoding="utf8")
    ps.chmod(0o755)
    env = sanitized_env()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["ANTIGRAVITY_FOR_CODEX_PS_TIMEOUT_MS"] = "50"
    source = """
import { terminateValidatedJobWorker } from './plugins/antigravity-for-codex/scripts/lib/process.mjs';

const expected = {
  pid: 999999,
  ppid: process.pid,
  command: `${process.execPath} plugins/antigravity-for-codex/scripts/antigravity-companion.mjs __run-job fake`
};

const termination = terminateValidatedJobWorker(expected.pid, expected, process.env);
if (termination.status !== 'failed') throw new Error(`expected failed cancellation: ${JSON.stringify(termination)}`);
if (termination.phase !== 'initial') throw new Error(`expected initial phase: ${JSON.stringify(termination)}`);
if (!termination.diagnostic) throw new Error(`expected diagnostic: ${JSON.stringify(termination)}`);
console.log(JSON.stringify(termination));
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr


def test_antigravity_windows_taskkill_failure_after_worker_exit_is_not_running(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    count_file = tmp_path / "powershell-count"
    powershell = fake_bin / "powershell.exe"
    powershell.write_text(
        "#!/bin/sh\n"
        f"count_file={shlex.quote(str(count_file))}\n"
        "count=0\n"
        "[ -f \"$count_file\" ] && count=$(cat \"$count_file\")\n"
        "next=$((count + 1))\n"
        "printf '%s' \"$next\" > \"$count_file\"\n"
        "if [ \"$count\" -eq 0 ]; then\n"
        "  printf '%s\\n' '{\"ProcessId\":4242,\"ParentProcessId\":1,\"CommandLine\":\"node plugins/antigravity-for-codex/scripts/antigravity-companion.mjs __run-job fake\"}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf8",
    )
    powershell.chmod(0o755)
    taskkill = fake_bin / "taskkill.exe"
    taskkill.write_text("#!/bin/sh\necho 'taskkill target not found' >&2\nexit 128\n", encoding="utf8")
    taskkill.chmod(0o755)
    env = sanitized_env()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    source = """
import process from 'node:process';
import { terminateValidatedJobWorker } from './plugins/antigravity-for-codex/scripts/lib/process.mjs';

try {
  Object.defineProperty(process, 'platform', { value: 'win32' });
} catch {
  process.exit(0);
}

const expected = {
  pid: 4242,
  ppid: 1,
  command: 'node plugins/antigravity-for-codex/scripts/antigravity-companion.mjs __run-job fake'
};
const termination = terminateValidatedJobWorker(4242, expected, process.env);
if (termination.status !== 'not_running') {
  throw new Error(`expected not_running after taskkill race: ${JSON.stringify(termination)}`);
}
if (termination.phase !== 'taskkill') {
  throw new Error(`expected taskkill phase: ${JSON.stringify(termination)}`);
}
console.log(JSON.stringify(termination));
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr


def test_antigravity_cancel_missing_trusted_identity_rejects_missing_pid():
    source = """
import { terminateValidatedJobWorker } from './plugins/antigravity-for-codex/scripts/lib/process.mjs';

const termination = terminateValidatedJobWorker(undefined, {}, process.env);
if (termination.status !== 'failed') throw new Error(`expected failed cancellation: ${JSON.stringify(termination)}`);
if (termination.phase !== 'initial') throw new Error(`expected initial phase: ${JSON.stringify(termination)}`);
if (!String(termination.error || '').includes('missing trusted worker identity')) {
  throw new Error(`expected missing trusted identity error: ${JSON.stringify(termination)}`);
}
console.log(JSON.stringify(termination));
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr


def test_antigravity_cancel_uses_top_level_worker_pid_when_worker_pid_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ps = fake_bin / "ps"
    ps.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '4242 1 node plugins/antigravity-for-codex/scripts/antigravity-companion.mjs __run-job fake'\n",
        encoding="utf8",
    )
    ps.chmod(0o755)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["ANTIGRAVITY_FOR_CODEX_TEST_REPO"] = str(repo)
    source = """
import process from 'node:process';
import { createJob, updateJob, cancelJob } from './plugins/antigravity-for-codex/scripts/lib/jobs.mjs';

const originalKill = process.kill;
const killCalls = [];
const cwd = process.env.ANTIGRAVITY_FOR_CODEX_TEST_REPO;

try {
  process.kill = (pid, signal = 0) => {
    killCalls.push({ pid, signal });
    if (pid === -4242 && signal === 'SIGTERM') {
      return true;
    }
    if (pid === -4242 && signal === 0) {
      const error = new Error('no such process group');
      error.code = 'ESRCH';
      throw error;
    }
    return originalKill(pid, signal);
  };

  const expectedIdentity = {
    pid: 4242,
    ppid: 1,
    command: 'node plugins/antigravity-for-codex/scripts/antigravity-companion.mjs __run-job fake'
  };
  const job = createJob({ command: 'review', args: ['top-level worker pid'], cwd }, process.env);
  updateJob(job.id, (draft) => {
    draft.status = 'running';
    draft.submissionState = 'running';
    draft.worker = { identity: expectedIdentity };
    draft.workerPid = 4242;
    return draft;
  }, cwd, process.env);

  const cancelled = cancelJob(job.id, cwd, process.env);
  if (cancelled.status !== 'cancelled') throw new Error(`expected cancelled: ${JSON.stringify(cancelled)}`);
  if (cancelled.cancel.status === 'not_running') {
    throw new Error(`top-level worker pid was not used for termination: ${JSON.stringify(cancelled)}`);
  }
  if (cancelled.cancel.status !== 'terminated') {
    throw new Error(`expected terminated cancellation: ${JSON.stringify(cancelled)}`);
  }
  if (!killCalls.some((call) => call.pid === -4242 && call.signal === 'SIGTERM')) {
    throw new Error(`expected SIGTERM for top-level worker pid: ${JSON.stringify({ cancelled, killCalls })}`);
  }
  console.log(JSON.stringify({ cancel: cancelled.cancel, killCalls }));
} finally {
  process.kill = originalKill;
}
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr


def test_antigravity_cancel_post_sigterm_probe_timeout_does_not_sigkill(tmp_path):
    worker_script = tmp_path / "antigravity-companion.mjs"
    worker_script.write_text(
        "process.on('SIGTERM', () => {});\n"
        "setInterval(() => {}, 1000);\n",
        encoding="utf8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    count_file = tmp_path / "ps-count"
    real_ps = shutil.which("ps") or "/bin/ps"
    ps = fake_bin / "ps"
    ps.write_text(
        "#!/bin/sh\n"
        f"count_file={shlex.quote(str(count_file))}\n"
        f"real_ps={shlex.quote(real_ps)}\n"
        "count=0\n"
        "[ -f \"$count_file\" ] && count=$(cat \"$count_file\")\n"
        "next=$((count + 1))\n"
        "printf '%s' \"$next\" > \"$count_file\"\n"
        "if [ \"$count\" -eq 0 ]; then\n"
        "  exec \"$real_ps\" \"$@\"\n"
        "fi\n"
        "sleep 2\n",
        encoding="utf8",
    )
    ps.chmod(0o755)
    env = sanitized_env()
    env["REAL_PATH"] = env.get("PATH", "")
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["ANTIGRAVITY_FOR_CODEX_PS_TIMEOUT_MS"] = "500"
    env["ANTIGRAVITY_FOR_CODEX_TEST_WORKER_SCRIPT"] = str(worker_script)
    source = """
import { spawn } from 'node:child_process';
import process from 'node:process';
import {
  captureProcessIdentityProbe,
  terminateValidatedJobWorker
} from './plugins/antigravity-for-codex/scripts/lib/process.mjs';

function sleepMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

const child = spawn(process.execPath, [process.env.ANTIGRAVITY_FOR_CODEX_TEST_WORKER_SCRIPT, '__run-job', 'fake'], {
  detached: process.platform !== 'win32',
  stdio: 'ignore'
});
child.unref();
if (!child.pid) throw new Error('failed to spawn test child');

const deadline = Date.now() + 1000;
let running = false;
while (Date.now() < deadline) {
  try {
    process.kill(child.pid, 0);
    running = true;
    break;
  } catch {}
  sleepMs(25);
}
if (!running) throw new Error('test child did not become observable');

try {
  const expectedProbe = captureProcessIdentityProbe(child.pid, { ...process.env, PATH: process.env.REAL_PATH });
  if (!expectedProbe.ok) throw new Error(`expected initial real probe: ${JSON.stringify(expectedProbe)}`);
  const termination = terminateValidatedJobWorker(child.pid, expectedProbe.identity, process.env);
  if (termination.status !== 'failed') throw new Error(`expected failed cancellation: ${JSON.stringify(termination)}`);
  if (termination.phase !== 'post-sigterm') throw new Error(`expected post-sigterm phase: ${JSON.stringify(termination)}`);
  try {
    process.kill(child.pid, 0);
  } catch {
    throw new Error('child was SIGKILLed after inconclusive post-SIGTERM probe');
  }
  console.log(JSON.stringify(termination));
} finally {
  try {
    if (process.platform === 'win32') {
      process.kill(child.pid, 'SIGKILL');
    } else {
      process.kill(-child.pid, 'SIGKILL');
    }
  } catch {}
}
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr


def test_antigravity_cancel_missing_trusted_identity_for_active_job_fails(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    env["ANTIGRAVITY_FOR_CODEX_TEST_REPO"] = str(repo)
    source = """
import { createJob, updateJob } from './plugins/antigravity-for-codex/scripts/lib/jobs.mjs';

const cwd = process.env.ANTIGRAVITY_FOR_CODEX_TEST_REPO;
const job = createJob({ command: 'review', args: ['missing trusted identity'], cwd }, process.env);
updateJob(job.id, (draft) => {
  draft.status = 'running';
  draft.submissionState = 'running';
  draft.worker = null;
  draft.workerPid = 123456;
  return draft;
}, cwd, process.env);
console.log(job.id);
"""
    created = run_node_eval(source, env)
    assert created.returncode == 0, created.stderr
    job_id = created.stdout.strip()

    cancel = run_companion(["cancel", job_id], repo, env)

    assert cancel.returncode == 0, cancel.stderr
    payload = json.loads(cancel.stdout)
    assert payload["status"] == "cancel_failed"
    assert "missing trusted worker identity" in payload["error"]
    latest = json.loads(run_companion(["status", job_id], repo, env).stdout)
    assert latest["status"] == "cancel_failed"
    assert latest["cancel"]["phase"] == "initial"


def test_antigravity_cancel_missing_trusted_identity_for_starting_queued_job_fails(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    env["ANTIGRAVITY_FOR_CODEX_TEST_REPO"] = str(repo)
    source = """
import { createJob, updateJob } from './plugins/antigravity-for-codex/scripts/lib/jobs.mjs';

const cwd = process.env.ANTIGRAVITY_FOR_CODEX_TEST_REPO;
const job = createJob({ command: 'review', args: ['starting missing trusted identity'], cwd }, process.env);
updateJob(job.id, (draft) => {
  draft.status = 'queued';
  draft.submissionState = 'starting';
  draft.worker = null;
  draft.workerPid = null;
  return draft;
}, cwd, process.env);
console.log(job.id);
"""
    created = run_node_eval(source, env)
    assert created.returncode == 0, created.stderr
    job_id = created.stdout.strip()

    cancel = run_companion(["cancel", job_id], repo, env)

    assert cancel.returncode == 0, cancel.stderr
    payload = json.loads(cancel.stdout)
    assert payload["status"] == "cancel_failed"
    assert "missing trusted worker identity" in payload["error"]
    assert payload["cancel"]["phase"] == "initial"


def companion_env(tmp_path, agy=None):
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    if agy is not None:
        env["AGY_CLI_PATH"] = str(agy)
    return env


def doctor_env(agy):
    env = sanitized_env()
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


def wait_for_process_exit(pid, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process_is_running(pid):
            return True
        time.sleep(0.05)
    return not process_is_running(pid)


def test_agy_capabilities_parse_help_and_model_catalog():
    source = """
import {
  parseAgyHelp,
  parseAgyModels,
  selectAgyModel,
  validateAgyModelForProvider
} from './plugins/antigravity-for-codex/scripts/lib/agy-capabilities.mjs';

const help = `Usage of agy:
  --add-dir
  --log-file
  --model
  --print
  --print-timeout
  --prompt
  --sandbox
Available subcommands:
  models
  plugin
`;
const capabilities = parseAgyHelp(help);
const timeoutOnlyCapabilities = parseAgyHelp('Usage of agy:\\n  --print-timeout\\n  --prompt\\n');
const models = parseAgyModels(`Gemini 3.1 Pro (High)
Claude Sonnet 4.6 (Thinking)
GPT-OSS 120B (Medium)
`);
if (!capabilities.prompt || !capabilities.printTimeout || !capabilities.logFile || !capabilities.modelsCommand) {
  throw new Error('expected key agy capabilities');
}
if (timeoutOnlyCapabilities.print) {
  throw new Error('--print-timeout must not imply standalone --print support');
}
if (models.gemini[0] !== 'Gemini 3.1 Pro (High)') throw new Error('gemini model missing');
if (models.claude[0] !== 'Claude Sonnet 4.6 (Thinking)') throw new Error('claude model missing');
if (models.unsupported[0] !== 'GPT-OSS 120B (Medium)') throw new Error('unsupported model missing');
const mixedEnv = {
  ANTIGRAVITY_FOR_CODEX_MODEL: 'Gemini 3.1 Pro (High)',
  ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL: 'Claude Sonnet 4.6 (Thinking)'
};
const explicitProvider = selectAgyModel({ provider: 'claude', env: mixedEnv, models });
if (explicitProvider.model !== 'Claude Sonnet 4.6 (Thinking)' || explicitProvider.source !== 'env-provider') {
  throw new Error(`generic env should not block explicit Claude provider: ${JSON.stringify(explicitProvider)}`);
}
const catalogFallback = selectAgyModel({
  provider: 'claude',
  env: { ANTIGRAVITY_FOR_CODEX_MODEL: 'Gemini 3.1 Pro (High)' },
  models
});
if (catalogFallback.model !== 'Claude Sonnet 4.6 (Thinking)' || catalogFallback.source !== 'catalog') {
  throw new Error(`generic env mismatch should fall back to provider catalog: ${JSON.stringify(catalogFallback)}`);
}
const providerFallback = selectAgyModel({
  provider: 'gemini',
  env: {
    ANTIGRAVITY_FOR_CODEX_MODEL: 'Claude Sonnet 4.6 (Thinking)',
    ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL: 'Gemini 3.1 Pro (High)'
  },
  models
});
if (providerFallback.model !== 'Gemini 3.1 Pro (High)' || providerFallback.source !== 'env-provider') {
  throw new Error(`valid other-provider generic env should fall back to Gemini provider env: ${JSON.stringify(providerFallback)}`);
}
if (selectAgyModel({ provider: 'gemini', models }).model !== 'Gemini 3.1 Pro (High)') throw new Error('gemini default wrong');
if (selectAgyModel({ provider: 'claude', models }).model !== 'Claude Sonnet 4.6 (Thinking)') throw new Error('claude default wrong');
const reorderedModels = parseAgyModels(`Gemini 3.5 Flash (Medium)
Gemini 3.1 Pro (High)
Claude Opus 4.6 (Thinking)
Claude Sonnet 4.6 (Thinking)
`);
if (selectAgyModel({ provider: 'gemini', models: reorderedModels }).model !== 'Gemini 3.1 Pro (High)') {
  throw new Error('gemini default should prefer high-quality default over first catalog entry');
}
if (selectAgyModel({ provider: 'claude', models: reorderedModels }).model !== 'Claude Sonnet 4.6 (Thinking)') {
  throw new Error('claude default should prefer configured default over first catalog entry');
}
for (const [provider, model, expected] of [
  ['gemini', 'Anthropic', 'requires a Gemini model'],
  ['claude', 'not-a-model', 'requires a Claude/Sonnet/Opus/Haiku model']
]) {
  try {
    selectAgyModel({ provider, env: { ANTIGRAVITY_FOR_CODEX_MODEL: model }, models });
    throw new Error(`${provider} accepted malformed generic env`);
  } catch (error) {
    if (!String(error.message).includes(expected)) throw error;
  }
}
try {
  validateAgyModelForProvider('GPT-OSS 120B (Medium)', 'gemini');
  throw new Error('gpt model was accepted');
} catch (error) {
  if (!String(error.message).includes('does not support GPT/OpenAI')) throw error;
}
console.log(JSON.stringify({ ok: true, capabilities, models }));
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr


def test_runtime_defaults_to_gemini_model(tmp_path):
    agy = fake_agy(tmp_path)
    env = sanitized_env()
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
    env = sanitized_env()
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
    env = sanitized_env()
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


def test_runtime_claude_provider_ignores_cross_provider_generic_env(tmp_path):
    agy = fake_agy(tmp_path)
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(agy)
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"
    env["ANTIGRAVITY_FOR_CODEX_MODEL"] = "Gemini 3.1 Pro (High)"
    env["ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL"] = "Claude Sonnet 4.6 (Thinking)"
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
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(agy)
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "process.stdout.write(JSON.stringify(r.antigravityPreflight(process.env, {model: 'Anthropic'})));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "requires a Claude/Sonnet/Opus/Haiku model" in payload["error"]


def test_runtime_rejects_cross_provider_model(tmp_path):
    agy = fake_agy(tmp_path)
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(agy)
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "gemini"
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "process.stdout.write(JSON.stringify(r.antigravityPreflight(process.env, {model: 'Claude Sonnet 4.6 (Thinking)'})));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "requires a Gemini model" in payload["error"]


def test_runtime_rejects_gpt_model_for_any_provider(tmp_path):
    agy = fake_agy(tmp_path)
    env = sanitized_env()
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
    env = sanitized_env()
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


def test_runtime_preflight_error_result_includes_outcome(tmp_path):
    help_without_print_timeout = FAKE_AGY_HELP.replace("  --print-timeout\n", "")
    agy = fake_agy(tmp_path, help_text=help_without_print_timeout)
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(agy)
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "const result = r.antigravityPrint('preflight check', {}, process.env);"
        "process.stdout.write(JSON.stringify(result));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == 2
    assert payload["outcome"]["kind"] == "provider-error"
    assert payload["outcome"]["ok"] is False


def test_runtime_async_preflight_error_result_includes_outcome(tmp_path):
    help_without_print_timeout = FAKE_AGY_HELP.replace("  --print-timeout\n", "")
    agy = fake_agy(tmp_path, help_text=help_without_print_timeout)
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(agy)
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "const result = await r.antigravityPrintAsync('preflight check', {}, process.env);"
        "process.stdout.write(JSON.stringify(result));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == 2
    assert payload["outcome"]["kind"] == "provider-error"
    assert payload["outcome"]["ok"] is False


def test_runtime_async_timeout_kills_agy_that_ignores_sigterm(tmp_path):
    agy = fake_agy(tmp_path, ignore_sigterm=True, never_exit=True)
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(agy)
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "const started = Date.now();"
        "const preflight = r.antigravityPreflight(process.env, {timeout: 2000});"
        "if (!preflight.ok) throw new Error(preflight.error || 'preflight failed');"
        "const result = await r.antigravityPrintAsync('timeout check', "
        "{preflight, timeout: 100, timeoutKillGraceMs: 50, timeoutForceResolveGraceMs: 50}, process.env);"
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


def test_runtime_async_timeout_close_handler_keeps_timeout_outcome(tmp_path):
    agy = tmp_path / "agy"
    agy.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('fs');\n"
        "const argv = process.argv.slice(2);\n"
        "if (argv.join(' ') === '--help') { process.stdout.write(`Usage of agy:\\n  --log-file\\n  --model\\n  --print-timeout\\n  --prompt\\n`); process.exit(0); }\n"
        "if (argv.join(' ') === 'models') { process.stdout.write('Gemini 3.1 Pro (High)\\nClaude Sonnet 4.6 (Thinking)\\n'); process.exit(0); }\n"
        "process.on('SIGTERM', () => {\n"
        "  const logFileIndex = argv.indexOf('--log-file');\n"
        "  if (logFileIndex >= 0) fs.writeFileSync(argv[logFileIndex + 1], 'E0611 agent executor error: RESOURCE_EXHAUSTED (code 429): timeout quota detail\\n');\n"
        "  process.exit(0);\n"
        "});\n"
        "setInterval(() => {}, 1000);\n",
        encoding="utf8",
    )
    agy.chmod(0o755)
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(agy)
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "const preflight = r.antigravityPreflight(process.env, {timeout: 2000});"
        "if (!preflight.ok) throw new Error(preflight.error || 'preflight failed');"
        "const result = await r.antigravityPrintAsync('timeout check', "
        "{preflight, timeout: 100, timeoutKillGraceMs: 1000, timeoutForceResolveGraceMs: 1000}, process.env);"
        "process.stdout.write(JSON.stringify(result));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["timedOut"] is True
    assert payload["errorCode"] == "ETIMEDOUT"
    assert payload["outcome"]["kind"] == "timeout"
    assert "timeout" in payload["stderr"].lower()
    assert "RESOURCE_EXHAUSTED (code 429)" in payload["stderr"]
    assert "empty-output" not in payload["stderr"]


def test_runtime_timeout_forced_windows_cleanup_sweeps_descendants_by_parent_pid(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    taskkill_log = tmp_path / "taskkill.log"
    powershell_log = tmp_path / "powershell.log"
    taskkill = fake_bin / "taskkill.exe"
    taskkill.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(taskkill_log))}\n"
        "exit 0\n",
        encoding="utf8",
    )
    taskkill.chmod(0o755)
    powershell = fake_bin / "powershell.exe"
    powershell.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(powershell_log))}\n"
        "exit 0\n",
        encoding="utf8",
    )
    powershell.chmod(0o755)
    env = sanitized_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ANTIGRAVITY_FOR_CODEX_TEST_FORCE_WINDOWS_TREE_CLEANUP"] = "1"
    source = """
import { runCommand } from './plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs';
const result = runCommand(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { timeout: 50, env: process.env });
process.stdout.write(JSON.stringify(result));
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["errorCode"] == "ETIMEDOUT"
    assert taskkill_log.exists()
    assert powershell_log.exists()
    powershell_args = powershell_log.read_text(encoding="utf8")
    assert "ParentProcessId = $parent" in powershell_args
    assert "Stop-Process -Id $childPid" in powershell_args


def test_runtime_sync_timeout_cleans_posix_descendant_process_group(tmp_path):
    if os.name == "nt":
        return
    descendant_pid_file = tmp_path / "agy-descendant.pid"
    agy = descendant_spawning_agy(tmp_path, descendant_pid_file)
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(agy)
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "const preflight = r.antigravityPreflight(process.env, {timeout: 2000});"
        "if (!preflight.ok) throw new Error(preflight.error || 'preflight failed');"
        "const result = r.antigravityPrint('timeout check', {preflight, timeout: 100}, process.env);"
        "process.stdout.write(JSON.stringify(result));"
    )

    result = run_node_eval(source, env)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["errorCode"] == "ETIMEDOUT"
    assert descendant_pid_file.exists()
    descendant_pid = int(descendant_pid_file.read_text(encoding="utf8"))
    try:
        assert wait_for_process_exit(descendant_pid, timeout=3)
    finally:
        if process_is_running(descendant_pid):
            subprocess.run(["kill", "-KILL", str(descendant_pid)], capture_output=True, text=True)


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


def test_runtime_log_diagnostic_redacts_emails_and_paths():
    source = """
const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');
const log = 'E0611 agent executor error: RESOURCE_EXHAUSTED (code 429): user dev@example.com in /Users/example/My Project/file.js and C:\\\\Projects\\\\secret\\\\file.js and ~/local/token\\n';
process.stdout.write(r.antigravityLogDiagnostic(log));
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr
    assert "[redacted-email]" in result.stdout
    assert "[redacted-path]" in result.stdout
    assert "dev@example.com" not in result.stdout
    assert "/Users/example" not in result.stdout
    assert "C:\\Projects" not in result.stdout
    assert "~/local" not in result.stdout


def test_agy_outcome_classifier_covers_quota_auth_invalid_stream_and_success():
    source = """
import { classifyAgyOutcome } from './plugins/antigravity-for-codex/scripts/lib/agy-outcome.mjs';
const cases = [
  [{ status: 0, stdout: 'ALLOW: ok', stderr: '', errorCode: '' }, '', 'success'],
  [{ status: 0, stdout: 'ALLOW: ok', stderr: 'warning: quota something', errorCode: '' }, '', 'success'],
  [{ status: 0, stdout: 'ALLOW: ok', stderr: 'warning: previous stream timed out but recovered', errorCode: '' }, '', 'success'],
  [{ status: 0, stdout: 'ALLOW: ok', stderr: '', errorCode: 'ETIMEDOUT' }, '', 'timeout'],
  [{ status: 0, stdout: '', stderr: '', errorCode: '' }, 'RESOURCE_EXHAUSTED (code 429): Individual quota reached.', 'quota'],
  [{ status: 0, stdout: '', stderr: 'Invalid stream: malformed tool call', errorCode: '' }, '', 'malformed-output'],
  [{ status: 0, stdout: '', stderr: 'Invalid stream: empty response', errorCode: '' }, '', 'invalid-stream'],
  [{ status: 1, stdout: '', stderr: 'You are not logged into Antigravity', errorCode: '' }, '', 'auth'],
  [{ status: 1, stdout: '', stderr: '', error: 'spawn ETIMEDOUT', errorCode: 'ETIMEDOUT' }, '', 'timeout']
];
for (const [result, logDiagnostic, kind] of cases) {
  const classified = classifyAgyOutcome(result, { logDiagnostic });
  if (classified.kind !== kind) {
    throw new Error(`expected ${kind}, got ${classified.kind}: ${JSON.stringify(classified)}`);
  }
}
console.log('ok');
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr


def test_runtime_empty_output_surfaces_agy_log_diagnostic(tmp_path):
    argv_file = tmp_path / "agy-argv.json"
    agy = fake_agy(tmp_path, response="", capture_argv=argv_file)
    env = sanitized_env()
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
    env = sanitized_env()
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


def test_antigravity_job_lifecycle_key_changes_with_provider_model_and_fingerprint(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "a.txt").write_text("one\n", encoding="utf8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "a.txt").write_text("two\n", encoding="utf8")
    source = f"""
import {{ deriveJobIdempotencyKey }} from './plugins/antigravity-for-codex/scripts/lib/job-lifecycle.mjs';
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
const cwd = {json.dumps(str(repo))};
const fp = worktreeFingerprint(cwd, {{ env: process.env }});
if (!fp.fingerprint || !fp.status) throw new Error('missing fingerprint');
const one = deriveJobIdempotencyKey({{
  command: 'review',
  args: ['review current diff'],
  cwd,
  workspaceFingerprint: fp.fingerprint,
  executionControls: {{ provider: 'gemini', model: 'Gemini 3.1 Pro (High)' }}
}});
const two = deriveJobIdempotencyKey({{
  command: 'review',
  args: ['review current diff'],
  cwd,
  workspaceFingerprint: fp.fingerprint,
  executionControls: {{ provider: 'claude', model: 'Claude Sonnet 4.6 (Thinking)' }}
}});
const fingerprintOne = deriveJobIdempotencyKey({{
  command: 'review',
  args: ['review current diff'],
  cwd,
  workspaceFingerprint: 'fingerprint-one',
  executionControls: {{ provider: 'gemini', model: 'Gemini 3.1 Pro (High)' }}
}});
const fingerprintTwo = deriveJobIdempotencyKey({{
  command: 'review',
  args: ['review current diff'],
  cwd,
  workspaceFingerprint: 'fingerprint-two',
  executionControls: {{ provider: 'gemini', model: 'Gemini 3.1 Pro (High)' }}
}});
if (one === two) throw new Error('provider/model did not affect idempotency');
if (fingerprintOne === fingerprintTwo) throw new Error('workspace fingerprint did not affect idempotency');
console.log(JSON.stringify({{ one, two, status: fp.status }}));
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr


def test_antigravity_lifecycle_classifies_queued_running_lost_terminal():
    source = """
import { classifyJobLiveness } from './plugins/antigravity-for-codex/scripts/lib/job-lifecycle.mjs';
const now = Date.parse('2026-06-12T00:00:00.000Z');
const queued = classifyJobLiveness({ status: 'queued', createdAt: '2026-06-11T23:59:55.000Z' }, { now });
const lost = classifyJobLiveness({ status: 'running', startedAt: '2026-06-11T23:00:00.000Z', lastHeartbeatAt: '2026-06-11T23:00:00.000Z' }, { now });
const done = classifyJobLiveness({ status: 'succeeded' }, { now });
if (queued.state !== 'queued') throw new Error('queued wrong');
if (lost.state !== 'lost') throw new Error('lost wrong');
if (done.state !== 'terminal') throw new Error('terminal wrong');
console.log('ok');
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr


def test_antigravity_lifecycle_queued_age_uses_queue_transition_time():
    source = """
import { classifyJobLiveness } from './plugins/antigravity-for-codex/scripts/lib/job-lifecycle.mjs';
const now = Date.parse('2026-06-12T00:00:00.000Z');
const queued = classifyJobLiveness({
  status: 'queued',
  createdAt: '2026-06-11T23:00:00.000Z',
  updatedAt: '2026-06-11T23:59:55.000Z'
}, { now });
const reserved = classifyJobLiveness({
  status: 'reserved',
  createdAt: '2026-06-11T23:00:00.000Z',
  updatedAt: '2026-06-11T23:59:55.000Z'
}, { now });
if (queued.state !== 'queued' || queued.staleForMs !== 5000) throw new Error(JSON.stringify(queued));
if (reserved.state !== 'lost') throw new Error(JSON.stringify(reserved));
console.log('ok');
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr


def test_antigravity_lifecycle_running_without_signals_uses_created_at_for_loss():
    source = """
import { classifyJobLiveness } from './plugins/antigravity-for-codex/scripts/lib/job-lifecycle.mjs';
const now = Date.parse('2026-06-12T00:00:00.000Z');
const lost = classifyJobLiveness({
  status: 'running',
  createdAt: '2026-06-11T23:30:00.000Z',
  startedAt: '',
  lastHeartbeatAt: '',
  lastProgressAt: ''
}, { now });
if (lost.state !== 'lost') throw new Error(JSON.stringify(lost));
if (lost.staleForMs !== 30 * 60 * 1000) throw new Error(JSON.stringify(lost));
console.log('ok');
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr


def test_antigravity_lifecycle_job_heartbeat_interval_uses_planned_env_name():
    source = """
import { JOB_HEARTBEAT_INTERVAL_MS, jobHeartbeatIntervalMs } from './plugins/antigravity-for-codex/scripts/lib/job-lifecycle.mjs';
if (jobHeartbeatIntervalMs({ ANTIGRAVITY_FOR_CODEX_JOB_HEARTBEAT_INTERVAL_MS: '100' }) !== 100) {
  throw new Error('planned heartbeat env was ignored');
}
if (jobHeartbeatIntervalMs({ ANTIGRAVITY_FOR_CODEX_HEARTBEAT_INTERVAL_MS: '100' }) !== JOB_HEARTBEAT_INTERVAL_MS) {
  throw new Error('legacy heartbeat env should not control job heartbeat interval');
}
console.log('ok');
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr


def test_antigravity_lifecycle_helpers_parse_and_cap_env_values():
    source = """
import {
  JOB_HEARTBEAT_INTERVAL_MS,
  TERMINAL_JOB_STATUSES,
  gitSignalTimeoutMs,
  isTerminalJobStatus,
  jobHeartbeatIntervalMs,
  maxActiveJobs,
  parsePositiveInteger
} from './plugins/antigravity-for-codex/scripts/lib/job-lifecycle.mjs';
if (parsePositiveInteger('7.9', 1) !== 7) throw new Error('expected truncation');
if (parsePositiveInteger('bad', 11) !== 11) throw new Error('expected invalid fallback');
if (parsePositiveInteger('0', 11, { min: 1 }) !== 11) throw new Error('expected min fallback');
if (parsePositiveInteger('999', 11, { max: 32 }) !== 32) throw new Error('expected max cap');
if (gitSignalTimeoutMs({ ANTIGRAVITY_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS: '99' }) !== 10000) {
  throw new Error('git timeout below min should fall back');
}
if (gitSignalTimeoutMs({ ANTIGRAVITY_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS: '999999' }) !== 60000) {
  throw new Error('git timeout should cap');
}
if (jobHeartbeatIntervalMs({ ANTIGRAVITY_FOR_CODEX_JOB_HEARTBEAT_INTERVAL_MS: '99' }) !== JOB_HEARTBEAT_INTERVAL_MS) {
  throw new Error('heartbeat below min should fall back');
}
if (jobHeartbeatIntervalMs({ ANTIGRAVITY_FOR_CODEX_JOB_HEARTBEAT_INTERVAL_MS: '999999' }) !== 5 * 60 * 1000) {
  throw new Error('heartbeat should cap');
}
if (maxActiveJobs({}) !== 3) throw new Error('maxActiveJobs default wrong');
if (maxActiveJobs({ ANTIGRAVITY_FOR_CODEX_MAX_ACTIVE_JOBS: '9' }) !== 9) throw new Error('maxActiveJobs env wrong');
if (maxActiveJobs({ ANTIGRAVITY_FOR_CODEX_MAX_ACTIVE_JOBS: '99' }) !== 32) throw new Error('maxActiveJobs cap wrong');
if (!Object.isFrozen(TERMINAL_JOB_STATUSES)) throw new Error('terminal statuses should be frozen');
if (!TERMINAL_JOB_STATUSES.includes('succeeded') || !TERMINAL_JOB_STATUSES.includes('cancel_failed')) {
  throw new Error('terminal status exports missing expected values');
}
if (!TERMINAL_JOB_STATUSES.every(isTerminalJobStatus)) throw new Error('exported terminal status not recognized');
if (isTerminalJobStatus('running') || isTerminalJobStatus('')) throw new Error('non-terminal status recognized');
console.log('ok');
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr


def test_antigravity_worktree_fingerprint_changes_for_same_stat_tracked_edits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    target = repo / "same.txt"
    target.write_text("aa\n", encoding="utf8")
    subprocess.run(["git", "add", "same.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    target.write_text("bb\n", encoding="utf8")
    source_one = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(repo))}, {{ env: process.env }})));
"""
    first = run_node_eval(source_one)
    assert first.returncode == 0, first.stderr
    target.write_text("cc\n", encoding="utf8")
    second = run_node_eval(source_one)
    assert second.returncode == 0, second.stderr
    assert json.loads(first.stdout)["fingerprint"] != json.loads(second.stdout)["fingerprint"]


def test_antigravity_worktree_fingerprint_changes_for_same_stat_staged_edits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    target = repo / "same.txt"
    target.write_text("aa\n", encoding="utf8")
    subprocess.run(["git", "add", "same.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    target.write_text("bb\n", encoding="utf8")
    subprocess.run(["git", "add", "same.txt"], cwd=repo, check=True)
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(repo))}, {{ env: process.env }})));
"""
    first = run_node_eval(source)
    assert first.returncode == 0, first.stderr
    target.write_text("cc\n", encoding="utf8")
    subprocess.run(["git", "add", "same.txt"], cwd=repo, check=True)
    second = run_node_eval(source)
    assert second.returncode == 0, second.stderr
    assert json.loads(first.stdout)["fingerprint"] != json.loads(second.stdout)["fingerprint"]


def test_antigravity_worktree_fingerprint_non_git_is_not_trusted(tmp_path):
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(tmp_path))}, {{ env: process.env }})));
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["nonGit"] is True
    assert payload["status"] != "trusted"


def test_antigravity_worktree_fingerprint_non_timeout_git_failure_is_inconclusive(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    real_git = shutil.which("git")
    assert real_git
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$arg\" = \"status\" ]; then\n"
        "    echo 'forced status failure' >&2\n"
        "    exit 42\n"
        "  fi\n"
        "done\n"
        f"exec {real_git!r} \"$@\"\n",
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    env = sanitized_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(repo))}, {{ env: process.env }})));
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "inconclusive"
    assert payload["untrusted"] is True
    assert payload["failureKind"] == "inconclusive"
    assert "forced status failure" in payload["text"]


def test_antigravity_worktree_fingerprint_disables_git_color_output(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    target = repo / "colored.txt"
    target.write_text("one\n", encoding="utf8")
    subprocess.run(["git", "add", "colored.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "color.ui", "always"], cwd=repo, check=True)
    subprocess.run(["git", "config", "color.status", "always"], cwd=repo, check=True)
    subprocess.run(["git", "config", "color.diff", "always"], cwd=repo, check=True)
    target.write_text("two\n", encoding="utf8")
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(repo))}, {{ env: process.env }})));
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "\x1b[" not in payload["text"]


def test_antigravity_worktree_fingerprint_pins_git_locale():
    text = (PLUGIN / "scripts" / "lib" / "worktree-fingerprint.mjs").read_text(encoding="utf8")
    assert 'env.LANG = "C"' in text
    assert 'env.LC_ALL = "C"' in text
    assert 'env.LC_MESSAGES = "C"' in text


def test_antigravity_worktree_fingerprint_disables_textconv_filters(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    marker = tmp_path / "textconv-was-run"
    textconv = tmp_path / "textconv.sh"
    textconv.write_text(
        "#!/bin/sh\n"
        f"printf textconv > {str(marker)!r}\n"
        "cat \"$1\"\n",
        encoding="utf8",
    )
    textconv.chmod(0o755)
    (repo / ".gitattributes").write_text("*.spy diff=spy\n", encoding="utf8")
    target = repo / "secret.spy"
    target.write_text("one\n", encoding="utf8")
    subprocess.run(["git", "config", "diff.spy.textconv", str(textconv)], cwd=repo, check=True)
    subprocess.run(["git", "add", ".gitattributes", "secret.spy"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    target.write_text("two\n", encoding="utf8")
    subprocess.run(["git", "add", "secret.spy"], cwd=repo, check=True)
    target.write_text("three\n", encoding="utf8")
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(repo))}, {{ env: process.env }})));
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["fingerprint"]
    assert payload["hash"] == payload["fingerprint"]
    assert payload["status"] in {"trusted", "inconclusive"}
    assert "git diff --no-color --no-ext-diff --no-textconv --stat" in payload["text"]
    assert not marker.exists()


def test_antigravity_worktree_fingerprint_disables_fsmonitor_helper(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    marker = tmp_path / "fsmonitor-was-run"
    fsmonitor = tmp_path / "fsmonitor.sh"
    fsmonitor.write_text(
        "#!/bin/sh\n"
        f"printf fsmonitor > {str(marker)!r}\n"
        "exit 1\n",
        encoding="utf8",
    )
    fsmonitor.chmod(0o755)
    target = repo / "tracked.txt"
    target.write_text("one\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "core.fsmonitor", str(fsmonitor)], cwd=repo, check=True)
    target.write_text("two\n", encoding="utf8")
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(repo))}, {{ env: process.env }})));
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["fingerprint"]
    assert payload["hash"] == payload["fingerprint"]
    assert payload["status"] in {"trusted", "inconclusive"}
    assert not marker.exists()


def test_antigravity_worktree_fingerprint_ignores_ambient_git_repository_env(tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    init_git_repo(repo_a)
    init_git_repo(repo_b)
    (repo_a / "repo-a-unique.txt").write_text("repo a base\n", encoding="utf8")
    (repo_b / "repo-b-unique.txt").write_text("repo b base\n", encoding="utf8")
    subprocess.run(["git", "add", "repo-a-unique.txt"], cwd=repo_a, check=True)
    subprocess.run(["git", "commit", "-m", "repo a base"], cwd=repo_a, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "repo-b-unique.txt"], cwd=repo_b, check=True)
    subprocess.run(["git", "commit", "-m", "repo b base"], cwd=repo_b, check=True, capture_output=True, text=True)
    (repo_a / "repo-a-unique.txt").write_text("repo a changed\n", encoding="utf8")
    (repo_b / "repo-b-unique.txt").write_text("repo b changed\n", encoding="utf8")
    repo_a_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_a, check=True, capture_output=True, text=True).stdout.strip()
    repo_b_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_b, check=True, capture_output=True, text=True).stdout.strip()
    env = sanitized_env()
    env.update({
        "GIT_DIR": str(repo_b / ".git"),
        "GIT_WORK_TREE": str(repo_b),
        "GIT_INDEX_FILE": str(repo_b / ".git" / "index"),
        "GIT_COMMON_DIR": str(repo_b / ".git"),
        "GIT_OBJECT_DIRECTORY": str(repo_b / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(repo_b / ".git" / "objects"),
        "GIT_NAMESPACE": "poison",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "false",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(repo_b / ".gitconfig"),
        "GIT_CONFIG_SYSTEM": str(repo_b / ".gitconfig-system"),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.worktree",
        "GIT_CONFIG_VALUE_0": str(repo_b),
    })
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(repo_a))}, {{ env: process.env }})));
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "trusted"
    assert repo_a_head in payload["text"]
    assert repo_b_head not in payload["text"]
    assert "repo-a-unique.txt" in payload["text"]
    assert "repo-b-unique.txt" not in payload["text"]


def test_antigravity_state_dir_ignores_ambient_git_repository_env(tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    init_git_repo(repo_a)
    init_git_repo(repo_b)
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    env.update({
        "GIT_DIR": str(repo_b / ".git"),
        "GIT_WORK_TREE": str(repo_b),
        "GIT_INDEX_FILE": str(repo_b / ".git" / "index"),
        "GIT_COMMON_DIR": str(repo_b / ".git"),
        "GIT_OBJECT_DIRECTORY": str(repo_b / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(repo_b / ".git" / "objects"),
        "GIT_NAMESPACE": "poison",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "false",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.worktree",
        "GIT_CONFIG_VALUE_0": str(repo_b),
    })
    source = f"""
import path from 'node:path';
import {{ stateDirForCwd }} from './plugins/antigravity-for-codex/scripts/lib/state.mjs';
const dir = stateDirForCwd({json.dumps(str(repo_a))}, process.env);
process.stdout.write(JSON.stringify({{ dir, base: path.basename(dir) }}));
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["base"].startswith("repo-a-")
    assert not payload["base"].startswith("repo-b-")


def test_background_job_state_uses_hardened_workspace_slug_under_poisoned_git_env(tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    init_git_repo(repo_a)
    init_git_repo(repo_b)
    (repo_a / "tracked.txt").write_text("base\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo_a, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo_a, check=True, capture_output=True, text=True)
    (repo_a / "tracked.txt").write_text("changed\n", encoding="utf8")
    (repo_b / "repo-b-unique.txt").write_text("repo b\n", encoding="utf8")
    env = companion_env(tmp_path, fake_agy(tmp_path, response="BACKGROUND_OK"))
    env.update({
        "GIT_DIR": str(repo_b / ".git"),
        "GIT_WORK_TREE": str(repo_b),
        "GIT_INDEX_FILE": str(repo_b / ".git" / "index"),
        "GIT_COMMON_DIR": str(repo_b / ".git"),
        "GIT_OBJECT_DIRECTORY": str(repo_b / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(repo_b / ".git" / "objects"),
        "GIT_NAMESPACE": "poison",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.worktree",
        "GIT_CONFIG_VALUE_0": str(repo_b),
    })

    queued = run_companion(["review", "--background", "poisoned state"], repo_a, env)

    assert queued.returncode == 0, queued.stderr
    job_id = json.loads(queued.stdout)["jobId"]
    final_status = wait_for_job(repo_a, env, job_id, timeout=5)
    assert final_status["status"] == "succeeded"
    job_files = list((tmp_path / "state").rglob(f"{job_id}.json"))
    assert len(job_files) == 1
    assert any(part.startswith("repo-a-") for part in job_files[0].parts)
    assert not any(part.startswith("repo-b-") for part in job_files[0].parts)


def test_antigravity_worktree_fingerprint_untracked_file_count_budget_is_inconclusive(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "one.txt").write_text("one\n", encoding="utf8")
    (repo / "two.txt").write_text("two\n", encoding="utf8")
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_FILES"] = "1"
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(repo))}, {{ env: process.env }})));
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "inconclusive"
    assert payload["untrusted"] is True
    assert payload["budgetExceeded"] is True
    assert "UNTRACKED_FINGERPRINT_BUDGET_EXCEEDED" in payload["text"]
    assert "files=2 maxFiles=1" in payload["text"]


def test_antigravity_worktree_fingerprint_untracked_byte_budget_is_inconclusive(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "large-untracked.txt").write_text("abcdef\n", encoding="utf8")
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_BYTES"] = "3"
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(repo))}, {{ env: process.env }})));
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "inconclusive"
    assert payload["untrusted"] is True
    assert payload["budgetExceeded"] is True
    assert "UNTRACKED_FINGERPRINT_BUDGET_EXCEEDED" in payload["text"]
    assert "file=large-untracked.txt" in payload["text"]
    assert "remainingBytes=3" in payload["text"]


def test_antigravity_worktree_fingerprint_sigkills_term_ignoring_git(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "trap '' TERM\n"
        "sleep 3\n"
        "echo 'fake git should have been killed' >&2\n",
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    env = sanitized_env()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ANTIGRAVITY_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS"] = "100"
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(tmp_path))}, {{ env: process.env }})));
"""
    started = time.monotonic()
    result = run_node_eval(source, env)
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    assert elapsed < 2
    payload = json.loads(result.stdout)
    assert payload["timedOut"] is True
    assert payload["status"] == "inconclusive"
    assert payload["failureKind"] == "timeout"


def test_antigravity_worktree_fingerprint_hashes_repo_root_untracked_from_subdir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    subdir = repo / "subdir"
    subdir.mkdir()
    (subdir / "tracked.txt").write_text("tracked\n", encoding="utf8")
    subprocess.run(["git", "add", "subdir/tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    root_untracked = repo / "root.txt"
    root_untracked.write_text("one\n", encoding="utf8")
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(subdir))}, {{ env: process.env }})));
"""
    first = run_node_eval(source)
    assert first.returncode == 0, first.stderr
    root_untracked.write_text("two\n", encoding="utf8")
    second = run_node_eval(source)
    assert second.returncode == 0, second.stderr
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["status"] != "trusted" or first_payload["fingerprint"] != second_payload["fingerprint"]


def test_antigravity_worktree_fingerprint_changes_for_untracked_executable_mode(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    tracked = repo / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    target = repo / "script.sh"
    target.write_text("#!/bin/sh\necho same\n", encoding="utf8")
    target.chmod(0o644)
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
process.stdout.write(JSON.stringify(worktreeFingerprint({json.dumps(str(repo))}, {{ env: process.env }})));
"""
    first = run_node_eval(source)
    assert first.returncode == 0, first.stderr
    target.chmod(0o755)
    second = run_node_eval(source)
    assert second.returncode == 0, second.stderr
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["fingerprint"] != second_payload["fingerprint"]
    assert '"mode":"100644"' in first_payload["text"]
    assert '"mode":"100755"' in second_payload["text"]


def test_antigravity_worktree_fingerprint_is_invariant_from_subdir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    subdir = repo / "subdir"
    subdir.mkdir()
    (repo / "tracked-root.txt").write_text("tracked\n", encoding="utf8")
    (subdir / "tracked-subdir.txt").write_text("subdir\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked-root.txt", "subdir/tracked-subdir.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "tracked-root.txt").write_text("tracked changed\n", encoding="utf8")
    (repo / "untracked-root.txt").write_text("untracked\n", encoding="utf8")
    source = f"""
import {{ worktreeFingerprint }} from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
const root = worktreeFingerprint({json.dumps(str(repo))}, {{ env: process.env }});
const subdir = worktreeFingerprint({json.dumps(str(subdir))}, {{ env: process.env }});
process.stdout.write(JSON.stringify({{ root, subdir }}));
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["root"]["fingerprint"] == payload["subdir"]["fingerprint"]
    assert payload["root"]["status"] == payload["subdir"]["status"]
    assert payload["root"]["text"] == payload["subdir"]["text"]


def test_antigravity_untracked_fingerprint_trust_is_content_based():
    source = """
import { isTrustedUntrackedFingerprint } from './plugins/antigravity-for-codex/scripts/lib/worktree-fingerprint.mjs';
const trusted = [
  { type: 'file', size: 3, mode: '100644', sha256: 'abc' },
  { type: 'file', size: 3, mode: '100755', sha256: 'abc' }
];
const untrusted = [
  { type: 'file', size: 3, sha256: 'abc' },
  { type: 'file', size: 3, mode: '100600', sha256: 'abc' },
  { type: 'symlink', target: '../target' },
  { type: 'budget-exceeded', size: 2, remainingBytes: 1 },
  { type: 'file-large', size: 1048577, mtimeMs: 1 },
  { type: 'error', errorCode: 'EACCES' },
  { type: 'outside-workspace' },
  { type: 'other', size: 0, mtimeMs: 1 }
];
if (!trusted.every(isTrustedUntrackedFingerprint)) throw new Error('expected trusted content fingerprints');
if (untrusted.some(isTrustedUntrackedFingerprint)) throw new Error('expected skipped fingerprints to be untrusted');
"""
    result = run_node_eval(source)
    assert result.returncode == 0, result.stderr


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
    env = sanitized_env()
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
    assert report_payload["outcome"] == "success"
    assert report_payload["retryable"] is False
    assert report_payload["stdoutBytes"] > 0
    assert report_payload["stderrBytes"] == 0
    assert "stdout" not in report_payload
    assert "stderr" not in report_payload
    assert "rawOutput" not in report_payload

    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert "Return only one JSON object" in prompt
    assert '"verdict"' in prompt
    assert "approve" in prompt
    assert "Git context:" in prompt
    assert "Do not edit files" in prompt


def test_failed_review_writes_latest_sanitized_report(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path / "success", response="AGY_REVIEW_OK"))

    success = subprocess.run([NODE, str(runtime), "review", "first"], cwd=repo, env=env, capture_output=True, text=True)
    assert success.returncode == 0, success.stderr
    first_report = subprocess.run([NODE, str(runtime), "report", "--latest"], cwd=repo, env=env, capture_output=True, text=True)
    assert first_report.returncode == 0, first_report.stderr
    assert json.loads(first_report.stdout)["outcome"] == "success"

    time.sleep(0.02)
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path / "failure", response=""))
    env["FAKE_AGY_LOG"] = (
        "E0611 agent executor error: RESOURCE_EXHAUSTED (code 429): Individual quota reached. "
        "Resets in 38h51m45s.\n"
    )
    failed = subprocess.run([NODE, str(runtime), "review", "second"], cwd=repo, env=env, capture_output=True, text=True)
    assert failed.returncode == 1
    assert "RESOURCE_EXHAUSTED (code 429)" in failed.stderr

    latest = subprocess.run([NODE, str(runtime), "report", "--latest"], cwd=repo, env=env, capture_output=True, text=True)
    assert latest.returncode == 0, latest.stderr
    report_payload = json.loads(latest.stdout)
    assert report_payload["command"] == "review"
    assert report_payload["status"] == 1
    assert report_payload["outcome"] == "quota"
    assert report_payload["retryable"] is True
    assert report_payload["stdoutBytes"] == 0
    assert report_payload["stderrBytes"] > 0
    assert "stdout" not in report_payload
    assert "stderr" not in report_payload
    assert "rawOutput" not in report_payload


def test_preflight_failed_review_writes_latest_sanitized_report(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path / "success", response="AGY_REVIEW_OK"))

    success = subprocess.run([NODE, str(runtime), "review", "first"], cwd=repo, env=env, capture_output=True, text=True)
    assert success.returncode == 0, success.stderr
    assert json.loads(subprocess.run(
        [NODE, str(runtime), "report", "--latest"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    ).stdout)["outcome"] == "success"

    time.sleep(0.02)
    help_without_print_timeout = FAKE_AGY_HELP.replace("  --print-timeout\n", "")
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path / "preflight-failure", help_text=help_without_print_timeout))
    failed = subprocess.run([NODE, str(runtime), "review", "second"], cwd=repo, env=env, capture_output=True, text=True)
    assert failed.returncode == 2
    assert "--print-timeout" in failed.stderr

    latest = subprocess.run([NODE, str(runtime), "report", "--latest"], cwd=repo, env=env, capture_output=True, text=True)
    assert latest.returncode == 0, latest.stderr
    report_payload = json.loads(latest.stdout)
    assert report_payload["command"] == "review"
    assert report_payload["status"] == 2
    assert report_payload["outcome"] == "provider-error"
    assert report_payload["retryable"] is False
    assert report_payload["stdoutBytes"] == 0
    assert report_payload["stderrBytes"] > 0
    assert "stdout" not in report_payload
    assert "stderr" not in report_payload
    assert "rawOutput" not in report_payload


def test_parse_failed_review_writes_latest_sanitized_report(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path / "success", response="AGY_REVIEW_OK"))

    success = subprocess.run([NODE, str(runtime), "review", "first"], cwd=repo, env=env, capture_output=True, text=True)
    assert success.returncode == 0, success.stderr
    assert json.loads(subprocess.run(
        [NODE, str(runtime), "report", "--latest"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    ).stdout)["outcome"] == "success"

    time.sleep(0.02)
    failed = subprocess.run([NODE, str(runtime), "review", "--model-provider", "bad", "second"], cwd=repo, env=env, capture_output=True, text=True)
    assert failed.returncode == 2
    assert "Invalid --model-provider" in failed.stderr

    latest = subprocess.run([NODE, str(runtime), "report", "--latest"], cwd=repo, env=env, capture_output=True, text=True)
    assert latest.returncode == 0, latest.stderr
    report_payload = json.loads(latest.stdout)
    assert report_payload["command"] == "review"
    assert report_payload["status"] == 2
    assert report_payload["outcome"] == "provider-error"
    assert report_payload["retryable"] is False
    assert report_payload["stdoutBytes"] == 0
    assert report_payload["stderrBytes"] > 0
    assert "stdout" not in report_payload
    assert "stderr" not in report_payload
    assert "rawOutput" not in report_payload


def test_structured_failed_review_writes_latest_sanitized_report(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_STATE_HOME"] = str(tmp_path / "state")
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path / "success", response="AGY_REVIEW_OK"))

    success = subprocess.run([NODE, str(runtime), "review", "first"], cwd=repo, env=env, capture_output=True, text=True)
    assert success.returncode == 0, success.stderr
    assert json.loads(subprocess.run(
        [NODE, str(runtime), "report", "--latest"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    ).stdout)["outcome"] == "success"

    time.sleep(0.02)
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path / "structured-failure", response="not json"))
    failed = subprocess.run([NODE, str(runtime), "review", "--structured", "second"], cwd=repo, env=env, capture_output=True, text=True)
    assert failed.returncode == 1
    assert "Structured review output invalid" in failed.stderr

    latest = subprocess.run([NODE, str(runtime), "report", "--latest"], cwd=repo, env=env, capture_output=True, text=True)
    assert latest.returncode == 0, latest.stderr
    report_payload = json.loads(latest.stdout)
    assert report_payload["command"] == "review"
    assert report_payload["status"] == 1
    assert report_payload["outcome"] == "malformed-output"
    assert report_payload["retryable"] is True
    assert report_payload["stdoutBytes"] > 0
    assert report_payload["stderrBytes"] > 0
    assert "stdout" not in report_payload
    assert "stderr" not in report_payload
    assert "rawOutput" not in report_payload


def test_review_json_implies_structured_output(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    response = json.dumps({"verdict": "approve", "summary": "ok", "findings": [], "next_steps": []})
    env = sanitized_env()
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
    env = sanitized_env()
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
    env = sanitized_env()
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
    env = sanitized_env()
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
    env = sanitized_env()
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
    env = sanitized_env()
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
    env = sanitized_env()
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
    env = sanitized_env()
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
    env = sanitized_env()
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
    assert "[git output truncated or timed out for: git diff --no-ext-diff --no-textconv -- .]" in prompt


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


def test_background_review_reuses_same_idempotency_key_and_changes_on_model(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "tracked.txt").write_text("one\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "tracked.txt").write_text("two\n", encoding="utf8")
    env = companion_env(tmp_path, fake_agy(tmp_path, response="REUSED_OK", delay_ms=1200))

    first = run_companion(["review", "--background", "same request"], repo, env)
    second = run_companion(["review", "--background", "same request"], repo, env)
    claude_env = dict(env)
    claude_env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"
    third = run_companion(["review", "--background", "same request"], repo, claude_env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert third.returncode == 0, third.stderr
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    third_payload = json.loads(third.stdout)
    try:
        assert first_payload["jobId"] == second_payload["jobId"]
        assert second_payload["reused"] is True
        assert third_payload["jobId"] != first_payload["jobId"]
    finally:
        run_companion(["cancel", first_payload["jobId"]], repo, env)
        run_companion(["cancel", third_payload["jobId"]], repo, claude_env)


def test_concurrent_background_review_same_key_creates_one_job(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "tracked.txt").write_text("one\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "tracked.txt").write_text("two\n", encoding="utf8")
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    env = companion_env(tmp_path, fake_agy(tmp_path, response="CONCURRENT_OK", delay_ms=1200))

    runs = [
        subprocess.Popen(
            [NODE, str(runtime), "review", "--background", "same request"],
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [proc.communicate(timeout=10) + (proc.returncode,) for proc in runs]

    assert all(result[2] == 0 for result in results), results
    payloads = [json.loads(result[0]) for result in results]
    assert payloads[0]["jobId"] == payloads[1]["jobId"]
    try:
        assert len({payload["jobId"] for payload in payloads}) == 1
    finally:
        run_companion(["cancel", payloads[0]["jobId"]], repo, env)


def test_background_active_cap_rejects_new_distinct_job(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, never_exit=True))
    env["ANTIGRAVITY_FOR_CODEX_MAX_ACTIVE_JOBS"] = "1"

    first = run_companion(["review", "--background", "first active"], repo, env)
    assert first.returncode == 0, first.stderr
    first_id = json.loads(first.stdout)["jobId"]
    try:
        wait_for_job(repo, env, first_id, terminal=False)
        second = run_companion(["review", "--background", "second distinct"], repo, env)

        assert second.returncode == 2
        assert "maximum active background jobs" in second.stderr.lower()
    finally:
        run_companion(["cancel", first_id], repo, env)


def test_background_metadata_persistence_failure_does_not_start_worker(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    agy_pid_file = tmp_path / "agy.pid"
    env = companion_env(tmp_path, fake_agy(tmp_path, capture_pid=agy_pid_file, delay_ms=500))
    env["ANTIGRAVITY_FOR_CODEX_TEST_UPDATE_JOB_FAILURE"] = "1"

    result = run_companion(["review", "--background", "metadata failure"], repo, env)

    assert result.returncode == 2
    assert "metadata persistence" in result.stderr.lower()
    time.sleep(0.3)
    assert not agy_pid_file.exists()
    listing = run_companion(["jobs"], repo, env)
    assert listing.returncode == 0, listing.stderr
    jobs = json.loads(listing.stdout)["jobs"]
    active_jobs = [
        job for job in jobs
        if job["status"] in {"queued", "running"} or job["liveness"]["state"] in {"queued", "healthy", "suspect"}
    ]
    assert active_jobs == []
    if jobs:
        assert jobs[0]["status"] == "failed"
        assert jobs[0]["submissionState"] == "metadata_failed"
        assert "Metadata persistence failed" in jobs[0]["error"]


def test_result_viewed_state_survives_finish_and_unread_hook(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="VIEWED_DONE", delay_ms=100))
    hook = PLUGIN / "hooks" / "unread-result.mjs"

    queued = run_companion(["review", "--background", "viewed state"], repo, env)
    job_id = json.loads(queued.stdout)["jobId"]
    assert wait_for_job(repo, env, job_id)["status"] == "succeeded"

    first = subprocess.run([NODE, str(hook)], cwd=repo, env=env, capture_output=True, text=True)
    assert first.returncode == 0
    assert job_id in first.stderr
    viewed = run_companion(["result", job_id], repo, env)
    viewed_payload = json.loads(viewed.stdout)
    assert viewed.returncode == 0, viewed.stderr
    assert viewed_payload["viewed"] is True
    assert viewed_payload["resultViewedAt"]
    status = run_companion(["status", job_id], repo, env)
    assert json.loads(status.stdout)["resultViewedAt"] == viewed_payload["resultViewedAt"]
    second = subprocess.run([NODE, str(hook)], cwd=repo, env=env, capture_output=True, text=True)
    assert second.returncode == 0
    assert job_id not in second.stderr


def test_background_job_heartbeat_updates_while_agy_is_running(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="HEARTBEAT_OK", delay_ms=1000))
    env["ANTIGRAVITY_FOR_CODEX_JOB_HEARTBEAT_INTERVAL_MS"] = "100"

    queued = run_companion(["review", "--background", "heartbeat"], repo, env)

    assert queued.returncode == 0, queued.stderr
    job_id = json.loads(queued.stdout)["jobId"]
    running = wait_for_job(repo, env, job_id, terminal=False)
    assert running["status"] == "running"
    time.sleep(0.35)
    heartbeat = run_companion(["status", job_id], repo, env)
    payload = json.loads(heartbeat.stdout)
    assert payload["lastHeartbeatAt"]
    assert payload["liveness"]["state"] in {"healthy", "suspect"}
    assert wait_for_job(repo, env, job_id, timeout=5)["status"] == "succeeded"


def test_background_job_persists_custom_timeout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="TIMEOUT_PERSISTED", delay_ms=100))

    queued = run_companion(["review", "--background", "--timeout-seconds", "1", "custom timeout"], repo, env)

    assert queued.returncode == 0, queued.stderr
    job_id = json.loads(queued.stdout)["jobId"]
    status = run_companion(["status", job_id], repo, env)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["timeout"] == 1000
    assert wait_for_job(repo, env, job_id, timeout=5)["status"] == "succeeded"


def test_background_supervisor_cleans_descendant_after_inner_timeout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    descendant_pid_file = tmp_path / "agy-descendant.pid"
    agy = tmp_path / "agy"
    agy.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('fs');\n"
        "const { spawn } = require('child_process');\n"
        "const argv = process.argv.slice(2);\n"
        "if (argv.join(' ') === '--version') { console.log('1.0.6-fake'); process.exit(0); }\n"
        f"if (argv.join(' ') === '--help') {{ process.stdout.write({json.dumps(FAKE_AGY_HELP)}); process.exit(0); }}\n"
        f"if (argv.join(' ') === 'models') {{ process.stdout.write({json.dumps(FAKE_AGY_MODELS)}); process.exit(0); }}\n"
        "if (!argv.includes('--prompt')) { console.error('missing prompt'); process.exit(9); }\n"
        "if (!argv.includes('--model')) { console.error('missing model'); process.exit(8); }\n"
        "const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });\n"
        "child.unref();\n"
        f"fs.writeFileSync({json.dumps(str(descendant_pid_file))}, String(child.pid));\n"
        "setInterval(() => {}, 1000);\n",
        encoding="utf8",
    )
    agy.chmod(0o755)
    env = companion_env(tmp_path, agy)

    queued = run_companion(["review", "--background", "--timeout-seconds", "1", "descendant timeout"], repo, env)

    assert queued.returncode == 0, queued.stderr
    job_id = json.loads(queued.stdout)["jobId"]
    deadline = time.time() + 5
    while not descendant_pid_file.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert descendant_pid_file.exists()
    descendant_pid = int(descendant_pid_file.read_text(encoding="utf8"))
    try:
        assert process_is_running(descendant_pid)
        final_status = wait_for_job(repo, env, job_id, timeout=10)
        assert final_status["status"] == "failed"
        result_payload = json.loads(run_companion(["result", job_id], repo, env).stdout)
        assert "ETIMEDOUT" in (result_payload.get("stderr", "") + result_payload.get("error", ""))
        assert wait_for_process_exit(descendant_pid, timeout=3)
    finally:
        if process_is_running(descendant_pid):
            subprocess.run(["kill", "-KILL", str(descendant_pid)], capture_output=True, text=True)


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


def test_antigravity_doctor_is_cheap_and_reports_capabilities(tmp_path):
    agy = fake_agy(tmp_path)
    env = doctor_env(agy)
    result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["agy"]["available"] is True
    assert payload["agy"]["capabilities"]["prompt"] is True
    assert payload["agy"]["capabilities"]["logFile"] is True
    assert payload["models"]["gemini"]
    assert payload["models"]["claude"]
    assert payload["hooks"]["supportedEvents"] == ["SessionStart", "SessionEnd", "UserPromptSubmit", "Stop"]
    assert [item["event"] for item in payload["hooks"]["supported"]] == ["SessionStart", "SessionEnd", "UserPromptSubmit", "Stop"]
    assert all(item["failOpen"] is True for item in payload["hooks"]["supported"])
    assert any(item["event"] == "PermissionRequest" and item["reason"] for item in payload["hooks"]["unsupported"])
    assert "modelCall" not in payload


def test_antigravity_doctor_ignores_benign_success_stderr_in_json(tmp_path):
    agy = fake_agy(
        tmp_path,
        help_stderr="usage summary was printed on stderr",
        models_stderr="model catalog warning on stderr",
    )
    env = doctor_env(agy)
    result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["agy"]["available"] is True
    assert payload["agy"]["helpError"] == ""
    assert payload["models"]["available"] is True
    assert payload["models"]["error"] == ""


def test_antigravity_doctor_does_not_throw_when_generic_model_matches_one_provider(tmp_path):
    agy = fake_agy(tmp_path)
    env = doctor_env(agy)
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "gemini"
    env["ANTIGRAVITY_FOR_CODEX_MODEL"] = "Gemini 3.1 Pro (High)"
    result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["selected"]["current"]["modelProvider"] == "gemini"
    assert payload["selected"]["current"]["model"] == "Gemini 3.1 Pro (High)"
    assert payload["selected"]["current"]["source"] == "env-generic"
    assert payload["selected"]["providers"]["claude"]["ok"] is True


def test_antigravity_doctor_reports_invalid_provider_env_as_json(tmp_path):
    agy = fake_agy(tmp_path)
    env = doctor_env(agy)
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "openai"
    result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["agy"]["available"] is True
    assert payload["selected"]["current"]["ok"] is False
    assert "Invalid Antigravity model provider" in payload["selected"]["current"]["error"]
    assert "stack" not in result.stderr.lower()


def test_antigravity_doctor_reports_provider_model_mismatch_in_json_and_human_output(tmp_path):
    agy = fake_agy(tmp_path)
    env = doctor_env(agy)
    command = [
        NODE,
        str(PLUGIN / "scripts" / "antigravity-companion.mjs"),
        "doctor",
        "--model-provider",
        "claude",
        "--model",
        "Gemini 3.1 Pro (High)",
    ]
    json_result = subprocess.run([*command, "--json"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert json_result.returncode == 0, json_result.stderr
    payload = json.loads(json_result.stdout)
    assert payload["ok"] is False
    assert payload["selected"]["current"]["ok"] is False
    assert "requires a Claude" in payload["selected"]["current"]["error"]

    human_result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    assert human_result.returncode == 0, human_result.stderr
    assert "Ready: no" in human_result.stdout
    assert "Current selection: error" in human_result.stdout
    assert "requires a Claude" in human_result.stdout


def test_antigravity_doctor_is_not_ready_when_models_command_fails(tmp_path):
    agy = fake_agy(
        tmp_path,
        models_text=FAKE_AGY_MODELS,
        models_stderr="catalog failed",
        models_exit_code=4,
    )
    env = doctor_env(agy)

    json_result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor", "--json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert json_result.returncode == 0, json_result.stderr
    payload = json.loads(json_result.stdout)
    assert payload["ok"] is False
    assert payload["models"]["available"] is False
    assert payload["models"]["status"] == 4
    assert "catalog failed" in payload["models"]["error"]

    human_result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert human_result.returncode == 0, human_result.stderr
    assert "Ready: no" in human_result.stdout
    assert "Models error: catalog failed" in human_result.stdout


def test_antigravity_doctor_reports_stdout_only_probe_failures_in_json(tmp_path):
    help_agy = fake_agy(tmp_path / "help", help_text="help failed on stdout", help_stderr="", help_exit_code=3)
    help_result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor", "--json"],
        cwd=ROOT,
        env=doctor_env(help_agy),
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0, help_result.stderr
    help_payload = json.loads(help_result.stdout)
    assert help_payload["ok"] is False
    assert help_payload["agy"]["available"] is False
    assert help_payload["agy"]["helpStatus"] == 3
    assert help_payload["agy"]["helpError"] == "help failed on stdout"

    models_agy = fake_agy(tmp_path / "models", models_text="models failed on stdout", models_stderr="", models_exit_code=4)
    models_result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor", "--json"],
        cwd=ROOT,
        env=doctor_env(models_agy),
        capture_output=True,
        text=True,
    )
    assert models_result.returncode == 0, models_result.stderr
    models_payload = json.loads(models_result.stdout)
    assert models_payload["ok"] is False
    assert models_payload["models"]["available"] is False
    assert models_payload["models"]["status"] == 4
    assert models_payload["models"]["error"] == "models failed on stdout"


def test_antigravity_doctor_human_output_surfaces_cli_and_model_errors(tmp_path):
    help_agy = fake_agy(tmp_path / "help", help_text="", help_stderr="help failed", help_exit_code=3)
    help_result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor"],
        cwd=ROOT,
        env=doctor_env(help_agy),
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "Available: no" in help_result.stdout
    assert "Antigravity error: help failed" in help_result.stdout

    models_agy = fake_agy(tmp_path / "models", models_text="", models_stderr="models failed", models_exit_code=4)
    models_result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor"],
        cwd=ROOT,
        env=doctor_env(models_agy),
        capture_output=True,
        text=True,
    )
    assert models_result.returncode == 0, models_result.stderr
    assert "Ready: no" in models_result.stdout
    assert "Models error: models failed" in models_result.stdout
    assert "Gemini selection error: no Gemini models listed" in models_result.stdout
    assert "Claude selection error: no Claude models listed" in models_result.stdout


def test_antigravity_doctor_malformed_json_option_returns_json_error(tmp_path):
    agy = fake_agy(tmp_path)
    cases = [
        (["--model-provider", "--json"], "Missing value for --model-provider."),
        (["--bogus", "--json"], "Unknown doctor argument: --bogus."),
        (["extra", "--json"], "Unknown doctor argument: extra."),
        (["--model-provider=", "--json"], "Missing value for --model-provider."),
        (["--model=", "--json"], "Missing value for --model."),
        (["--model-provider", "", "--json"], "Missing value for --model-provider."),
        (["--model", "", "--json"], "Missing value for --model."),
    ]
    for args, expected_error in cases:
        result = subprocess.run(
            [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor", *args],
            cwd=ROOT,
            env=doctor_env(agy),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["error"] == expected_error
        assert "stack" not in result.stderr.lower()


def test_antigravity_doctor_malformed_human_option_returns_stderr(tmp_path):
    agy = fake_agy(tmp_path)
    result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "antigravity-companion.mjs"), "doctor", "extra"],
        cwd=ROOT,
        env=doctor_env(agy),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "Unknown doctor argument: extra.\n"
    assert "stack" not in result.stderr.lower()


def test_preflight_warns_when_selected_model_not_listed(tmp_path):
    agy = fake_agy(tmp_path, models_text="Gemini 3.5 Flash (High)\nClaude Sonnet 4.6 (Thinking)\n")
    env = sanitized_env()
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


def test_preflight_uses_catalog_fallback_when_default_model_is_not_listed(tmp_path):
    agy = fake_agy(tmp_path, models_text="Gemini 3.5 Flash (High)\nClaude Opus 4.6 (Thinking)\n")
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(agy)
    source = (
        "const r = await import('./plugins/antigravity-for-codex/scripts/lib/antigravity-runtime.mjs');"
        "process.stdout.write(JSON.stringify(r.antigravityPreflight(process.env)));"
    )
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["model"] == "Gemini 3.5 Flash (High)"
    assert payload["modelCatalog"]["selectedModelListed"] is True
    assert payload["modelCatalog"]["count"] == 2


def test_reserved_job_lifecycle_is_explicit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="RESERVED_OK", delay_ms=100))

    reserved = run_companion(["reserve-job", "review", "--background", "--background=ignored", "--timeout-seconds", "1", "reserved focus"], repo, env)

    assert reserved.returncode == 0, reserved.stderr
    reserved_payload = json.loads(reserved.stdout)
    assert reserved_payload["status"] == "reserved"
    job_id = reserved_payload["jobId"]
    status = run_companion(["status", job_id], repo, env)
    status_payload = json.loads(status.stdout)
    assert status_payload["status"] == "reserved"
    assert status_payload["timeout"] == 1000
    assert "--background" not in status_payload["args"]
    assert "--background=ignored" not in status_payload["args"]

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


def test_reserved_job_reuses_same_key_and_respects_active_cap(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="RESERVED_OK", delay_ms=100))
    env["ANTIGRAVITY_FOR_CODEX_MAX_ACTIVE_JOBS"] = "1"

    first = run_companion(["reserve-job", "review", "same reserved"], repo, env)
    same = run_companion(["reserve-job", "review", "same reserved"], repo, env)
    distinct = run_companion(["reserve-job", "review", "different reserved"], repo, env)

    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    assert first_payload["status"] == "reserved"
    same_payload = json.loads(same.stdout)
    assert same.returncode == 0, same.stderr
    assert same_payload["reused"] is True
    assert same_payload["jobId"] == first_payload["jobId"]
    assert distinct.returncode == 2
    assert "maximum active background jobs (1) reached" in distinct.stderr


def test_run_reserved_job_rechecks_active_cap_before_start(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="RESERVED_OK", delay_ms=800))
    env["ANTIGRAVITY_FOR_CODEX_MAX_ACTIVE_JOBS"] = "2"
    first = run_companion(["reserve-job", "review", "first reserved"], repo, env)
    second = run_companion(["reserve-job", "review", "second reserved"], repo, env)
    first_id = json.loads(first.stdout)["jobId"]
    second_id = json.loads(second.stdout)["jobId"]

    started_first = run_companion(["run-reserved-job", first_id], repo, env)
    capped_env = dict(env)
    capped_env["ANTIGRAVITY_FOR_CODEX_MAX_ACTIVE_JOBS"] = "1"
    rejected_second = run_companion(["run-reserved-job", second_id], repo, capped_env)

    assert started_first.returncode == 0, started_first.stderr
    assert rejected_second.returncode == 2
    assert "maximum active background jobs (1) reached" in rejected_second.stderr
    assert json.loads(run_companion(["status", second_id], repo, env).stdout)["status"] == "reserved"
    assert wait_for_job(repo, env, first_id)["status"] == "succeeded"


def test_reserved_job_cancel_is_metadata_only(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="RESERVED_OK", delay_ms=100))

    reserved = run_companion(["reserve-job", "review", "reserved cancel"], repo, env)
    assert reserved.returncode == 0, reserved.stderr
    job_id = json.loads(reserved.stdout)["jobId"]

    cancelled = run_companion(["cancel", job_id], repo, env)

    assert cancelled.returncode == 0, cancelled.stderr
    payload = json.loads(cancelled.stdout)
    assert payload["status"] == "cancelled"
    assert payload["cancel"]["status"] == "not_running"
    assert "missing trusted worker identity" not in payload.get("error", "")


def test_reserved_job_cancel_uses_recorded_worker_pid_when_present(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ps = fake_bin / "ps"
    ps.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '4242 1 node plugins/antigravity-for-codex/scripts/antigravity-companion.mjs __run-job fake'\n",
        encoding="utf8",
    )
    ps.chmod(0o755)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["ANTIGRAVITY_FOR_CODEX_TEST_REPO"] = str(repo)
    source = """
import process from 'node:process';
import { createJob, updateJob, cancelJob } from './plugins/antigravity-for-codex/scripts/lib/jobs.mjs';

const originalKill = process.kill;
const killCalls = [];
const cwd = process.env.ANTIGRAVITY_FOR_CODEX_TEST_REPO;

try {
  process.kill = (pid, signal = 0) => {
    killCalls.push({ pid, signal });
    if (pid === -4242 && signal === 'SIGTERM') {
      return true;
    }
    if (pid === -4242 && signal === 0) {
      const error = new Error('no such process group');
      error.code = 'ESRCH';
      throw error;
    }
    return originalKill(pid, signal);
  };

  const expectedIdentity = {
    pid: 4242,
    ppid: 1,
    command: 'node plugins/antigravity-for-codex/scripts/antigravity-companion.mjs __run-job fake'
  };
  const job = createJob({ command: 'review', args: ['reserved with worker pid'], cwd }, process.env);
  updateJob(job.id, (draft) => {
    draft.status = 'reserved';
    draft.submissionState = 'created';
    draft.worker = { identity: expectedIdentity };
    draft.workerPid = '4242';
    return draft;
  }, cwd, process.env);

  const cancelled = cancelJob(job.id, cwd, process.env);
  if (cancelled.status !== 'cancelled') throw new Error(`expected cancelled: ${JSON.stringify(cancelled)}`);
  if (cancelled.cancel.status === 'not_running') {
    throw new Error(`reserved worker pid was not used for termination: ${JSON.stringify(cancelled)}`);
  }
  if (cancelled.cancel.status !== 'terminated') {
    throw new Error(`expected terminated cancellation: ${JSON.stringify(cancelled)}`);
  }
  if (!killCalls.some((call) => call.pid === -4242 && call.signal === 'SIGTERM')) {
    throw new Error(`expected SIGTERM for reserved worker pid: ${JSON.stringify({ cancelled, killCalls })}`);
  }
  console.log(JSON.stringify({ cancel: cancelled.cancel, killCalls }));
} finally {
  process.kill = originalKill;
}
"""
    result = run_node_eval(source, env)
    assert result.returncode == 0, result.stderr


def test_queued_unstarted_job_cancel_is_metadata_only(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = companion_env(tmp_path, fake_agy(tmp_path))
    env["ANTIGRAVITY_FOR_CODEX_TEST_REPO"] = str(repo)
    source = """
import { createJob } from './plugins/antigravity-for-codex/scripts/lib/jobs.mjs';

const cwd = process.env.ANTIGRAVITY_FOR_CODEX_TEST_REPO;
const job = createJob({ command: 'review', args: ['queued only'], cwd }, process.env);
console.log(job.id);
"""
    created = run_node_eval(source, env)
    assert created.returncode == 0, created.stderr
    job_id = created.stdout.strip()

    cancelled = run_companion(["cancel", job_id], repo, env)

    assert cancelled.returncode == 0, cancelled.stderr
    payload = json.loads(cancelled.stdout)
    assert payload["status"] == "cancelled"
    assert payload["cancel"]["status"] == "not_running"
    assert "missing trusted worker identity" not in payload.get("error", "")


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
    env = sanitized_env()
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

    assert "sameProcessIdentity(expectedIdentity, initialProbe.identity)" in text
    assert "actualCommand === expectedCommand" in text
    assert 'expectedCommand.includes("antigravity-companion.mjs")' in text
    assert 'expectedCommand.includes("__run-job")' in text
    assert "ppid changed" not in text
    assert "process.kill(-pid, signal)" in text
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
    env = sanitized_env()
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
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, capture_argv=argv_file))

    result = subprocess.run([NODE, str(runtime), "multi-review", "--help"], env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert "Usage: antigravity-companion.mjs multi-review" in result.stdout
    assert not argv_file.exists()


def test_role_packs_lists_builtin_packs():
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    result = subprocess.run([NODE, str(runtime), "roles", "--json"], env=sanitized_env(), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "default" in payload["packs"]
    assert payload["packs"]["release"]["roles"] == ["release", "tests", "correctness", "security"]


def test_github_actions_init_validate_and_render(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = sanitized_env()

    rendered = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--model-provider", "gemini", "--timeout-minutes", "15"],
        cwd=repo,
        env=env,
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
        env=env,
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

    validate = subprocess.run([NODE, str(runtime), "github-actions", "validate"], cwd=repo, env=env, capture_output=True, text=True)
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
    (repo / "change.txt").write_text("change\n", encoding="utf8")
    agy = fake_agy(tmp_path, response="ALLOW: ok", capture_argv=argv_file)
    env = companion_env(tmp_path, agy)
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER"] = "claude"
    env["ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL"] = "Claude Sonnet 4.6 (Thinking)"

    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert "Model provider: claude." in prompt
    assert "Model: Claude Sonnet 4.6 (Thinking)" in prompt


def test_github_actions_rejects_mutable_ref_and_validates_path(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    env = sanitized_env()
    for ref in ["main", "refs/heads/main", "develop", "release/latest"]:
        bad_ref = subprocess.run(
            [NODE, str(runtime), "github-actions", "render", "--ref", ref],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert bad_ref.returncode == 2
        assert "immutable" in bad_ref.stderr or "release tag" in bad_ref.stderr

    custom_ref = tmp_path / "custom-ref.yml"
    custom_render = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--ref", "antigravity-for-codex-v0.2.0"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert custom_render.returncode == 0, custom_render.stderr
    custom_ref.write_text(custom_render.stdout, encoding="utf8")
    custom_validate = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate", "--path", str(custom_ref)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert custom_validate.returncode == 0, custom_validate.stderr

    invalid_timeout = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--timeout-minutes", "abc"],
        cwd=tmp_path,
        env=env,
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
        "# antigravity-for-codex-v0.6.0\n"
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
        env=env,
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
        "          echo --ref antigravity-for-codex-v0.6.0\n"
        "          codex plugin add antigravity-for-codex@external-models-for-codex\n"
        "          ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER=gemini node plugins/antigravity-for-codex/scripts/antigravity-companion.mjs review\n",
        encoding="utf8",
    )
    mutable_ref = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate", "--path", str(mutable_ref_workflow)],
        cwd=tmp_path,
        env=env,
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
        env=env,
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
        env=env,
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
        env=env,
        capture_output=True,
        text=True,
    )
    assert linux_local_path.returncode == 1
    linux_local_path_checks = {check["name"]: check["ok"] for check in json.loads(linux_local_path.stdout)["checks"]}
    assert linux_local_path_checks["no-local-absolute-paths"] is False

    windows_forward_path_workflow = tmp_path / "windows-forward-local-path.yml"
    windows_forward_path_workflow.write_text(
        custom_render.stdout.replace(
            'ANTIGRAVITY_FOR_CODEX_MODEL: ""',
            'ANTIGRAVITY_FOR_CODEX_MODEL: ""\n          LEAKED_LOCAL_PATH: "C:/Users/example/project/plugins/antigravity-for-codex"',
        ),
        encoding="utf8",
    )
    windows_forward_path = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate", "--path", str(windows_forward_path_workflow)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert windows_forward_path.returncode == 1
    windows_forward_path_checks = {check["name"]: check["ok"] for check in json.loads(windows_forward_path.stdout)["checks"]}
    assert windows_forward_path_checks["no-local-absolute-paths"] is False

    windows_generic_drive_path_workflow = tmp_path / "windows-generic-drive-local-path.yml"
    windows_generic_drive_path_workflow.write_text(
        custom_render.stdout.replace(
            'ANTIGRAVITY_FOR_CODEX_MODEL: ""',
            'ANTIGRAVITY_FOR_CODEX_MODEL: ""\n          LEAKED_LOCAL_PATH: "D:/Work Projects/external-models-for-codex"',
        ),
        encoding="utf8",
    )
    windows_generic_drive_path = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate", "--path", str(windows_generic_drive_path_workflow)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert windows_generic_drive_path.returncode == 1
    generic_drive_checks = {check["name"]: check["ok"] for check in json.loads(windows_generic_drive_path.stdout)["checks"]}
    assert generic_drive_checks["no-local-absolute-paths"] is False

    home_path_with_spaces_workflow = tmp_path / "home-path-with-spaces.yml"
    home_path_with_spaces_workflow.write_text(
        custom_render.stdout.replace(
            'ANTIGRAVITY_FOR_CODEX_MODEL: ""',
            'ANTIGRAVITY_FOR_CODEX_MODEL: ""\n          LEAKED_LOCAL_PATH: "/Users/example/My Project/external-models-for-codex"',
        ),
        encoding="utf8",
    )
    home_path_with_spaces = subprocess.run(
        [NODE, str(runtime), "github-actions", "validate", "--path", str(home_path_with_spaces_workflow)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert home_path_with_spaces.returncode == 1
    home_path_checks = {check["name"]: check["ok"] for check in json.loads(home_path_with_spaces.stdout)["checks"]}
    assert home_path_checks["no-local-absolute-paths"] is False


def test_github_actions_rejects_invalid_shell_like_model(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    marker = tmp_path / "SHOULD_NOT_RUN"
    rendered = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--model", f"gemini $(touch {marker})"],
        cwd=tmp_path,
        env=sanitized_env(),
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
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="PACK_OK", capture_argv=argv_file))
    result = subprocess.run([NODE, str(runtime), "multi-review", "--role-pack", "security"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "## security" in result.stdout
    assert "## correctness" in result.stdout
    assert "## adversarial" in result.stdout


def test_review_gate_blocks_only_on_explicit_block(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "change.txt").write_text("change\n", encoding="utf8")
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"

    block_agy = fake_agy(tmp_path / "block", response="BLOCK: stop here\nEvidence")
    env["AGY_CLI_PATH"] = str(block_agy)
    block = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True)
    assert block.returncode == 0
    assert json.loads(block.stdout) == {"decision": "block", "reason": "stop here"}
    assert block.stderr == ""

    allow_agy = fake_agy(tmp_path / "allow", response="ALLOW: ok")
    env["AGY_CLI_PATH"] = str(allow_agy)
    allow = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True)
    assert allow.returncode == 0
    assert allow.stdout == ""
    assert allow.stderr == ""

    embedded_block_agy = fake_agy(tmp_path / "embedded", response="Notes first\nBLOCK: not first")
    env["AGY_CLI_PATH"] = str(embedded_block_agy)
    embedded = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True)
    assert embedded.returncode == 0
    assert embedded.stdout == ""
    assert "invalid output; allowing stop" in embedded.stderr


def test_review_gate_fail_open_on_invalid_output(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "change.txt").write_text("change\n", encoding="utf8")
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="not a gate verdict"))

    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert result.stdout == ""
    assert "[antigravity-for-codex review-gate] invalid output; allowing stop" in result.stderr


def test_review_gate_sync_timeout_cleans_posix_descendant_process_group(tmp_path):
    if os.name == "nt":
        return
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "change.txt").write_text("change\n", encoding="utf8")
    descendant_pid_file = tmp_path / "agy-descendant.pid"
    env = companion_env(tmp_path, descendant_spawning_agy(tmp_path, descendant_pid_file))
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"

    result = subprocess.run([NODE, str(runtime), "review-gate", "--timeout-seconds", "1"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert result.stdout == ""
    assert "runtime failed; allowing stop" in result.stderr
    assert descendant_pid_file.exists()
    descendant_pid = int(descendant_pid_file.read_text(encoding="utf8"))
    try:
        assert wait_for_process_exit(descendant_pid, timeout=3)
    finally:
        if process_is_running(descendant_pid):
            subprocess.run(["kill", "-KILL", str(descendant_pid)], capture_output=True, text=True)


def test_review_gate_clean_repo_skips_preflight_and_model_even_on_block(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    marker = tmp_path / "agy-called"
    repo.mkdir()
    init_git_repo(repo)
    agy = tmp_path / "agy"
    agy.write_text(
        "#!/bin/sh\n"
        f"printf called > {shlex.quote(str(marker))}\n"
        "if [ \"$*\" = \"--help\" ]; then printf 'Usage of agy:\\n  --log-file\\n  --model\\n  --print-timeout\\n  --prompt\\n'; exit 0; fi\n"
        "if [ \"$*\" = \"models\" ]; then printf 'Gemini 3.1 Pro (High)\\nClaude Sonnet 4.6 (Thinking)\\n'; exit 0; fi\n"
        "printf 'BLOCK: should not run'\n",
        encoding="utf8",
    )
    agy.chmod(0o755)
    env = companion_env(tmp_path, agy)
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"

    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert not marker.exists()


def test_review_gate_uses_inner_timeout_below_hook_timeout(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "change.txt").write_text("change\n", encoding="utf8")
    argv_file = tmp_path / "agy-argv.json"
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="ALLOW: ok", capture_argv=argv_file))

    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    assert argv[argv.index("--print-timeout") + 1] == "840s"
    prompt = argv[argv.index("--prompt") + 1]
    assert "<role_name>stop-gate</role_name>" in prompt
    assert "<task>Run a stop-gate review of the current git changes.</task>" in prompt


def test_review_gate_timeout_cap_ignores_inner_cli_and_env_timeout(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "change.txt").write_text("change\n", encoding="utf8")
    argv_file = tmp_path / "agy-argv.json"
    env = sanitized_env()
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS"] = str(60 * 60 * 1000)
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="ALLOW: ok", capture_argv=argv_file))

    result = subprocess.run(
        [NODE, str(runtime), "review-gate", "--timeout-seconds", "3600"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    assert argv[argv.index("--print-timeout") + 1] == "840s"


def test_review_gate_cli_timeout_bounds_hanging_model_call_and_fails_open(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "change.txt").write_text("change\n", encoding="utf8")
    env = companion_env(tmp_path, fake_agy(tmp_path, never_exit=True))
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"

    started = time.monotonic()
    result = subprocess.run(
        [NODE, str(runtime), "review-gate", "--timeout-seconds", "1"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0
    assert result.stdout == ""
    assert "allowing stop" in result.stderr
    assert elapsed < 5


def test_review_gate_hanging_agy_help_respects_aggregate_budget_and_fails_open(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "change.txt").write_text("change\n", encoding="utf8")
    agy = tmp_path / "agy"
    agy.write_text(
        "#!/usr/bin/env node\n"
        "const argv = process.argv.slice(2);\n"
        "if (argv.join(' ') === '--help') { setTimeout(() => {}, 10000); }\n"
        "else { process.stdout.write('ALLOW: ok'); }\n",
        encoding="utf8",
    )
    agy.chmod(0o755)
    env = companion_env(tmp_path, agy)
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS"] = "1000"

    started = time.monotonic()
    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
    elapsed = time.monotonic() - started

    assert result.returncode == 0
    assert result.stdout == ""
    assert "allowing stop" in result.stderr
    assert elapsed < 5


def test_review_gate_hanging_agy_times_out_before_wrapper_and_fails_open(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "change.txt").write_text("change\n", encoding="utf8")
    env = companion_env(tmp_path, fake_agy(tmp_path, never_exit=True))
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS"] = "1000"

    started = time.monotonic()
    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
    elapsed = time.monotonic() - started

    assert result.returncode == 0
    assert result.stdout == ""
    assert "allowing stop" in result.stderr
    assert elapsed < 5


def test_review_gate_hanging_git_context_respects_aggregate_budget_and_fails_open(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    repo.mkdir()
    init_git_repo(repo)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nsleep 5\n", encoding="utf8")
    fake_git.chmod(0o755)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="ALLOW: ok", capture_argv=argv_file))
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS"] = "1000"
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    started = time.monotonic()
    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
    elapsed = time.monotonic() - started

    assert result.returncode == 0
    assert result.stdout == ""
    assert "allowing stop" in result.stderr
    assert not argv_file.exists()
    assert elapsed < 5


def test_review_gate_git_root_failure_fails_open_without_model_call(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    repo.mkdir()
    init_git_repo(repo)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "while [ \"$1\" = \"-c\" ]; do shift 2; done\n"
        "if [ \"$1 $2\" = \"rev-parse --show-toplevel\" ]; then\n"
        "  echo 'fatal: injected git root failure' >&2\n"
        "  exit 128\n"
        "fi\n"
        f"exec {shlex.quote(shutil.which('git') or 'git')} \"$@\"\n",
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="ALLOW: ok", capture_argv=argv_file))
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS"] = "5000"
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)

    assert result.returncode == 0
    assert result.stdout == ""
    assert "allowing stop" in result.stderr
    assert "git root discovery failed" in result.stderr
    assert not argv_file.exists()


def test_review_gate_secondary_git_failure_fails_open_without_model_call(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    repo.mkdir()
    init_git_repo(repo)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "while [ \"$1\" = \"-c\" ]; do shift 2; done\n"
        "if [ \"$1 $2\" = \"rev-parse --show-toplevel\" ]; then\n"
        "  pwd\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1 $2\" = \"status --short\" ]; then\n"
        "  echo 'fatal: injected git status failure' >&2\n"
        "  exit 42\n"
        "fi\n"
        f"exec {shlex.quote(shutil.which('git') or 'git')} \"$@\"\n",
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="ALLOW: ok", capture_argv=argv_file))
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS"] = "5000"
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)

    assert result.returncode == 0
    assert result.stdout == ""
    assert "allowing stop" in result.stderr
    assert "git status failed" in result.stderr
    assert "injected git status failure" in result.stderr
    assert not argv_file.exists()


def test_review_gate_git_diffs_use_no_ext_diff(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    git_log = tmp_path / "git-argv.log"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "change.txt").write_text("change\n", encoding="utf8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "while [ \"$1\" = \"-c\" ]; do shift 2; done\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(git_log))}\n"
        f"exec {shlex.quote(shutil.which('git') or 'git')} \"$@\"\n",
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="ALLOW: ok", capture_argv=argv_file))
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)

    assert result.returncode == 0, result.stderr
    assert argv_file.exists()
    commands = git_log.read_text(encoding="utf8").splitlines()
    assert "diff --no-ext-diff --no-textconv --cached --stat" in commands
    assert "diff --no-ext-diff --no-textconv --stat" in commands
    assert "diff --no-ext-diff --no-textconv --cached -- ." in commands
    assert "diff --no-ext-diff --no-textconv -- ." in commands


def test_review_gate_git_context_ignores_ambient_git_env_and_disables_helpers(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    argv_file = tmp_path / "agy-argv.json"
    repo_a.mkdir()
    repo_b.mkdir()
    init_git_repo(repo_a)
    init_git_repo(repo_b)

    fsmonitor_marker = tmp_path / "fsmonitor-was-run"
    fsmonitor = tmp_path / "fsmonitor.sh"
    fsmonitor.write_text(
        "#!/bin/sh\n"
        f"printf fsmonitor > {str(fsmonitor_marker)!r}\n"
        "exit 1\n",
        encoding="utf8",
    )
    fsmonitor.chmod(0o755)
    textconv_marker = tmp_path / "textconv-was-run"
    textconv = tmp_path / "textconv.sh"
    textconv.write_text(
        "#!/bin/sh\n"
        f"printf textconv > {str(textconv_marker)!r}\n"
        "cat \"$1\"\n",
        encoding="utf8",
    )
    textconv.chmod(0o755)

    (repo_a / ".gitattributes").write_text("*.spy diff=spy\n", encoding="utf8")
    (repo_a / "repo-a-unique.txt").write_text("repo a base\n", encoding="utf8")
    (repo_a / "secret.spy").write_text("one\n", encoding="utf8")
    subprocess.run(["git", "config", "diff.spy.textconv", str(textconv)], cwd=repo_a, check=True)
    subprocess.run(["git", "config", "core.fsmonitor", str(fsmonitor)], cwd=repo_a, check=True)
    subprocess.run(["git", "add", ".gitattributes", "repo-a-unique.txt", "secret.spy"], cwd=repo_a, check=True)
    subprocess.run(["git", "commit", "-m", "repo a base"], cwd=repo_a, check=True, capture_output=True, text=True)

    (repo_b / "repo-b-unique.txt").write_text("repo b base\n", encoding="utf8")
    subprocess.run(["git", "add", "repo-b-unique.txt"], cwd=repo_b, check=True)
    subprocess.run(["git", "commit", "-m", "repo b base"], cwd=repo_b, check=True, capture_output=True, text=True)

    (repo_a / "repo-a-unique.txt").write_text("repo a changed\n", encoding="utf8")
    (repo_a / "secret.spy").write_text("two\n", encoding="utf8")
    (repo_b / "repo-b-unique.txt").write_text("repo b changed\n", encoding="utf8")
    fsmonitor_marker.unlink(missing_ok=True)
    textconv_marker.unlink(missing_ok=True)

    env = companion_env(tmp_path, fake_agy(tmp_path, response="ALLOW: ok", capture_argv=argv_file))
    env.update({
        "ANTIGRAVITY_FOR_CODEX_REVIEW_GATE": "on",
        "GIT_DIR": str(repo_b / ".git"),
        "GIT_WORK_TREE": str(repo_b),
        "GIT_INDEX_FILE": str(repo_b / ".git" / "index"),
        "GIT_COMMON_DIR": str(repo_b / ".git"),
        "GIT_OBJECT_DIRECTORY": str(repo_b / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(repo_b / ".git" / "objects"),
        "GIT_NAMESPACE": "poison",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "false",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.worktree",
        "GIT_CONFIG_VALUE_0": str(repo_b),
    })

    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo_a, env=env, capture_output=True, text=True, timeout=10)

    assert result.returncode == 0, result.stderr
    assert argv_file.exists()
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert str(repo_a) in prompt
    assert "repo-a-unique.txt" in prompt
    assert "repo-b-unique.txt" not in prompt
    assert not fsmonitor_marker.exists()
    assert not textconv_marker.exists()


def test_review_git_context_ignores_ambient_git_env_and_disables_helpers(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    argv_file = tmp_path / "agy-argv.json"
    repo_a.mkdir()
    repo_b.mkdir()
    init_git_repo(repo_a)
    init_git_repo(repo_b)

    fsmonitor_marker = tmp_path / "normal-fsmonitor-was-run"
    fsmonitor = tmp_path / "normal-fsmonitor.sh"
    fsmonitor.write_text(
        "#!/bin/sh\n"
        f"printf fsmonitor > {str(fsmonitor_marker)!r}\n"
        "exit 1\n",
        encoding="utf8",
    )
    fsmonitor.chmod(0o755)
    textconv_marker = tmp_path / "normal-textconv-was-run"
    textconv = tmp_path / "normal-textconv.sh"
    textconv.write_text(
        "#!/bin/sh\n"
        f"printf textconv > {str(textconv_marker)!r}\n"
        "cat \"$1\"\n",
        encoding="utf8",
    )
    textconv.chmod(0o755)

    (repo_a / ".gitattributes").write_text("*.spy diff=spy\n", encoding="utf8")
    (repo_a / "repo-a-unique.txt").write_text("repo a base\n", encoding="utf8")
    (repo_a / "secret.spy").write_text("one\n", encoding="utf8")
    subprocess.run(["git", "config", "diff.spy.textconv", str(textconv)], cwd=repo_a, check=True)
    subprocess.run(["git", "config", "core.fsmonitor", str(fsmonitor)], cwd=repo_a, check=True)
    subprocess.run(["git", "add", ".gitattributes", "repo-a-unique.txt", "secret.spy"], cwd=repo_a, check=True)
    subprocess.run(["git", "commit", "-m", "repo a base"], cwd=repo_a, check=True, capture_output=True, text=True)

    (repo_b / "repo-b-unique.txt").write_text("repo b base\n", encoding="utf8")
    subprocess.run(["git", "add", "repo-b-unique.txt"], cwd=repo_b, check=True)
    subprocess.run(["git", "commit", "-m", "repo b base"], cwd=repo_b, check=True, capture_output=True, text=True)

    (repo_a / "repo-a-unique.txt").write_text("repo a changed\n", encoding="utf8")
    (repo_a / "secret.spy").write_text("two\n", encoding="utf8")
    (repo_b / "repo-b-unique.txt").write_text("repo b changed\n", encoding="utf8")
    fsmonitor_marker.unlink(missing_ok=True)
    textconv_marker.unlink(missing_ok=True)

    env = companion_env(tmp_path, fake_agy(tmp_path, response="REVIEW_OK", capture_argv=argv_file))
    env.update({
        "GIT_DIR": str(repo_b / ".git"),
        "GIT_WORK_TREE": str(repo_b),
        "GIT_INDEX_FILE": str(repo_b / ".git" / "index"),
        "GIT_COMMON_DIR": str(repo_b / ".git"),
        "GIT_OBJECT_DIRECTORY": str(repo_b / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(repo_b / ".git" / "objects"),
        "GIT_NAMESPACE": "poison",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "false",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.worktree",
        "GIT_CONFIG_VALUE_0": str(repo_b),
    })

    result = subprocess.run([NODE, str(runtime), "review", "normal review"], cwd=repo_a, env=env, capture_output=True, text=True, timeout=10)

    assert result.returncode == 0, result.stderr
    assert argv_file.exists()
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert str(repo_a) in prompt
    assert "repo-a-unique.txt" in prompt
    assert "repo-b-unique.txt" not in prompt
    assert not fsmonitor_marker.exists()
    assert not textconv_marker.exists()


def test_review_gate_untracked_symlink_is_not_read(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    repo = tmp_path / "repo"
    argv_file = tmp_path / "agy-argv.json"
    secret = tmp_path / "outside-secret.txt"
    repo.mkdir()
    init_git_repo(repo)
    secret.write_text("DO_NOT_LEAK_STOP_GATE_SECRET\n", encoding="utf8")
    (repo / "leak").symlink_to(secret)
    env = companion_env(tmp_path, fake_agy(tmp_path, response="ALLOW: ok", capture_argv=argv_file))
    env["ANTIGRAVITY_FOR_CODEX_REVIEW_GATE"] = "on"

    result = subprocess.run([NODE, str(runtime), "review-gate"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)

    assert result.returncode == 0, result.stderr
    assert argv_file.exists()
    argv = json.loads(argv_file.read_text(encoding="utf8"))["argv"]
    prompt = argv[argv.index("--prompt") + 1]
    assert "Untracked file: leak" in prompt
    assert "[skipped symlink]" in prompt
    assert "DO_NOT_LEAK_STOP_GATE_SECRET" not in prompt


def test_review_gate_hook_does_not_block_on_open_stdin_pipe(tmp_path):
    hook = PLUGIN / "hooks" / "antigravity-review-gate.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    env = sanitized_env()
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

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=PLUGIN, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "PASS manifest-name" in result.stdout
    assert "PASS manifest-version" in result.stdout
    assert "PASS docs-version-aligned" in result.stdout
    assert "PASS manifest-model-policy" in result.stdout
    assert "PASS agy-capabilities-module" in result.stdout
    assert "PASS agy-outcome-module" in result.stdout
    assert "PASS doctor-command" in result.stdout
    assert "PASS job-lifecycle-fingerprint" in result.stdout
    assert "PASS hook-compat-module" in result.stdout
    assert "PASS no-claude-native-executable-leakage" in result.stdout
    assert "PASS no-raw-claude-executable-invocation" in result.stdout
    assert "PASS docs-negative-claude-boundary" in result.stdout
    assert "PASS no-local-absolute-paths" in result.stdout
    assert "PASS manifest-default-prompts-limit" in result.stdout
    assert "PASS manifest-composer-icon-relative" in result.stdout
    assert "PASS manifest-logo-relative" in result.stdout
    assert "PASS manifest-screenshots-relative" in result.stdout
    assert "PASS review-gate-timeout-env" in result.stdout
    assert "PASS normal-git-context-hardening" in result.stdout
    assert "PASS review-gate-git-hardening" in result.stdout
    assert "PASS review-gate-reviewable-first" in result.stdout
    assert "PASS state-git-hardening" in result.stdout
    assert "PASS untracked-symlink-safe" in result.stdout
    assert "PASS background-supervisor-hardening" in result.stdout
    assert "PASS windows-descendant-cleanup" in result.stdout
    assert "PASS posix-sync-timeout-cleanup" in result.stdout
    assert "PASS reserved-job-resource-guard" in result.stdout
    assert "PASS no-unsupported-review-gate-setup-command" in result.stdout
    assert "PASS background-idempotency-fingerprint" in result.stdout
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
    assert "PASS repository-install-docs-release-ref" in result.stdout
    assert "PASS docs-maturity-boundary" in result.stdout
    assert "PASS docs-no-unsupported-parity" in result.stdout
    assert "PASS docs-claude-through-antigravity-boundary" in result.stdout
    assert "PASS docs-real-smoke-opt-in" in result.stdout
    assert "PASS docs-ci-authenticated-agy" in result.stdout
    assert "PASS model-catalog-bounded-preflight" in result.stdout
    assert "PASS all-mature-commands" in result.stdout


def test_release_check_passes_from_installed_plugin_layout(tmp_path):
    installed = tmp_path / "plugins" / "cache" / "external-models-for-codex" / "antigravity-for-codex" / "0.6.0"
    shutil.copytree(PLUGIN, installed)
    runtime = installed / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=installed, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "PASS skills-natural-language-routing" in result.stdout
    assert "PASS github-actions-no-repo-relative-runtime-path" in result.stdout
    assert "PASS repository-install-docs-release-ref" in result.stdout


def test_release_check_rejects_missing_review_gate_git_hardening(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    companion = plugin / "scripts" / "antigravity-companion.mjs"
    text = companion.read_text(encoding="utf8")
    assert '    "-c", "core.fsmonitor=false",\n' in text
    companion.write_text(text.replace('    "-c", "core.fsmonitor=false",\n', "", 1), encoding="utf8")
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL review-gate-git-hardening" in result.stdout
    assert "release-check failed: review-gate-git-hardening" in result.stderr


def test_release_check_rejects_missing_normal_git_context_hardening(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    companion = plugin / "scripts" / "antigravity-companion.mjs"
    text = companion.read_text(encoding="utf8")
    assert '["diff", "--no-ext-diff", "--no-textconv", "--cached", "--stat"]' in text
    companion.write_text(
        text.replace('["diff", "--no-ext-diff", "--no-textconv", "--cached", "--stat"]', '["diff", "--cached", "--stat"]', 1),
        encoding="utf8",
    )
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL normal-git-context-hardening" in result.stdout
    assert "release-check failed: normal-git-context-hardening" in result.stderr


def test_release_check_rejects_review_gate_preflight_before_reviewable_check(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    companion = plugin / "scripts" / "antigravity-companion.mjs"
    text = companion.read_text(encoding="utf8")
    assert "if (!context.reviewable)" in text
    companion.write_text(text.replace("if (!context.reviewable)", "if (false && !context.reviewable)", 1), encoding="utf8")
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL review-gate-reviewable-first" in result.stdout
    assert "release-check failed: review-gate-reviewable-first" in result.stderr


def test_release_check_rejects_unhardened_state_git_root(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    state = plugin / "scripts" / "lib" / "state.mjs"
    text = state.read_text(encoding="utf8")
    assert '"core.fsmonitor=false"' in text
    state.write_text(text.replace('"core.fsmonitor=false",\n', "", 1), encoding="utf8")
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL state-git-hardening" in result.stdout
    assert "release-check failed: state-git-hardening" in result.stderr


def test_release_check_rejects_untracked_symlink_following(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    companion = plugin / "scripts" / "antigravity-companion.mjs"
    text = companion.read_text(encoding="utf8")
    assert "fs.lstatSync(filePath)" in text
    companion.write_text(text.replace("fs.lstatSync(filePath)", "fs.statSync(filePath)", 1), encoding="utf8")
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL untracked-symlink-safe" in result.stdout
    assert "release-check failed: untracked-symlink-safe" in result.stderr


def test_release_check_rejects_unhardened_background_supervisor(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    companion = plugin / "scripts" / "antigravity-companion.mjs"
    text = companion.read_text(encoding="utf8")
    assert "(DEFAULT_TIMEOUT_MS * 2) + BACKGROUND_SUPERVISOR_TIMEOUT_GRACE_MS" in text
    companion.write_text(
        text.replace("(DEFAULT_TIMEOUT_MS * 2) + BACKGROUND_SUPERVISOR_TIMEOUT_GRACE_MS", "DEFAULT_TIMEOUT_MS", 1),
        encoding="utf8",
    )
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL background-supervisor-hardening" in result.stdout
    assert "release-check failed: background-supervisor-hardening" in result.stderr


def test_release_check_rejects_missing_windows_descendant_cleanup(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    runtime_source = plugin / "scripts" / "lib" / "antigravity-runtime.mjs"
    text = runtime_source.read_text(encoding="utf8")
    assert "cleanupWindowsProcessTree(child.pid" in text
    runtime_source.write_text(text.replace("cleanupWindowsProcessTree(child.pid", "cleanupWindowsProcessTree(0", 1), encoding="utf8")
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL windows-descendant-cleanup" in result.stdout
    assert "release-check failed: windows-descendant-cleanup" in result.stderr


def test_release_check_rejects_unsupported_review_gate_setup_asset_text(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    asset = plugin / "assets" / "stop-gate.svg"
    asset.write_text(
        asset.read_text(encoding="utf8") + "\n<!-- setup --enable-review-gate --review-gate-mode multi-role -->\n",
        encoding="utf8",
    )
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL no-unsupported-review-gate-setup-command" in result.stdout
    assert "release-check failed: no-unsupported-review-gate-setup-command" in result.stderr


def test_release_check_rejects_stale_source_layout_repository_install_docs(tmp_path):
    repo = tmp_path / "repo"
    plugin = repo / "plugins" / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    (repo / "docs").mkdir(parents=True)
    (repo / "README.md").write_text("codex plugin marketplace add yilibinbin/external-models-for-codex --ref antigravity-for-codex-v0.0.0\n", encoding="utf8")
    (repo / "docs" / "README.en.md").write_text("missing current release ref\n", encoding="utf8")
    (repo / "docs" / "README.zh-CN.md").write_text("missing current release ref\n", encoding="utf8")
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL repository-install-docs-release-ref" in result.stdout
    assert "release-check failed: repository-install-docs-release-ref" in result.stderr


def test_release_check_rejects_local_paths_in_shipped_prompts(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    prompt = plugin / "prompts" / "review.md"
    prompt.write_text(
        prompt.read_text(encoding="utf8")
        + "\nLeaked local paths: /home/example/project and C:\\Users\\example\\project\n",
        encoding="utf8",
    )
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL no-local-absolute-paths" in result.stdout
    assert "release-check failed: no-local-absolute-paths" in result.stderr


def test_release_check_rejects_json_escaped_windows_home_paths(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    contract = plugin / "contracts" / "natural-language-routing.json"
    payload = json.loads(contract.read_text(encoding="utf8"))
    payload["testLocalPathLeak"] = r"C:\Users\example\project"
    contract.write_text(json.dumps(payload, indent=2), encoding="utf8")
    assert r"C:\\Users\\example\\project" in contract.read_text(encoding="utf8")
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL no-local-absolute-paths" in result.stdout
    assert "release-check failed: no-local-absolute-paths" in result.stderr


def test_release_check_rejects_windows_forward_slash_home_paths_in_json(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    manifest = plugin / ".codex-plugin" / "plugin.json"
    payload = json.loads(manifest.read_text(encoding="utf8"))
    payload.setdefault("keywords", []).append("C:/Users/example/project")
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf8")
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL no-local-absolute-paths" in result.stdout
    assert "release-check failed: no-local-absolute-paths" in result.stderr


def test_release_check_rejects_local_paths_in_shipped_assets(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    asset = plugin / "assets" / "logo.svg"
    asset.write_text(
        asset.read_text(encoding="utf8")
        + "\n<!-- Leaked local path: /Users/example/project/plugins/antigravity-for-codex -->\n",
        encoding="utf8",
    )
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 1
    assert "FAIL no-local-absolute-paths" in result.stdout
    assert "release-check failed: no-local-absolute-paths" in result.stderr


def test_release_check_ignores_symlinked_shipped_texts(tmp_path):
    plugin = tmp_path / "antigravity-for-codex"
    shutil.copytree(PLUGIN, plugin)
    outside = tmp_path / "outside.md"
    outside.write_text("Leaked local path behind symlink: /Users/example/secret/project\n", encoding="utf8")
    (plugin / "prompts" / "symlinked-local-path.md").symlink_to(outside)
    runtime = plugin / "scripts" / "antigravity-companion.mjs"

    result = subprocess.run([NODE, str(runtime), "release-check"], cwd=plugin, env=sanitized_env(), capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "PASS no-local-absolute-paths" in result.stdout


def test_antigravity_release_check_rejects_linux_home_paths_in_source_guard():
    text = (PLUGIN / "scripts" / "antigravity-companion.mjs").read_text(encoding="utf8")
    assert "/(?:Users|home|" in text
    assert "[A-Za-z]:(?:\\\\{1,2}|\\/)" in text
    assert "(?:^|[\\s\"'=])[A-Za-z]:" in text
    assert "no-local-absolute-paths" in text


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
    env = sanitized_env()
    env["AGY_CLI_PATH"] = str(fake_agy(tmp_path, response="ANTIGRAVITY_FOR_CODEX_SMOKE_OK"))
    env.pop("ANTIGRAVITY_FOR_CODEX_REAL_SMOKE", None)

    result = subprocess.run([NODE, str(runtime), "real-smoke", "--quick"], env=env, capture_output=True, text=True)

    assert result.returncode == 2
    assert "real-smoke is opt-in" in result.stderr


def test_real_smoke_runs_fake_agy_for_gemini_and_claude(tmp_path):
    runtime = PLUGIN / "scripts" / "antigravity-companion.mjs"
    argv_file = tmp_path / "agy-argv.json"
    env = sanitized_env()
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
    init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("agy\nagy-full-argv.json\nstate/\n", encoding="utf8")

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
