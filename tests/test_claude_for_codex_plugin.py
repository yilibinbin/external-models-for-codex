import json
import hashlib
import os
import pathlib
import re
import shutil
import signal
import subprocess
import time

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "claude-for-codex"
NODE = os.environ.get("NODE_BINARY") or shutil.which("node") or "/Applications/Codex.app/Contents/Resources/node"
DEFAULT_MULTI_REVIEW_ROLES_FOR_TESTS = ["correctness", "security", "tests", "release", "adversarial"]
MAX_WORKTREE_FINGERPRINT_FILE_BYTES = 1024 * 1024


def process_is_running(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "pid=", "-o", "stat="], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return False
    parts = result.stdout.strip().split(None, 1)
    if not parts or parts[0] != str(pid):
        return False
    stat = parts[1] if len(parts) > 1 else ""
    return not stat.startswith("Z")


def run_cancel_with_lock_retry(runtime, job_id, *, cwd, env, attempts=20):
    last = None
    for _ in range(attempts):
        last = subprocess.run(
            [NODE, str(runtime), "cancel", job_id],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )
        try:
            payload = json.loads(last.stdout)
        except json.JSONDecodeError:
            return last
        retryable_statuses = {payload.get("status"), payload.get("job", {}).get("status")}
        if retryable_statuses.isdisjoint({"locked", "workspace_locked"}):
            return last
        time.sleep(0.1)
    return last


def companion_working_tree_fingerprint(repo):
    def git_part(args):
        result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
        return "\n".join([f"status={result.returncode}", result.stdout, result.stderr])

    def safe_path(relative_path):
        root = repo.resolve()
        full_path = (root / relative_path).resolve()
        if full_path == root or root in full_path.parents:
            return full_path
        return None

    def file_fingerprint(file_path):
        try:
            stat = file_path.lstat()
            if file_path.is_symlink():
                return {"type": "symlink", "target": os.readlink(file_path)}
            if not file_path.is_file():
                return {"type": "other", "size": stat.st_size, "mtimeMs": stat.st_mtime_ns // 1_000_000}
            if stat.st_size > MAX_WORKTREE_FINGERPRINT_FILE_BYTES:
                return {"type": "file-large", "size": stat.st_size, "mtimeMs": stat.st_mtime_ns // 1_000_000}
            return {
                "type": "file",
                "size": stat.st_size,
                "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest(),
            }
        except OSError as error:
            return {"type": "error", "errorCode": getattr(error, "errno", None) or "UNKNOWN"}

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if untracked.returncode == 0:
        untracked_items = []
        for relative in sorted(item for item in untracked.stdout.split("\0") if item):
            full_path = safe_path(relative)
            untracked_items.append({
                "path": relative,
                "fingerprint": file_fingerprint(full_path) if full_path else {"type": "unsafe-path"},
            })
        untracked_part = json.dumps(untracked_items, separators=(",", ":"))
    else:
        untracked_part = f"status={untracked.returncode}\n{untracked.stderr}"

    parts = [
        git_part(["status", "--short", "--untracked-files=all"]),
        git_part(["diff", "--cached"]),
        git_part(["diff"]),
        untracked_part,
    ]
    return hashlib.sha256("\n--- claude-for-codex ---\n".join(parts).encode()).hexdigest()


def legacy_raw_hook_working_tree_fingerprint(repo):
    parts = []
    for args in (
        ["status", "--short", "--untracked-files=all"],
        ["diff", "--cached"],
        ["diff"],
    ):
        result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
        parts.append(result.stdout if result.returncode == 0 else "")
    return hashlib.sha256("\n--- claude-for-codex ---\n".join(parts).encode()).hexdigest()


def run_fake_claude_review(
    tmp_path,
    args,
    commit_head=False,
    extra_env=None,
    command="review",
    branch_file_count=0,
    branch_lines_per_file=0,
    extra_help=None,
):
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
        for index in range(branch_file_count):
            lines = "\n".join(f"branch {index} line {line}" for line in range(branch_lines_per_file))
            (repo / f"branch-{index}.txt").write_text(f"base\n{lines}\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
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
import time

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
if sys.argv[1:] == ["--help"]:
    marker = os.environ.get("HELP_PROBE_MARKER")
    if marker:
        pathlib.Path(marker).write_text("help-probed")
    if os.environ.get("FAIL_ON_HELP") == "1":
        print("unexpected help probe", file=sys.stderr)
        raise SystemExit(23)
    print(os.environ.get("FAKE_CLAUDE_HELP", "--model <model> alias opus sonnet --effort <level> --fallback-model <model> accepts a comma-separated list"))
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
    if extra_help is not None:
        env["FAKE_CLAUDE_HELP"] = extra_help
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [NODE, str(runtime), command, *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    prompt = (capture_dir / "prompt.txt").read_text() if (capture_dir / "prompt.txt").exists() else ""
    argv = json.loads((capture_dir / "argv.json").read_text()) if (capture_dir / "argv.json").exists() else []
    return result, prompt, argv


def env_without(*names):
    env = os.environ.copy()
    for name in names:
        env.pop(name, None)
    return env


def test_install_consistency_detects_stale_installed_version(tmp_path):
    module_uri = (PLUGIN / "scripts" / "lib" / "install-consistency.mjs").as_uri()
    marketplace = tmp_path / "marketplace"
    plugin = marketplace / "plugins" / "claude-for-codex"
    manifest_dir = plugin / ".codex-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text('{"name":"claude-for-codex","version":"0.18.1"}', encoding="utf8")
    installed_json = {
        "installed": [{
            "pluginId": "claude-for-codex@external-models-for-codex",
            "version": "0.17.0",
            "enabled": True,
            "source": {"path": str(plugin)},
            "marketplaceSource": {"source": "https://github.com/yilibinbin/external-models-for-codex.git"},
        }]
    }
    code = f"""
import {{ installConsistencyReport }} from {json.dumps(module_uri)};
const report = installConsistencyReport({{
  pluginRoot: {json.dumps(str(plugin))},
  pluginListJson: {json.dumps(json.dumps(installed_json))},
  pluginId: 'claude-for-codex@external-models-for-codex'
}});
if (report.ok) throw new Error('expected stale install to be attention');
if (report.installedVersion !== '0.17.0') throw new Error('bad installedVersion');
if (report.runningVersion !== '0.18.1') throw new Error('bad runningVersion');
if (!report.problems.some((problem) => problem.code === 'stale-installed-version')) throw new Error('missing stale problem');
if (!report.recommendedCommands.includes('codex plugin marketplace upgrade external-models-for-codex')) throw new Error('missing marketplace upgrade command');
if (!report.recommendedCommands.includes('codex plugin add claude-for-codex@external-models-for-codex')) throw new Error('missing plugin add command');
console.log(JSON.stringify(report));
"""
    result = subprocess.run([NODE, "--input-type=module", "-e", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_install_consistency_matches_name_marketplace_shape(tmp_path):
    module_uri = (PLUGIN / "scripts" / "lib" / "install-consistency.mjs").as_uri()
    marketplace = tmp_path / "marketplace"
    plugin = marketplace / "plugins" / "claude-for-codex"
    manifest_dir = plugin / ".codex-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text('{"name":"claude-for-codex","version":"0.18.1"}', encoding="utf8")
    installed_json = {
        "installed": [{
            "name": "claude-for-codex",
            "marketplaceName": "external-models-for-codex",
            "version": "0.18.1",
            "enabled": True,
            "source": {"path": str(plugin)},
        }]
    }
    code = f"""
import {{ installConsistencyReport }} from {json.dumps(module_uri)};
const report = installConsistencyReport({{
  pluginRoot: {json.dumps(str(plugin))},
  pluginListJson: {json.dumps(json.dumps(installed_json))}
}});
if (!report.ok) throw new Error('expected installed plugin to be ok: ' + JSON.stringify(report.problems));
if (report.status !== 'ok') throw new Error('expected ok status');
if (report.cacheVersion !== '0.18.1') throw new Error('expected cacheVersion from source manifest');
console.log(JSON.stringify(report));
"""
    result = subprocess.run([NODE, "--input-type=module", "-e", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_install_consistency_unknown_when_installed_version_missing(tmp_path):
    module_uri = (PLUGIN / "scripts" / "lib" / "install-consistency.mjs").as_uri()
    running = tmp_path / "running" / "plugins" / "claude-for-codex"
    installed = tmp_path / "installed" / "plugins" / "claude-for-codex"
    for root in (running, installed):
        manifest_dir = root / ".codex-plugin"
        manifest_dir.mkdir(parents=True)
    (running / ".codex-plugin" / "plugin.json").write_text('{"name":"claude-for-codex","version":"0.18.1"}', encoding="utf8")
    (installed / ".codex-plugin" / "plugin.json").write_text('{"name":"claude-for-codex","version":"0.17.0"}', encoding="utf8")
    installed_json = {
        "installed": [{
            "name": "claude-for-codex",
            "marketplaceName": "external-models-for-codex",
            "enabled": True,
            "source": {"path": str(installed)},
        }]
    }
    code = f"""
import {{ installConsistencyReport }} from {json.dumps(module_uri)};
const report = installConsistencyReport({{
  pluginRoot: {json.dumps(str(running))},
  pluginListJson: {json.dumps(json.dumps(installed_json))}
}});
if (!report.ok) throw new Error('missing version should be advisory unknown, not attention');
if (report.status !== 'unknown') throw new Error('expected unknown status, got ' + report.status);
if (!report.problems.some((problem) => problem.code === 'installed-version-unavailable')) throw new Error('missing version-unavailable marker');
if (report.cacheVersion !== '0.17.0') throw new Error('expected installed cache manifest signal');
console.log(JSON.stringify(report));
"""
    result = subprocess.run([NODE, "--input-type=module", "-e", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_job_lifecycle_helpers_classify_liveness_and_parse_limits(tmp_path):
    lifecycle = PLUGIN / "scripts" / "lib" / "job-lifecycle.mjs"
    script = f"""
import {{
  classifyJobLiveness,
  deriveJobIdempotencyKey,
  isTerminalJobStatus,
  parsePositiveInteger,
  reservationClaimMs,
  DEFAULT_BACKGROUND_WAIT_MS,
  HARD_JOB_TIMEOUT_MS,
  JOB_RESERVATION_CLAIM_MS
}} from {json.dumps(lifecycle.as_uri())};

const now = Date.parse("2026-06-09T00:10:00.000Z");
const payload = {{
  fresh: classifyJobLiveness({{ status: "running", lastHeartbeatAt: "2026-06-09T00:09:50.000Z" }}, {{ now }}),
  freshHeartbeatOldProgress: classifyJobLiveness({{ status: "running", lastProgressAt: "2026-06-09T00:00:00.000Z", lastHeartbeatAt: "2026-06-09T00:09:55.000Z" }}, {{ now }}),
  suspect: classifyJobLiveness({{ status: "running", lastHeartbeatAt: "2026-06-09T00:05:00.000Z" }}, {{ now }}),
  lost: classifyJobLiveness({{ status: "running", lastHeartbeatAt: "2026-06-09T00:00:00.000Z" }}, {{ now }}),
  queued: classifyJobLiveness({{ status: "queued", createdAt: "2026-06-09T00:09:30.000Z" }}, {{ now }}),
  reservationStillQueued: classifyJobLiveness({{ status: "queued", reservationMode: "host-forwarded", createdAt: "2026-06-09T00:09:00.000Z" }}, {{ now, queuedLostAfterMs: 100, reservationClaimMs: 120000 }}),
  reservationExpired: classifyJobLiveness({{ status: "queued", reservationMode: "host-forwarded", createdAt: "2026-06-09T00:08:00.000Z" }}, {{ now, queuedLostAfterMs: 100, reservationClaimMs: 1000 }}),
  terminal: isTerminalJobStatus("succeeded"),
  nonterminal: isTerminalJobStatus("running"),
  activeLimit: parsePositiveInteger("4", 3, {{ min: 1, max: 20 }}),
  invalidLimit: parsePositiveInteger("bad", 3, {{ min: 1, max: 20 }}),
  reservationLimit: reservationClaimMs({{ CLAUDE_FOR_CODEX_RESERVATION_CLAIM_MS: "5000" }}),
  key1: deriveJobIdempotencyKey({{ command: "review", args: ["--base", "main"], cwd: "/workspace/demo" }}),
  key2: deriveJobIdempotencyKey({{ command: "review", args: ["--base", "main"], cwd: "/workspace/demo" }}),
  wait: DEFAULT_BACKGROUND_WAIT_MS,
  hard: HARD_JOB_TIMEOUT_MS,
  reservationDefault: JOB_RESERVATION_CLAIM_MS
}};
console.log(JSON.stringify(payload));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["fresh"]["state"] == "healthy"
    assert payload["freshHeartbeatOldProgress"]["state"] == "healthy"
    assert payload["suspect"]["state"] == "suspect"
    assert payload["lost"]["state"] == "lost"
    assert payload["queued"]["state"] == "queued"
    assert payload["reservationStillQueued"]["state"] == "queued"
    assert payload["reservationExpired"]["state"] == "lost"
    assert payload["terminal"] is True
    assert payload["nonterminal"] is False
    assert payload["activeLimit"] == 4
    assert payload["invalidLimit"] == 3
    assert payload["reservationLimit"] == 5000
    assert payload["key1"] == payload["key2"]
    assert payload["key1"].startswith("sha256:")
    assert payload["wait"] <= 60_000
    assert payload["hard"] >= 30 * 60 * 1000
    assert payload["reservationDefault"] == 10 * 60 * 1000


def test_progress_events_parse_sanitize_and_count_malformed_lines(tmp_path):
    progress = PLUGIN / "scripts" / "lib" / "progress.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    script = f"""
import {{ formatProgressEvent, progressEventsFromLines }} from {json.dumps(progress.as_uri())};
const cwd = {json.dumps(str(repo))};
const good = formatProgressEvent({{ phase: "reviewing", message: "path " + cwd + " api_key='sk-progresssecret1234567890'", role: "security" }}, {{ cwd }}).trimEnd();
const parsed = progressEventsFromLines([good, "[claude-for-codex progress] {{bad-json", "[claude-for-codex progress typo"]);
console.log(JSON.stringify({{ good, parsed }}));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["good"].startswith("[claude-for-codex progress] ")
    assert str(repo) not in payload["good"]
    assert "sk-progresssecret" not in payload["good"]
    assert payload["parsed"]["events"][0]["phase"] == "reviewing"
    assert payload["parsed"]["events"][0]["role"] == "security"
    assert payload["parsed"]["malformedCount"] == 1
    assert payload["parsed"]["malformedPrefixCount"] == 1


def test_job_finish_sanitizes_output_and_progress_cannot_overwrite_terminal(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    secret = "api_key='sk-testsecretvalue1234567890'"
    script = f"""
import {{
  createJob,
  claimJobForRun,
  recordJobHeartbeat,
  recordJobProgress,
  finishJob,
  readJob,
  updateJob,
  updateJobUnlessTerminal
}} from {json.dumps(jobs.as_uri())};

const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const job = createJob(cwd, {{ command: "review", args: ["x"], cwd }}, env);
const claim = claimJobForRun(cwd, job.id, process.pid, env);
recordJobHeartbeat(cwd, job.id, {{ phase: "reviewing" }}, env);
recordJobProgress(cwd, job.id, {{ phase: "sdk-result", message: "path " + cwd + " {secret}", role: "security " + cwd }}, env);
const returnedUpdate = updateJob(cwd, job.id, {{
  cancelIdentity: {{ pid: 123, command: "node " + cwd + "/scripts/claude-companion.mjs __run-job " + job.id }},
  cancelChildIdentity: {{ pid: 124, command: "node " + cwd + "/scripts/claude-companion.mjs review" }},
  cancelWorkerIdentity: {{ pid: 123, command: "node " + cwd + "/scripts/claude-companion.mjs __run-job " + job.id }}
}}, env);
updateJobUnlessTerminal(cwd, job.id, {{ cancelFailureReason: "cancel path " + cwd + " {secret}" }}, env);
finishJob(cwd, job.id, {{
  status: 0,
  stdout: "ok " + cwd + " {secret}",
  stderr: "stderr " + cwd,
  error: "error {secret}"
}}, env);
const after = recordJobHeartbeat(cwd, job.id, {{ phase: "should-not-change" }}, env);
console.log(JSON.stringify({{ claim, returnedUpdate, after, stored: readJob(cwd, job.id, env) }}));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    stored_text = json.dumps(payload["stored"])
    assert payload["claim"]["status"] == "claimed"
    assert str(repo) not in payload["returnedUpdate"]["cancelIdentity"]["command"]
    assert str(repo) not in payload["returnedUpdate"]["cancelChildIdentity"]["command"]
    assert str(repo) not in payload["returnedUpdate"]["cancelWorkerIdentity"]["command"]
    assert payload["stored"]["status"] == "succeeded"
    assert payload["stored"]["phase"] == "succeeded"
    assert payload["stored"]["heartbeatSeq"] >= 1
    assert payload["stored"]["lastProgressAt"]
    assert "sk-testsecret" not in stored_text
    assert str(repo) not in payload["stored"]["stdout"]
    assert str(repo) not in payload["stored"].get("lastProgressMessage", "")
    assert str(repo) not in payload["stored"].get("lastProgressRole", "")
    assert str(repo) not in payload["stored"].get("cancelFailureReason", "")
    assert payload["after"]["phase"] == "succeeded"


def test_direct_and_reserved_claims_are_exclusive_and_share_submission_fields(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    companion = PLUGIN / "scripts" / "claude-companion.mjs"
    script = f"""
import {{ createJob, reserveJob, claimJobForRun, claimReservedJob, readJob, updateJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const direct = createJob(cwd, {{ command: "review", args: ["same"], cwd, idempotencyKey: "sha256:direct" }}, env);
const first = claimJobForRun(cwd, direct.id, 1111, env);
const second = claimJobForRun(cwd, direct.id, 2222, env);
const workerCommand = ["node", {json.dumps(str(companion))}, "run-reserved-job", "--job-id"];
const reserved = reserveJob(cwd, {{ command: "review", args: ["reserved"], cwd, idempotencyKey: "sha256:reserved" }}, workerCommand, env);
workerCommand.push(reserved.id);
updateJob(cwd, reserved.id, {{ workerCommand }}, env);
const reservedFirst = claimReservedJob(cwd, reserved.id, 3333, env);
const reservedSecond = claimReservedJob(cwd, reserved.id, 4444, env);
console.log(JSON.stringify({{
  first, second, reservedFirst, reservedSecond,
  directStored: readJob(cwd, direct.id, env),
  reservedStored: readJob(cwd, reserved.id, env)
}}));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["first"]["status"] == "claimed"
    assert payload["second"]["status"] == "not_claimed"
    assert payload["reservedFirst"]["status"] == "claimed"
    assert payload["reservedSecond"]["status"] == "not_claimed"
    for key in ["directStored", "reservedStored"]:
        assert payload[key]["status"] == "running"
        assert payload[key]["submissionState"] == "in-flight"
        assert payload[key]["submittedAt"]
        assert payload[key]["idempotencyKey"].startswith("sha256:")


def test_reserved_claim_refuses_same_key_active_direct_job(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    companion = PLUGIN / "scripts" / "claude-companion.mjs"
    script = f"""
import {{ createJob, reserveJob, claimJobForRun, claimReservedJob, readJob, updateJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const direct = createJob(cwd, {{ command: "review", args: ["same"], cwd, idempotencyKey: "sha256:same-key" }}, env);
const directClaim = claimJobForRun(cwd, direct.id, 1111, env);
const workerCommand = ["node", {json.dumps(str(companion))}, "run-reserved-job", "--job-id"];
const reserved = reserveJob(cwd, {{ command: "review", args: ["same"], cwd, idempotencyKey: "sha256:same-key" }}, workerCommand, env);
workerCommand.push(reserved.id);
updateJob(cwd, reserved.id, {{ workerCommand }}, env);
const reservedClaim = claimReservedJob(cwd, reserved.id, 2222, env);
console.log(JSON.stringify({{
  directClaim,
  reservedClaim,
  directStored: readJob(cwd, direct.id, env),
  reservedStored: readJob(cwd, reserved.id, env)
}}));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["directClaim"]["status"] == "claimed"
    assert payload["reservedClaim"]["status"] == "not_claimed"
    assert "active direct job" in payload["reservedClaim"]["reason"]
    assert payload["directStored"]["status"] == "running"
    assert payload["reservedStored"]["status"] == "queued"


def test_claim_job_for_run_is_exclusive_across_processes(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    start = tmp_path / "start"
    repo.mkdir()
    data.mkdir()
    create = f"""
import {{ createJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
console.log(createJob(cwd, {{ command: "review", args: ["same"], cwd }}, env).id);
"""
    job_id = subprocess.run([NODE, "--input-type=module", "--eval", create], capture_output=True, text=True, check=True).stdout.strip()
    worker = f"""
import fs from "node:fs";
import {{ claimJobForRun }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
while (!fs.existsSync({json.dumps(str(start))})) {{
  await new Promise((resolve) => setTimeout(resolve, 5));
}}
console.log(JSON.stringify(claimJobForRun(cwd, {json.dumps(job_id)}, process.pid, env)));
"""
    procs = [subprocess.Popen([NODE, "--input-type=module", "--eval", worker], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    start.write_text("go", encoding="utf8")
    outputs = [proc.communicate(timeout=10) + (proc.returncode,) for proc in procs]
    assert all(row[2] == 0 for row in outputs), outputs
    statuses = [json.loads(row[0])["status"] for row in outputs]
    assert statuses.count("claimed") == 1
    assert statuses.count("not_claimed") == 1


def test_claim_job_lock_contention_returns_structured_busy_not_exception(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    script = f"""
import fs from "node:fs";
import path from "node:path";
import {{ createJob, claimJobForRun, listJobs }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const job = createJob(cwd, {{ command: "review", args: ["same"], cwd }}, env);
const state = listJobs(cwd, env);
const lockFile = path.join(state.stateDir, "jobs", `${{job.id}}.json.lock`);
const fd = fs.openSync(lockFile, "wx", 0o600);
let claim;
try {{
  claim = claimJobForRun(cwd, job.id, process.pid, env);
}} finally {{
  fs.closeSync(fd);
  fs.rmSync(lockFile, {{ force: true }});
}}
console.log(JSON.stringify(claim));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] in {"locked", "not_claimed"}


def test_job_lock_does_not_remove_stale_lock_when_owner_is_alive(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    script = f"""
import fs from "node:fs";
import path from "node:path";
import {{ createJob, claimJobForRun, listJobs }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const job = createJob(cwd, {{ command: "review", args: ["same"], cwd }}, env);
const state = listJobs(cwd, env);
const lockFile = path.join(state.stateDir, "jobs", `${{job.id}}.json.lock`);
fs.writeFileSync(lockFile, JSON.stringify({{ pid: process.pid, createdAt: "2026-06-09T00:00:00.000Z" }}));
const old = new Date(Date.now() - 120000);
fs.utimesSync(lockFile, old, old);
const claim = claimJobForRun(cwd, job.id, process.pid, env);
const stillLocked = fs.existsSync(lockFile);
fs.rmSync(lockFile, {{ force: true }});
console.log(JSON.stringify({{ claim, stillLocked }}));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["claim"]["status"] == "locked"
    assert payload["stillLocked"] is True


def test_workspace_lock_does_not_remove_live_owner_after_stale_mtime(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env_json = json.dumps({"CLAUDE_PLUGIN_DATA": str(data), "HOME": str(tmp_path / "home")})
    holder_script = f"""
import {{ withWorkspaceJobLock, listJobs }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {env_json};
const state = listJobs(cwd, env);
console.log(JSON.stringify({{ stateDir: state.stateDir }}));
const block = new Int32Array(new SharedArrayBuffer(4));
withWorkspaceJobLock(cwd, env, () => {{
  console.log("LOCK_HELD");
  Atomics.wait(block, 0, 0, 5000);
  return {{ status: "released" }};
}});
"""
    holder = subprocess.Popen(
        [NODE, "--input-type=module", "--eval", holder_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        state_line = holder.stdout.readline().strip()
        assert state_line, holder.stderr.read()
        state_dir = pathlib.Path(json.loads(state_line)["stateDir"])
        assert holder.stdout.readline().strip() == "LOCK_HELD"
        lock_file = state_dir / "jobs" / ".workspace.lock.lock"
        old = time.time() - 120
        os.utime(lock_file, (old, old))

        contender_script = f"""
import {{ withWorkspaceJobLock }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {env_json};
const result = withWorkspaceJobLock(cwd, env, () => ({{ status: "entered" }}));
console.log(JSON.stringify(result));
"""
        contender = subprocess.run(
            [NODE, "--input-type=module", "--eval", contender_script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert contender.returncode == 0, contender.stderr
        payload = json.loads(contender.stdout)
        assert payload["status"] == "workspace_locked"
        assert lock_file.exists()
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=5)


def test_background_worker_heartbeats_during_no_output_child(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('claude fake')\n"
        "    raise SystemExit(0)\n"
        "time.sleep(2)\n"
        "print('NO_OUTPUT_FINISHED')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["CLAUDE_FOR_CODEX_HEARTBEAT_INTERVAL_MS"] = "100"
    started = subprocess.run([NODE, str(runtime), "review", "--background", "slow no output"], cwd=repo, env=env, capture_output=True, text=True, timeout=5)
    assert started.returncode == 0, started.stderr
    job_id = json.loads(started.stdout)["job"]["id"]
    observed = None
    for _ in range(30):
        listed = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True)
        observed = next(job for job in json.loads(listed.stdout)["jobs"] if job["id"] == job_id)
        if observed.get("heartbeatSeq", 0) >= 2:
            break
        time.sleep(0.1)
    assert observed["status"] in {"running", "succeeded"}
    assert observed["heartbeatSeq"] >= 2
    assert observed["lastHeartbeatAt"]


def test_background_worker_line_buffers_split_progress_events(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('claude fake')\n"
        "    raise SystemExit(0)\n"
        "sys.stderr.write('[claude-for-codex progress] {\"phase\":\"review')\n"
        "sys.stderr.flush()\n"
        "time.sleep(0.1)\n"
        "sys.stderr.write('ing\",\"message\":\"role started\"}\\n')\n"
        "sys.stderr.flush()\n"
        "print('PROGRESS_DONE')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    started = subprocess.run([NODE, str(runtime), "review", "--background", "--wait", "progress"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
    assert started.returncode == 0, started.stderr
    payload = json.loads(started.stdout)
    assert payload["job"]["status"] == "succeeded"
    assert payload["job"]["lastProgressAt"]
    assert payload["job"]["phase"] == "succeeded"
    assert payload["job"]["lastProgressMessage"] == "role started"


def test_background_worker_fast_child_exit_does_not_fail_identity_capture(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('claude fake')\n"
        "    raise SystemExit(0)\n"
        "print('FAST_DONE')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run([NODE, str(runtime), "review", "--background", "--wait", "--wait-timeout-ms", "3000", "fast-exit"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["job"]["status"] == "succeeded"
    assert "FAST_DONE" in payload["job"]["stdout"]
    assert "identity could not be validated" not in payload["job"].get("error", "")


def test_background_worker_hard_timeout_escalates_to_sigkill(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import signal, sys, time\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('claude fake')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, lambda signum, frame: None)\n"
        "while True:\n"
        "    time.sleep(1)\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["CLAUDE_FOR_CODEX_HARD_TIMEOUT_MS"] = "1000"
    env["CLAUDE_FOR_CODEX_KILL_GRACE_MS"] = "100"
    result = subprocess.run([NODE, str(runtime), "review", "--background", "--wait", "--wait-timeout-ms", "3000", "timeout"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["job"]["status"] == "failed"
    assert "timeout" in payload["job"].get("error", "").lower() or "SIGKILL" in payload["job"].get("error", "")


def test_background_worker_hard_timeout_cannot_succeed_after_sigterm_zero_exit(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import signal, sys, time\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('claude fake')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))\n"
        "while True:\n"
        "    time.sleep(1)\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["CLAUDE_FOR_CODEX_HARD_TIMEOUT_MS"] = "1000"
    env["CLAUDE_FOR_CODEX_KILL_GRACE_MS"] = "100"
    result = subprocess.run([NODE, str(runtime), "review", "--background", "--wait", "--wait-timeout-ms", "3000", "timeout-zero"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["job"]["status"] == "failed"
    assert payload["job"]["exitStatus"] != 0
    assert "hard timeout" in payload["job"].get("error", "")


def test_background_worker_hard_timeout_kills_surviving_process_group_member(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    child_pid_file = tmp_path / "survivor.pid"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        f"""#!/usr/bin/env python3
import pathlib
import signal
import subprocess
import sys
import time

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)

child = subprocess.Popen([
    sys.executable,
    "-c",
    "import signal,time; signal.signal(signal.SIGTERM, lambda s,f: None); time.sleep(30)",
])
pathlib.Path({json.dumps(str(child_pid_file))}).write_text(str(child.pid))
signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
while True:
    time.sleep(1)
""",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["CLAUDE_FOR_CODEX_HARD_TIMEOUT_MS"] = "1000"
    env["CLAUDE_FOR_CODEX_KILL_GRACE_MS"] = "200"

    result = subprocess.run(
        [NODE, str(runtime), "review", "--background", "--wait", "--wait-timeout-ms", "4000", "timeout-survivor"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["job"]["status"] == "failed"
    survivor_pid = int(child_pid_file.read_text())
    for _ in range(30):
        if not process_is_running(survivor_pid):
            break
        time.sleep(0.1)
    assert not process_is_running(survivor_pid)


def test_background_worker_caps_noisy_output_before_finish(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('claude fake')\n"
        "    raise SystemExit(0)\n"
        "sys.stdout.write('x' * (1024 * 1024 + 4096))\n"
        "sys.stderr.write('y' * (1024 * 1024 + 8192))\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    listed = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True, check=True)
    state_dir = pathlib.Path(json.loads(listed.stdout)["stateDir"])
    result = subprocess.run(
        [NODE, str(runtime), "review", "--background", "noisy"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    job_id = payload["job"]["id"]
    job_file = state_dir / "jobs" / f"{job_id}.json"
    job = {}
    for _ in range(50):
        job = json.loads(job_file.read_text())
        if job["status"] == "succeeded":
            break
        time.sleep(0.1)
    assert job["status"] == "succeeded"
    assert job["stdoutTruncated"] is True
    assert job["stderrTruncated"] is True
    assert job["stdoutBytes"] > job["stdoutStoredBytes"]
    assert job["stderrBytes"] > job["stderrStoredBytes"]
    assert job["stdoutStoredBytes"] <= 1024 * 1024
    assert job["stderrStoredBytes"] <= 1024 * 1024


def test_foreground_review_still_invokes_claude_after_async_background_refactor(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    marker = tmp_path / "foreground-called.txt"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        f"#!/usr/bin/env python3\nimport pathlib, sys\nif sys.argv[1:] == ['--version']:\n    print('claude fake')\n    raise SystemExit(0)\npathlib.Path({json.dumps(str(marker))}).write_text('called')\nprint('FOREGROUND_OK')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run([NODE, str(runtime), "review", "foreground check"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert marker.exists()
    assert "FOREGROUND_OK" in result.stdout


def test_background_wait_timeout_returns_running_job_without_killing_worker(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    marker = tmp_path / "finished.txt"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        f"#!/usr/bin/env python3\nimport pathlib, sys, time\nif sys.argv[1:] == ['--version']:\n    print('claude fake')\n    raise SystemExit(0)\ntime.sleep(1)\npathlib.Path({json.dumps(str(marker))}).write_text('done')\nprint('LATE_DONE')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["CLAUDE_FOR_CODEX_HEARTBEAT_INTERVAL_MS"] = "100"
    waited = subprocess.run([NODE, str(runtime), "review", "--background", "--wait", "--wait-timeout-ms", "100", "slow"], cwd=repo, env=env, capture_output=True, text=True, timeout=5)
    assert waited.returncode == 0, waited.stderr
    payload = json.loads(waited.stdout)
    assert payload["status"] == "running"
    assert payload["waitTimedOut"] is True
    assert payload["job"]["id"]
    assert not marker.exists()
    current = None
    for _ in range(30):
        result = subprocess.run([NODE, str(runtime), "result", payload["job"]["id"]], cwd=repo, env=env, capture_output=True, text=True)
        current = json.loads(result.stdout)["job"]
        if current["status"] == "succeeded":
            break
        time.sleep(0.1)
    assert marker.exists()
    assert current["stdout"].strip() == "LATE_DONE"


def test_background_wait_reports_missing_or_corrupt_job_state_nonzero(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")

    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    jobs = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True, check=True)
    state_dir = pathlib.Path(json.loads(jobs.stdout)["stateDir"])

    fake_worker = bin_dir / "fake-node"
    fake_worker.write_text(
        "#!/usr/bin/env sh\n"
        "job_id=''\n"
        "for arg in \"$@\"; do job_id=\"$arg\"; done\n"
        "job_file=\"$TEST_STATE_DIR/jobs/$job_id.json\"\n"
        "i=0\n"
        "while [ \"$i\" -lt 50 ]; do\n"
        "  grep -q '\"workerPid\"' \"$job_file\" 2>/dev/null && break\n"
        "  i=$((i + 1))\n"
        "  sleep 0.02\n"
        "done\n"
        "if [ \"$TEST_WORKER_MODE\" = delete ]; then\n"
        "  rm -f \"$job_file\"\n"
        "else\n"
        "  printf '{bad json' > \"$job_file\"\n"
        "fi\n"
        "sleep 1\n",
        encoding="utf8",
    )
    fake_worker.chmod(0o755)

    base_env = {
        **env,
        "CLAUDE_FOR_CODEX_WORKER_NODE": str(fake_worker),
        "TEST_STATE_DIR": str(state_dir),
    }
    for mode, expected_status in [("delete", "unknown"), ("corrupt", "corrupt")]:
        result = subprocess.run(
            [NODE, str(runtime), "review", "--background", "--wait", "--wait-timeout-ms", "1500", f"{mode}-state"],
            cwd=repo,
            env={**base_env, "TEST_WORKER_MODE": mode},
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 1, result.stderr
        payload = json.loads(result.stdout)
        assert payload["status"] == expected_status
        assert payload["waitTimedOut"] is True
        assert payload["job"]["status"] == expected_status


def test_background_wait_exits_nonzero_when_job_is_cancelled(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_worker = tmp_path / "fake-node"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_worker.write_text("#!/usr/bin/env sh\nsleep 5\n", encoding="utf8")
    fake_worker.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_WORKER_NODE"] = str(fake_worker)
    waited = subprocess.Popen(
        [NODE, str(runtime), "review", "--background", "--wait", "--wait-timeout-ms", "5000", "cancel-wait"],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        job_id = None
        for _ in range(50):
            listed = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True)
            jobs_payload = json.loads(listed.stdout)
            if jobs_payload["jobs"]:
                job_id = jobs_payload["jobs"][0]["id"]
                break
            time.sleep(0.1)
        assert job_id
        cancelled = subprocess.run([NODE, str(runtime), "cancel", job_id], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
        assert cancelled.returncode == 0, cancelled.stderr
        stdout, stderr = waited.communicate(timeout=10)
        assert waited.returncode == 1, stderr
        payload = json.loads(stdout)
        assert payload["status"] == "cancelled"
        assert payload["waitTimedOut"] is False
    finally:
        if waited.poll() is None:
            waited.terminate()
            waited.wait(timeout=5)


def test_wait_timeout_args_are_not_persisted_or_forwarded_to_child(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    args_file = tmp_path / "args.txt"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        f"#!/usr/bin/env python3\nimport pathlib, sys\npathlib.Path({json.dumps(str(args_file))}).write_text(' '.join(sys.argv[1:]))\nprint('OK')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run([NODE, str(runtime), "review", "--background", "--wait", "--wait-timeout-ms=5000", "strip"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "--wait-timeout-ms" not in " ".join(payload["job"].get("args", []))
    assert "--wait-timeout-ms" not in args_file.read_text(encoding="utf8")


def test_wait_timeout_is_clamped_below_hard_timeout_in_source():
    lifecycle = (PLUGIN / "scripts" / "lib" / "job-lifecycle.mjs").read_text(encoding="utf8")
    companion = (PLUGIN / "scripts" / "claude-companion.mjs").read_text(encoding="utf8")
    assert "MAX_BACKGROUND_WAIT_MS" in lifecycle
    assert "MAX_BACKGROUND_WAIT_MS" in companion
    wait_block = companion[companion.index("async function waitForJob"):companion.index("async function maybeStartBackground")]
    assert "max: MAX_BACKGROUND_WAIT_MS" in wait_block
    assert "max: HARD_JOB_TIMEOUT_MS" not in wait_block


def test_post_spawn_worker_stamp_is_terminal_safe_and_lock_tolerant_in_source():
    companion = (PLUGIN / "scripts" / "claude-companion.mjs").read_text(encoding="utf8")
    block = companion[companion.index("function startBackgroundJob"):companion.index("function reserveBackgroundJob")]
    assert "updateJobUnlessTerminal(cwd, job.id" in block
    assert 'stamped.status === "locked"' in block
    assert "workerPidUpdatePending" in block
    assert "return updateJob(cwd, job.id" not in block


def test_recommend_execution_mode_routes_large_reviews_to_background(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "file.txt").write_text("base\n", encoding="utf8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("base\n" + "changed\n" * 60, encoding="utf8")
    result = subprocess.run([NODE, str(runtime), "recommend-execution-mode", "--json"], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["recommendation"] == "background"
    assert payload["changedLineEstimate"] >= 50
    assert payload["reviewable"] is True


def test_recommend_execution_mode_routes_committed_base_diff_to_background(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "base.txt").write_text("base\n", encoding="utf8")
    subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "branch", "base-ref"], cwd=repo, check=True)
    for index in range(3):
        (repo / f"changed-{index}.txt").write_text(f"changed {index}\n", encoding="utf8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=repo, check=True, capture_output=True, text=True)

    result = subprocess.run(
        [NODE, str(runtime), "recommend-execution-mode", "--json", "review", "--base", "base-ref"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["recommendation"] == "background"
    assert payload["reviewable"] is True
    assert payload["fileCountEstimate"] >= 3
    assert payload["git"]["base"] == "base-ref"
    assert payload["git"]["baseDiffAvailable"] is True


def test_recommend_execution_mode_distinguishes_git_timeout_from_non_repo(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env sh\n"
        "if [ \"$1\" = \"rev-parse\" ]; then echo true; exit 0; fi\n"
        "sleep 2\n",
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["CLAUDE_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS"] = "100"
    result = subprocess.run([NODE, str(runtime), "recommend-execution-mode", "--json"], cwd=repo, env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["recommendation"] == "background"
    assert payload["reviewable"] is False
    assert payload["git"]["timedOut"] is True
    assert "timed out" in payload["reason"]


def test_jobs_and_result_include_lifecycle_elapsed_and_progress_preview(tmp_path):
    import datetime

    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    jobs = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True)
    state_dir = pathlib.Path(json.loads(jobs.stdout)["stateDir"])
    job_file = state_dir / "jobs" / "job-lifecycle.json"
    job_file.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    created = (now - datetime.timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
    started = (now - datetime.timedelta(seconds=9)).isoformat().replace("+00:00", "Z")
    updated = (now - datetime.timedelta(seconds=8)).isoformat().replace("+00:00", "Z")
    job_file.write_text(json.dumps({
        "id": "job-lifecycle",
        "status": "running",
        "command": "review",
        "args": ["focus"],
        "cwd": str(repo),
        "createdAt": created,
        "startedAt": started,
        "updatedAt": updated,
        "lastHeartbeatAt": updated,
        "heartbeatSeq": 3,
        "phase": "reviewing",
        "lastProgressMessage": "security started"
    }), encoding="utf8")
    listed = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True)
    job = next(item for item in json.loads(listed.stdout)["jobs"] if item["id"] == "job-lifecycle")
    assert job["lifecycle"]["state"] in {"healthy", "suspect", "lost"}
    assert job["phase"] == "reviewing"
    assert job["heartbeatSeq"] == 3
    assert job["progressPreview"] == ["security started"]
    assert job["elapsedMs"] > 0
    result = subprocess.run([NODE, str(runtime), "result", "job-lifecycle"], cwd=repo, env=env, capture_output=True, text=True)
    result_job = json.loads(result.stdout)["job"]
    assert result_job["lifecycle"]["state"] in {"healthy", "suspect", "lost"}
    assert result_job["progressPreview"] == ["security started"]


def test_background_start_respects_active_job_limit_without_spawning(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    spawn_marker = tmp_path / "spawn-count.txt"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        f"#!/usr/bin/env python3\nimport pathlib, time\npath = pathlib.Path({json.dumps(str(spawn_marker))})\npath.write_text(path.read_text() + 'x' if path.exists() else 'x')\ntime.sleep(5)\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["CLAUDE_FOR_CODEX_MAX_ACTIVE_JOBS"] = "1"
    first = subprocess.run([NODE, str(runtime), "review", "--background", "one"], cwd=repo, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run([NODE, str(runtime), "review", "--background", "two"], cwd=repo, env=env, capture_output=True, text=True)
    assert second.returncode == 2
    payload = json.loads(second.stdout)
    assert payload["status"] == "capacity_blocked"
    assert payload["activeCount"] == 1


def test_duplicate_background_retry_reuses_existing_job_without_second_spawn(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    spawn_marker = tmp_path / "spawn-count.txt"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        f"#!/usr/bin/env python3\nimport pathlib, time\npath = pathlib.Path({json.dumps(str(spawn_marker))})\nwith path.open('a', encoding='utf8') as handle:\n    handle.write('x')\ntime.sleep(5)\nprint('done')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    first = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    second = subprocess.run([NODE, str(runtime), "review", "--background", "--wait", "--wait-timeout-ms", "100", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    second_payload = json.loads(second.stdout)
    assert second_payload["job"]["id"] == first_payload["job"]["id"]
    assert second_payload["job"].get("reusedExisting") is True
    for _ in range(20):
        if spawn_marker.exists():
            break
        time.sleep(0.1)
    assert spawn_marker.read_text(encoding="utf8") == "x"


def test_background_idempotency_changes_when_worktree_changes(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    spawn_marker = tmp_path / "spawn-count.txt"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change one\n", encoding="utf8")
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        f"#!/usr/bin/env python3\nimport pathlib, time\npath = pathlib.Path({json.dumps(str(spawn_marker))})\nwith path.open('a', encoding='utf8') as handle:\n    handle.write('x')\ntime.sleep(5)\nprint('done')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    first = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    (repo / "changed.txt").write_text("change two\n", encoding="utf8")
    second = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    second_payload = json.loads(second.stdout)
    assert second_payload["job"]["id"] != first_payload["job"]["id"]
    assert second_payload["job"].get("reusedExisting") is not True
    for _ in range(100):
        if spawn_marker.exists() and len(spawn_marker.read_text(encoding="utf8")) >= 2:
            break
        time.sleep(0.1)
    assert len(spawn_marker.read_text(encoding="utf8")) >= 2


@pytest.mark.parametrize(
    ("first_extra_env", "second_extra_env"),
    [
        ({"CLAUDE_FOR_CODEX_QUALITY": "standard"}, {"CLAUDE_FOR_CODEX_QUALITY": "max"}),
        ({"CLAUDE_FOR_CODEX_TOP_MODEL": "opus"}, {"CLAUDE_FOR_CODEX_TOP_MODEL": "fable"}),
        (
            {"CLAUDE_FOR_CODEX_TOP_MODEL_FALLBACK": "opus"},
            {"CLAUDE_FOR_CODEX_TOP_MODEL_FALLBACK": "opus,sonnet"},
        ),
    ],
)
def test_background_idempotency_changes_when_execution_controls_change(
    tmp_path, first_extra_env, second_extra_env
):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\nimport sys, time\nif sys.argv[1:] == ['--version']:\n    print('claude fake')\n    raise SystemExit(0)\ntime.sleep(5)\nprint('done')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    first_env = {**env, **first_extra_env}
    second_env = {**env, **second_extra_env}
    first = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=first_env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=second_env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert second_payload["job"]["id"] != first_payload["job"]["id"]
    assert second_payload["job"].get("reusedExisting") is not True


def test_background_idempotency_does_not_reuse_when_fingerprint_times_out(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_worker = bin_dir / "fake-node"
    fake_worker.write_text("#!/usr/bin/env sh\nsleep 5\n", encoding="utf8")
    fake_worker.chmod(0o755)
    fake_git = bin_dir / "git"
    fake_git.write_text("#!/usr/bin/env sh\nsleep 1\nexit 0\n", encoding="utf8")
    fake_git.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_WORKER_NODE"] = str(fake_worker)
    env["CLAUDE_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS"] = "100"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    first = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["job"]["id"] != second_payload["job"]["id"]
    assert first_payload["job"]["fingerprintTimedOut"] is True
    assert second_payload["job"]["fingerprintTimedOut"] is True
    assert first_payload["job"]["fingerprintReuseDisabled"] is True
    assert second_payload["job"]["fingerprintReuseDisabled"] is True
    assert second_payload["job"].get("reusedExisting") is not True


def test_worktree_fingerprint_marks_untracked_budget_exceeded_inconclusive(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "one.txt").write_text("12345\n", encoding="utf8")
    module_uri = (PLUGIN / "scripts" / "lib" / "worktree-fingerprint.mjs").as_uri()
    script = f"""
import {{ workingTreeFingerprintDetails }} from {json.dumps(module_uri)};
const details = workingTreeFingerprintDetails({json.dumps(str(repo))}, [], {{
  env: {{ ...process.env, CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_BYTES: "4" }}
}});
console.log(JSON.stringify(details));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    details = json.loads(result.stdout)
    assert details["timedOut"] is False
    assert details["untrusted"] is True
    assert details["reuseDisabled"] is True
    assert details["budgetExceeded"] is True
    assert details["hash"]


def test_worktree_fingerprint_marks_non_timeout_git_failures_untrusted(tmp_path):
    repo = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/usr/bin/env sh\n"
        "echo 'fatal: synthetic git failure' >&2\n"
        "exit 2\n",
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    module_uri = (PLUGIN / "scripts" / "lib" / "worktree-fingerprint.mjs").as_uri()
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    script = f"""
import {{ workingTreeFingerprintDetails }} from {json.dumps(module_uri)};
const details = workingTreeFingerprintDetails({json.dumps(str(repo))});
console.log(JSON.stringify(details));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    details = json.loads(result.stdout)
    assert details["timedOut"] is False
    assert details["untrusted"] is True
    assert details["reuseDisabled"] is True
    assert details["failureKind"] == "inconclusive"
    assert details["hash"]


def test_background_idempotency_does_not_reuse_when_untracked_budget_exceeded(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "large-untracked.txt").write_text("12345\n", encoding="utf8")
    fake_worker = bin_dir / "fake-node"
    fake_worker.write_text("#!/usr/bin/env sh\nsleep 5\n", encoding="utf8")
    fake_worker.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_WORKER_NODE"] = str(fake_worker)
    env["CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_BYTES"] = "4"
    first = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["job"]["id"] != second_payload["job"]["id"]
    assert first_payload["job"]["fingerprintTimedOut"] is False
    assert second_payload["job"]["fingerprintTimedOut"] is False
    assert first_payload["job"]["fingerprintReuseDisabled"] is True
    assert second_payload["job"]["fingerprintReuseDisabled"] is True
    assert second_payload["job"].get("reusedExisting") is not True


def test_duplicate_background_retry_reuses_existing_job_in_non_git_workspace(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_worker = bin_dir / "fake-node"
    fake_worker.write_text("#!/usr/bin/env sh\nsleep 5\n", encoding="utf8")
    fake_worker.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_WORKER_NODE"] = str(fake_worker)
    first = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run([NODE, str(runtime), "review", "--background", "--wait", "--wait-timeout-ms", "100", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert second_payload["job"]["id"] == first_payload["job"]["id"]
    assert second_payload["job"].get("reusedExisting") is True
    assert second_payload["job"]["fingerprintTimedOut"] is False
    assert second_payload["job"].get("fingerprintReuseDisabled") in (False, None)


def test_background_idempotency_does_not_reuse_when_git_returns_nonzero(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("base\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "tracked.txt").write_text("change\n", encoding="utf8")
    fake_worker = bin_dir / "fake-node"
    fake_worker.write_text("#!/usr/bin/env sh\nsleep 5\n", encoding="utf8")
    fake_worker.chmod(0o755)
    real_git = shutil.which("git")
    assert real_git
    fake_git = bin_dir / "git"
    fake_git.write_text(
        f"""#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
if args[:1] == ["status"]:
    print("fatal: synthetic status failure", file=sys.stderr)
    raise SystemExit(128)
os.execv({json.dumps(real_git)}, ["git", *args])
""",
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_WORKER_NODE"] = str(fake_worker)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    first = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["job"]["id"] != second_payload["job"]["id"]
    assert first_payload["job"]["fingerprintTimedOut"] is False
    assert second_payload["job"]["fingerprintTimedOut"] is False
    assert first_payload["job"]["fingerprintReuseDisabled"] is True
    assert second_payload["job"]["fingerprintReuseDisabled"] is True
    assert second_payload["job"].get("reusedExisting") is not True


def test_background_idempotency_changes_when_head_or_base_ref_changes(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "file.txt").write_text("base\n", encoding="utf8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("change one\n", encoding="utf8")
    subprocess.run(["git", "commit", "-am", "change-one"], cwd=repo, check=True, capture_output=True, text=True)
    fake_worker = bin_dir / "fake-node"
    fake_worker.write_text("#!/usr/bin/env sh\nsleep 5\n", encoding="utf8")
    fake_worker.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_WORKER_NODE"] = str(fake_worker)
    first = subprocess.run([NODE, str(runtime), "review", "--background", "--base", "HEAD~1", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    (repo / "file.txt").write_text("change two\n", encoding="utf8")
    subprocess.run(["git", "commit", "-am", "change-two"], cwd=repo, check=True, capture_output=True, text=True)
    second = subprocess.run([NODE, str(runtime), "review", "--background", "--base", "HEAD~1", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["job"]["id"] != second_payload["job"]["id"]
    assert second_payload["job"].get("reusedExisting") is not True


def test_background_worker_launch_failure_does_not_leave_active_job(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_WORKER_NODE"] = str(tmp_path / "missing-node")
    result = subprocess.run([NODE, str(runtime), "review", "--background", "launch-fails"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "launch_failed"
    assert payload["job"]["status"] == "failed"
    jobs = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True, check=True)
    assert "launch-fails" not in jobs.stdout or "failed" in jobs.stdout


def test_background_jobs_fail_fast_when_posix_process_groups_are_unavailable(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_PROCESS_PLATFORM"] = "win32"
    started = subprocess.run([NODE, str(runtime), "review", "--background", "unsupported"], cwd=repo, env=env, capture_output=True, text=True)
    assert started.returncode == 2
    payload = json.loads(started.stdout)
    assert payload["status"] == "unsupported_platform"
    assert payload["platform"] == "win32"
    assert "POSIX process groups" in payload["message"]
    jobs = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True, check=True)
    assert json.loads(jobs.stdout)["jobs"] == []


def test_reserve_job_fails_fast_when_posix_process_groups_are_unavailable(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_PROCESS_PLATFORM"] = "win32"
    reserved = subprocess.run([NODE, str(runtime), "reserve-job", "review", "unsupported"], cwd=repo, env=env, capture_output=True, text=True)
    assert reserved.returncode == 2
    assert "POSIX process groups" in reserved.stderr


def test_background_worker_bootstrap_exit_is_reaped_from_queued(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_worker = tmp_path / "fake-node"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_worker.write_text("#!/usr/bin/env sh\nexit 1\n", encoding="utf8")
    fake_worker.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_WORKER_NODE"] = str(fake_worker)
    env["CLAUDE_FOR_CODEX_QUEUED_LOST_AFTER_MS"] = "100"
    started = subprocess.run([NODE, str(runtime), "review", "--background", "bootstrap-exits"], cwd=repo, env=env, capture_output=True, text=True)
    assert started.returncode == 0, started.stderr
    time.sleep(1.2)
    listed = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True, check=True)
    job = next(item for item in json.loads(listed.stdout)["jobs"] if item["args"] == ["bootstrap-exits"])
    assert job["status"] == "failed"
    assert job["phase"] == "worker-launch-failed"


def test_abandoned_host_forwarded_reservation_is_reaped_from_queued(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_QUEUED_LOST_AFTER_MS"] = "100"
    env["CLAUDE_FOR_CODEX_RESERVATION_CLAIM_MS"] = "1000"

    reserved = subprocess.run(
        [NODE, str(runtime), "reserve-job", "review", "abandoned"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert reserved.returncode == 0, reserved.stderr
    job_id = json.loads(reserved.stdout)["job"]["id"]

    time.sleep(1.5)
    listed = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True, check=True)
    job = next(item for item in json.loads(listed.stdout)["jobs"] if item["id"] == job_id)
    assert job["status"] == "failed"
    assert job["phase"] == "reservation-expired"
    assert "reserved job was not claimed" in job["error"]


def test_reaper_does_not_expire_reserved_job_claimed_after_snapshot(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    script = f"""
	import {{ claimReservedJob, reapLostJobs, readJob, reserveJob }} from {json.dumps(jobs.as_uri())};
	const cwd = {json.dumps(str(repo))};
	const env = {{
	  CLAUDE_PLUGIN_DATA: {json.dumps(str(data))},
	  HOME: {json.dumps(str(tmp_path / "home"))},
	  CLAUDE_FOR_CODEX_ENABLE_TEST_SEAMS: "1"
	}};
reserveJob(cwd, {{
  id: "job-race",
  command: "review",
  args: ["race"],
  cwd,
  createdAt: "2026-06-09T00:00:00.000Z",
  updatedAt: "2026-06-09T00:00:00.000Z"
}}, [{json.dumps(NODE)}, {json.dumps(str(runtime))}, "run-reserved-job", "--job-id", "job-race"], env);
let callbackCount = 0;
const updates = reapLostJobs(cwd, {{
  now: Date.parse("2026-06-09T00:20:00.000Z"),
  beforeLostJobUpdate: (job) => {{
    if (job.id === "job-race") {{
      callbackCount += 1;
      claimReservedJob(cwd, job.id, process.pid, env);
    }}
  }}
}}, env);
console.log(JSON.stringify({{ callbackCount, updates, job: readJob(cwd, "job-race", env) }}));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["callbackCount"] == 1
    assert payload["job"]["status"] == "running"
    assert payload["job"]["phase"] == "starting"
    assert payload["job"].get("error") is None
    assert payload["job"].get("finishedAt") is None


def test_reaper_prunes_old_terminal_jobs_from_snapshot(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    script = f"""
import fs from "node:fs";
import path from "node:path";
import {{ createJob, listJobs, reapLostJobs, readJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{
  CLAUDE_PLUGIN_DATA: {json.dumps(str(data))},
  HOME: {json.dumps(str(tmp_path / "home"))},
  CLAUDE_FOR_CODEX_TERMINAL_JOB_RETENTION_MS: "1"
}};
const state = listJobs(cwd, env);
const oldDone = {{
  id: "old-done",
  status: "succeeded",
  command: "review",
  args: ["old"],
  cwd,
  createdAt: "2026-06-09T00:00:00.000Z",
  updatedAt: "2026-06-09T00:00:00.000Z",
  finishedAt: "2026-06-09T00:00:00.000Z",
  resultViewedAt: "2026-06-09T00:00:01.000Z",
  stdout: "old",
  stderr: ""
}};
fs.writeFileSync(path.join(state.stateDir, "jobs", "old-done.json"), JSON.stringify(oldDone, null, 2));
const unreadDone = {{
  id: "unread-done",
  status: "succeeded",
  command: "review",
  args: ["unread"],
  cwd,
  createdAt: "2026-06-09T00:00:00.000Z",
  updatedAt: "2026-06-09T00:00:00.000Z",
  finishedAt: "2026-06-09T00:00:00.000Z",
  stdout: "unread",
  stderr: ""
}};
fs.writeFileSync(path.join(state.stateDir, "jobs", "unread-done.json"), JSON.stringify(unreadDone, null, 2));
const active = createJob(cwd, {{
  id: "active-job",
  command: "review",
  args: ["active"],
  cwd,
  createdAt: "2026-06-10T00:00:00.000Z",
  updatedAt: "2026-06-10T00:00:00.000Z"
}}, env);
const snapshot = listJobs(cwd, env).jobs;
const updates = reapLostJobs(cwd, {{ jobs: snapshot, now: Date.parse("2026-06-10T00:00:00.010Z") }}, env);
console.log(JSON.stringify({{
  updates,
  oldDone: readJob(cwd, oldDone.id, env),
  unreadDone: readJob(cwd, unreadDone.id, env),
  active: readJob(cwd, active.id, env),
  remainingIds: listJobs(cwd, env).jobs.map((job) => job.id).sort()
}}));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["oldDone"] is None
    assert payload["unreadDone"]["status"] == "succeeded"
    assert payload["active"]["status"] == "queued"
    assert payload["remainingIds"] == ["active-job", "unread-done"]


def test_claim_queued_job_without_idempotency_keeps_empty_key(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    script = f"""
import {{ claimJobForRun, createJob, readJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const job = createJob(cwd, {{ id: "legacy-no-key", command: "review", args: ["legacy"], cwd }}, env);
const claimed = claimJobForRun(cwd, job.id, process.pid, env);
console.log(JSON.stringify({{ claimed, stored: readJob(cwd, job.id, env) }}));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["claimed"]["status"] == "claimed"
    assert payload["stored"]["idempotencyKey"] == ""


def test_background_wait_reaps_bootstrap_dead_worker(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_worker = tmp_path / "fake-node"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_worker.write_text("#!/usr/bin/env sh\nexit 1\n", encoding="utf8")
    fake_worker.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_WORKER_NODE"] = str(fake_worker)
    env["CLAUDE_FOR_CODEX_QUEUED_LOST_AFTER_MS"] = "100"
    waited = subprocess.run(
        [NODE, str(runtime), "review", "--background", "--wait", "--wait-timeout-ms", "3000", "bootstrap-exits"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert waited.returncode == 1
    payload = json.loads(waited.stdout)
    assert payload["status"] == "failed"
    assert payload["waitTimedOut"] is False
    assert payload["job"]["phase"] == "worker-launch-failed"


def test_jobs_reports_queued_stale_when_direct_worker_is_alive(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_QUEUED_LOST_AFTER_MS"] = "100"

    jobs = subprocess.run(
        [NODE, str(runtime), "jobs"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert jobs.returncode == 0, jobs.stderr
    state_dir = pathlib.Path(json.loads(jobs.stdout)["stateDir"])
    sleeper = subprocess.Popen(
        [
            "python3",
            "-c",
            "import time; time.sleep(30)",
            "claude-companion.mjs",
            "__run-job",
            "job-slow-claim",
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        (state_dir / "jobs" / "job-slow-claim.json").write_text(json.dumps({
            "id": "job-slow-claim",
            "status": "queued",
            "command": "review",
            "args": ["slow"],
            "cwd": str(repo),
            "workerPid": sleeper.pid,
            "submissionState": "starting",
            "createdAt": "2026-06-09T00:00:00.000Z",
            "updatedAt": "2026-06-09T00:00:00.000Z"
        }))

        listed = subprocess.run(
            [NODE, str(runtime), "jobs"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
        )

        assert listed.returncode == 0, listed.stderr
        job = next(item for item in json.loads(listed.stdout)["jobs"] if item["id"] == "job-slow-claim")
        assert job["status"] == "queued"
        assert job["lifecycle"]["state"] == "queued-stale"
        assert job["lifecycle"]["workerAlive"] is True
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sleeper.kill()


def test_lost_job_reaper_is_process_aware_and_terminal_safe(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    script = f"""
import {{ createJob, updateJob, finishJob, reapLostJobs, readJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const lost = createJob(cwd, {{ command: "review", args: ["lost"], cwd }}, env);
updateJob(cwd, lost.id, {{
  status: "running",
  workerPid: 999999,
  startedAt: "2026-06-09T00:00:00.000Z",
  lastHeartbeatAt: "2026-06-09T00:00:00.000Z"
}}, env);
const done = createJob(cwd, {{ command: "review", args: ["done"], cwd }}, env);
finishJob(cwd, done.id, {{ status: 0, stdout: "done" }}, env);
const updates = reapLostJobs(cwd, {{ now: Date.parse("2026-06-09T00:20:00.000Z") }}, env);
console.log(JSON.stringify({{ updates, lost: readJob(cwd, lost.id, env), done: readJob(cwd, done.id, env) }}));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["lost"]["status"] == "failed"
    assert payload["lost"]["phase"] == "lost"
    assert payload["done"]["status"] == "succeeded"


def test_lost_job_reaper_preserves_live_child_without_saved_identity(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    sleeper = subprocess.Popen(
        ["python3", "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        script = f"""
import {{ activeJobs, createJob, reapLostJobs, readJob, updateJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const job = createJob(cwd, {{ id: "unsafe-child", command: "review", args: ["unsafe"], cwd }}, env);
updateJob(cwd, job.id, {{
  status: "running",
  phase: "running",
  workerPid: 999999,
  childPid: {sleeper.pid},
  startedAt: "2026-06-09T00:00:00.000Z",
  lastHeartbeatAt: "2026-06-09T00:00:00.000Z"
}}, env);
const updates = reapLostJobs(cwd, {{ now: Date.parse("2026-06-09T00:20:00.000Z") }}, env);
console.log(JSON.stringify({{ updates, job: readJob(cwd, job.id, env), activeIds: activeJobs(cwd, env).map((item) => item.id) }}));
"""
        result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["job"]["status"] == "running"
        assert payload["job"]["phase"] == "unsafe-child-identity"
        assert payload["job"]["lifecycleState"] == "lost"
        assert payload["job"]["workerPid"] is None
        assert "manual inspection" in payload["job"]["lastProgressMessage"]
        assert "unsafe-child" in payload["activeIds"]

        env = os.environ.copy()
        env["CLAUDE_PLUGIN_DATA"] = str(data)
        listed = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True)
        assert listed.returncode == 0, listed.stderr
        listed_job = next(item for item in json.loads(listed.stdout)["jobs"] if item["id"] == "unsafe-child")
        assert listed_job["phase"] == "unsafe-child-identity"
        assert listed_job["lifecycle"]["state"] == "lost"
        assert any("manual inspection" in line for line in listed_job["progressPreview"])
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sleeper.kill()


def test_lost_job_reaper_preserves_leaderless_child_process_group(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    info_file = tmp_path / "leaderless.json"
    repo.mkdir()
    data.mkdir()
    leader = subprocess.Popen(
        [
            "python3",
            "-c",
            (
                "import json, os, pathlib, time\n"
                f"info = pathlib.Path({json.dumps(str(info_file))})\n"
                "leader_pid = os.getpid()\n"
                "pgid = os.getpgrp()\n"
                "child_pid = os.fork()\n"
                "if child_pid == 0:\n"
                "    info.write_text(json.dumps({'leaderPid': leader_pid, 'childPid': os.getpid(), 'pgid': os.getpgrp()}))\n"
                "    while True: time.sleep(1)\n"
                "time.sleep(0.1)\n"
            ),
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            if info_file.exists():
                break
            time.sleep(0.1)
        assert info_file.exists()
        info = json.loads(info_file.read_text(encoding="utf8"))
        leader_pid = int(info["leaderPid"])
        child_pid = int(info["childPid"])
        pgid = int(info["pgid"])
        assert pgid == leader_pid
        for _ in range(30):
            if leader.poll() is not None:
                break
            time.sleep(0.1)
        assert leader.poll() is not None
        assert process_is_running(child_pid)
        script = f"""
import {{ activeJobs, createJob, reapLostJobs, readJob, updateJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const job = createJob(cwd, {{ id: "leaderless", command: "review", args: ["leaderless"], cwd }}, env);
updateJob(cwd, job.id, {{
  status: "running",
  phase: "running",
  workerPid: 999999,
  childProcessGroupPid: {leader_pid},
  childProcessGroupIdentity: {{ pid: {leader_pid}, pgid: {leader_pid}, command: "exited leader", commandHash: "exited leader" }},
  startedAt: "2026-06-09T00:00:00.000Z",
  lastHeartbeatAt: "2026-06-09T00:00:00.000Z"
}}, env);
const updates = reapLostJobs(cwd, {{ now: Date.parse("2026-06-09T00:20:00.000Z") }}, env);
console.log(JSON.stringify({{ updates, job: readJob(cwd, job.id, env), activeIds: activeJobs(cwd, env).map((item) => item.id) }}));
"""
        result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["job"]["status"] == "running"
        assert payload["job"]["phase"] == "leaderless-orphaned"
        assert payload["job"]["lifecycleState"] == "lost"
        assert "leaderless" in payload["activeIds"]
    finally:
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if leader.poll() is None:
            leader.kill()


def test_lost_job_reaper_surfaces_inconclusive_process_group_probe(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    fake_ps = bin_dir / "ps"
    fake_ps.write_text(
        "#!/usr/bin/env sh\n"
        "if [ \"$1\" = \"-eo\" ]; then\n"
        "  echo 'synthetic ps group-scan failure' >&2\n"
        "  exit 2\n"
        "fi\n"
        "exit 1\n",
        encoding="utf8",
    )
    fake_ps.chmod(0o755)
    script = f"""
import {{ activeJobs, createJob, reapLostJobs, readJob, updateJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const job = createJob(cwd, {{ id: "inconclusive-ps", command: "review", args: ["inconclusive"], cwd }}, env);
updateJob(cwd, job.id, {{
  status: "running",
  phase: "running",
  workerPid: 999999,
  childProcessGroupPid: 424242,
  childProcessGroupIdentity: {{ pid: 424242, pgid: 424242, command: "missing leader", commandHash: "missing leader" }},
  startedAt: "2026-06-09T00:00:00.000Z",
  lastHeartbeatAt: "2026-06-09T00:00:00.000Z"
}}, env);
const updates = reapLostJobs(cwd, {{ now: Date.parse("2026-06-09T00:20:00.000Z") }}, env);
console.log(JSON.stringify({{ updates, job: readJob(cwd, job.id, env), activeIds: activeJobs(cwd, env).map((item) => item.id) }}));
"""
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["job"]["status"] == "running"
    assert payload["job"]["phase"] == "leaderless-liveness-inconclusive"
    assert payload["job"]["lifecycleState"] == "lost"
    assert "process-group liveness probing is inconclusive" in payload["job"]["lastProgressMessage"]
    assert "inconclusive-ps" in payload["activeIds"]


def test_finish_job_records_output_truncation_metadata(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    lifecycle = PLUGIN / "scripts" / "lib" / "job-lifecycle.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    script = f"""
import {{ createJob, finishJob, readJob }} from {json.dumps(jobs.as_uri())};
import {{ MAX_STORED_OUTPUT_BYTES }} from {json.dumps(lifecycle.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const job = createJob(cwd, {{ command: "review", args: ["long-output"], cwd }}, env);
finishJob(cwd, job.id, {{
  status: 0,
  stdout: "x".repeat(MAX_STORED_OUTPUT_BYTES + 32),
  stderr: "y".repeat(MAX_STORED_OUTPUT_BYTES + 64)
}}, env);
console.log(JSON.stringify(readJob(cwd, job.id, env)));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["stdoutTruncated"] is True
    assert payload["stderrTruncated"] is True
    assert payload["stdoutBytes"] > payload["stdoutStoredBytes"]
    assert payload["stderrBytes"] > payload["stderrStoredBytes"]
    assert payload["stdout"].endswith("...<truncated>")
    assert payload["stderr"].endswith("...<truncated>")


def test_sdk_progress_machine_line_uses_real_event_type_fields():
    backend = PLUGIN / "scripts" / "lib" / "claude-backend.mjs"
    text = backend.read_text(encoding="utf8")
    assert "function maybeWriteSdkProgress(event, options)" in text
    assert "formatProgressEvent" in text
    assert "event.phase" not in text
    assert "sdk-${eventType}" in text or "sdk-\" + eventType" in text


def test_review_gate_never_starts_background_or_ultrareview():
    companion = (PLUGIN / "scripts" / "claude-companion.mjs").read_text(encoding="utf8")
    gate = (PLUGIN / "hooks" / "claude-review-gate.mjs").read_text(encoding="utf8")
    skill = (PLUGIN / "skills" / "claude-review-gate" / "SKILL.md").read_text(encoding="utf8")
    readme = (PLUGIN / "README.md").read_text(encoding="utf8")
    assert "runReviewGate" in companion
    assert "--background" not in gate
    assert "startBackgroundJob(" not in gate
    assert "reserveJob(" not in gate
    assert "ultrareview" not in gate.lower()
    assert "roleTimeoutMs" in companion or "REVIEW_GATE_TIMEOUT" in companion
    assert "warnGate" in companion and "allowing stop" in companion
    assert "review gate never starts background jobs" in skill.lower()
    assert "Stop hook never starts background jobs" in readme


def test_lifecycle_docs_preserve_no_resubmit_and_cancel_boundaries():
    readme = (PLUGIN / "README.md").read_text(encoding="utf8")
    result = (PLUGIN / "skills" / "claude-result" / "SKILL.md").read_text(encoding="utf8")
    cancel = (PLUGIN / "skills" / "claude-cancel" / "SKILL.md").read_text(encoding="utf8")
    assert "Do not rerun the same review just because `--wait` expired" in readme
    assert "do not start a replacement review" in result
    assert "stdoutTruncated" in readme and "stderrTruncated" in result
    assert "Do not claim a running process was stopped unless the runtime reports `cancelled`" in cancel


def test_cancel_escalates_to_sigkill_for_sigterm_ignoring_child(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    pid_file = tmp_path / "fake-claude.pid"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        f"#!/usr/bin/env python3\nimport os, pathlib, signal, sys, time\npath = pathlib.Path({json.dumps(str(pid_file))})\npath.write_text(str(os.getpid()))\nsignal.signal(signal.SIGTERM, lambda signum, frame: None)\nwhile True:\n    time.sleep(1)\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["CLAUDE_FOR_CODEX_KILL_GRACE_MS"] = "100"
    started = subprocess.run([NODE, str(runtime), "review", "--background", "cancel-me"], cwd=repo, env=env, capture_output=True, text=True)
    assert started.returncode == 0, started.stderr
    job_id = json.loads(started.stdout)["job"]["id"]
    for _ in range(100):
        if pid_file.exists():
            break
        time.sleep(0.1)
    assert pid_file.exists(), "fake Claude process did not start within 10 seconds"
    fake_pid = int(pid_file.read_text(encoding="utf8"))
    cancelled = subprocess.run([NODE, str(runtime), "cancel", job_id], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
    assert cancelled.returncode == 0, cancelled.stderr
    for _ in range(30):
        if not process_is_running(fake_pid):
            break
        time.sleep(0.1)
    else:
        pytest.fail("fake Claude process survived cancel SIGKILL escalation")
    payload = json.loads(cancelled.stdout)
    assert payload["status"] == "cancelled"


def test_cancel_orphaned_job_uses_validated_child_process_group(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    process_lib = PLUGIN / "scripts" / "lib" / "process.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    pid_file = tmp_path / "orphan.pid"
    child_script = tmp_path / "orphan_child.py"
    repo.mkdir()
    data.mkdir()
    child_script.write_text(
        f"import os, pathlib, signal, time\n"
        f"pathlib.Path({json.dumps(str(pid_file))}).write_text(str(os.getpid()))\n"
        f"signal.signal(signal.SIGTERM, lambda signum, frame: None)\n"
        f"while True: time.sleep(1)\n",
        encoding="utf8",
    )
    child = subprocess.Popen(
        ["python3", str(child_script)],
        start_new_session=True,
    )
    try:
        for _ in range(30):
            if pid_file.exists():
                break
            time.sleep(0.1)
        child_pid = int(pid_file.read_text(encoding="utf8"))
        script = f"""
import {{ createJob, updateJob }} from {json.dumps(jobs.as_uri())};
import {{ captureProcessIdentity }} from {json.dumps(process_lib.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const job = createJob(cwd, {{ command: "review", args: ["orphan"], cwd }}, env);
const childIdentity = captureProcessIdentity({child_pid});
if (!childIdentity) throw new Error("child identity missing");
childIdentity.command = "<redacted-path>orphan_child.py";
delete childIdentity.commandHash;
updateJob(cwd, job.id, {{
  status: "running",
  phase: "orphaned",
  workerPid: 999999,
  childPid: {child_pid},
  childProcessGroupPid: {child_pid},
  childProcessGroupIdentity: childIdentity,
  startedAt: new Date().toISOString(),
  lastHeartbeatAt: "2026-06-09T00:00:00.000Z"
}}, env);
console.log(job.id);
"""
        job_id = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True, check=True).stdout.strip()
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_DATA"] = str(data)
        env["CLAUDE_FOR_CODEX_KILL_GRACE_MS"] = "100"
        cancelled = subprocess.run([NODE, str(runtime), "cancel", job_id], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
        assert cancelled.returncode == 0, cancelled.stderr
        payload = json.loads(cancelled.stdout)
        assert payload["status"] == "cancelled"
        assert payload["job"]["status"] == "cancelled"
        for _ in range(30):
            if child.poll() is not None:
                break
            time.sleep(0.1)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()


def test_cancel_preserves_terminal_result_written_during_signal(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    process_lib = PLUGIN / "scripts" / "lib" / "process.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    pid_file = tmp_path / "race.pid"
    child_script = tmp_path / "cancel_race_child.py"
    repo.mkdir()
    data.mkdir()
    child_script.write_text(
        "import json, os, pathlib, signal, sys, time\n"
        f"pathlib.Path({json.dumps(str(pid_file))}).write_text(str(os.getpid()))\n"
        "job_file = pathlib.Path(os.environ['JOB_FILE'])\n"
        "def handler(signum, frame):\n"
        "    job = json.loads(job_file.read_text())\n"
        "    job.update({'status': 'succeeded', 'phase': 'succeeded', 'finishedAt': '2026-06-09T00:01:00.000Z', 'exitStatus': 0})\n"
        "    job_file.write_text(json.dumps(job))\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, handler)\n"
        "while True: time.sleep(1)\n",
        encoding="utf8",
    )
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    listed = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True, check=True)
    jobs_dir = pathlib.Path(json.loads(listed.stdout)["stateDir"]) / "jobs"
    job_file = jobs_dir / "cancel-race.json"
    child = subprocess.Popen(
        ["python3", str(child_script)],
        env={**os.environ, "JOB_FILE": str(job_file)},
        start_new_session=True,
    )
    try:
        for _ in range(30):
            if pid_file.exists():
                break
            time.sleep(0.1)
        child_pid = int(pid_file.read_text(encoding="utf8"))
        script = f"""
import {{ createJob, updateJob }} from {json.dumps(jobs.as_uri())};
import {{ captureProcessIdentity }} from {json.dumps(process_lib.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
const childIdentity = captureProcessIdentity({child_pid});
if (!childIdentity) throw new Error("child identity missing");
childIdentity.command = "<redacted-path>cancel_race_child.py";
delete childIdentity.commandHash;
createJob(cwd, {{ id: "cancel-race", command: "review", args: ["race"], cwd }}, env);
updateJob(cwd, "cancel-race", {{
  status: "running",
  phase: "running",
  childPid: {child_pid},
  childProcessGroupPid: {child_pid},
  childProcessGroupIdentity: childIdentity,
  startedAt: "2026-06-09T00:00:00.000Z",
  lastHeartbeatAt: "2026-06-09T00:00:00.000Z"
}}, env);
"""
        created = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
        assert created.returncode == 0, created.stderr
        cancel = subprocess.run([NODE, str(runtime), "cancel", "cancel-race"], cwd=repo, env=env, capture_output=True, text=True, timeout=10)
        assert cancel.returncode == 1, cancel.stdout
        payload = json.loads(cancel.stdout)
        assert payload["status"] == "succeeded"
        assert "terminal state" in payload["reason"]
        assert json.loads(job_file.read_text())["status"] == "succeeded"
    finally:
        if child.poll() is None:
            child.kill()


def test_cancel_validation_failure_preserves_running_job_status(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    script = f"""
import {{ createJob, updateJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
createJob(cwd, {{ id: "cancel-validation", command: "review", args: ["unsafe"], cwd }}, env);
updateJob(cwd, "cancel-validation", {{
  status: "running",
  phase: "running",
  workerPid: {os.getpid()},
  startedAt: "2026-06-09T00:00:00.000Z",
  lastHeartbeatAt: "2026-06-09T00:00:00.000Z"
}}, env);
"""
    created = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert created.returncode == 0, created.stderr
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    cancelled = subprocess.run([NODE, str(runtime), "cancel", "cancel-validation"], cwd=repo, env=env, capture_output=True, text=True)
    assert cancelled.returncode == 1
    payload = json.loads(cancelled.stdout)
    assert payload["status"] == "cancel_failed"
    assert "requires process identity validation" in payload["reason"]
    assert payload["job"]["status"] == "running"
    assert payload["job"]["phase"] == "cancel_failed"
    finished_script = f"""
import {{ finishJob, readJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
finishJob(cwd, "cancel-validation", {{ status: 0, stdout: "done", stderr: "" }}, env);
console.log(JSON.stringify(readJob(cwd, "cancel-validation", env)));
"""
    finished = subprocess.run([NODE, "--input-type=module", "--eval", finished_script], capture_output=True, text=True)
    assert finished.returncode == 0, finished.stderr
    finished_payload = json.loads(finished.stdout)
    assert finished_payload["status"] == "succeeded"
    assert finished_payload["phase"] == "succeeded"


def test_finish_job_after_cancel_request_is_recorded_as_cancelled(tmp_path):
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    script = f"""
import {{ createJob, finishJob, readJob, updateJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
createJob(cwd, {{ id: "finish-after-cancel", command: "review", args: ["cancelled"], cwd }}, env);
updateJob(cwd, "finish-after-cancel", {{
  status: "running",
  phase: "cancelling",
  cancelRequestedAt: "2026-06-09T00:00:01.000Z",
  startedAt: "2026-06-09T00:00:00.000Z"
}}, env);
finishJob(cwd, "finish-after-cancel", {{ status: 1, stdout: "", stderr: "interrupted" }}, env);
console.log(JSON.stringify(readJob(cwd, "finish-after-cancel", env)));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "cancelled"
    assert payload["phase"] == "cancelled"


def test_process_group_cancel_fails_when_group_survives_sigkill(tmp_path):
    process_lib = PLUGIN / "scripts" / "lib" / "process.mjs"
    pid_file = tmp_path / "group.pid"
    child = subprocess.Popen(
        ["python3", "-c", f"import os, pathlib, time\npathlib.Path({json.dumps(str(pid_file))}).write_text(str(os.getpid()))\nwhile True: time.sleep(1)\n"],
        start_new_session=True,
    )
    try:
        for _ in range(30):
            if pid_file.exists():
                break
            time.sleep(0.1)
        child_pid = int(pid_file.read_text(encoding="utf8"))
        script = f"""
import {{ captureProcessIdentity, terminateValidatedProcessGroup }} from {json.dumps(process_lib.as_uri())};
const pid = {child_pid};
const identity = captureProcessIdentity(pid);
const originalKill = process.kill;
process.kill = () => true;
try {{
  const result = terminateValidatedProcessGroup(pid, identity, {{ killGraceMs: 10 }});
  console.log(JSON.stringify(result));
}} finally {{
  process.kill = originalKill;
}}
"""
        result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["delivered"] is True
        assert "still alive after SIGKILL" in payload["reason"]
    finally:
        if child.poll() is None:
            child.kill()


def test_process_group_cancel_reports_inconclusive_after_sigkill(tmp_path):
    process_lib = PLUGIN / "scripts" / "lib" / "process.mjs"
    fake_bin = tmp_path / "bin"
    state_file = tmp_path / "ps-state.txt"
    fake_bin.mkdir()
    state_file.write_text("initial", encoding="utf8")
    fake_ps = fake_bin / "ps"
    fake_ps.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "state = pathlib.Path(os.environ['FAKE_PS_STATE']).read_text().strip()\n"
        "if '-p' in sys.argv:\n"
        "    if state == 'initial':\n"
        "        print('123456 1 123456 S fake-command')\n"
        "        raise SystemExit(0)\n"
        "    raise SystemExit(1)\n"
        "if '-eo' in sys.argv:\n"
        "    if state == 'sigterm':\n"
        "        print('999 123456 S')\n"
        "        raise SystemExit(0)\n"
        "    if state == 'sigkill':\n"
        "        print('synthetic post-sigkill group scan failure', file=sys.stderr)\n"
        "        raise SystemExit(2)\n"
        "raise SystemExit(1)\n",
        encoding="utf8",
    )
    fake_ps.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAKE_PS_STATE"] = str(state_file)
    script = f"""
import fs from "node:fs";
import {{ captureProcessIdentity, psProbeDiagnostics, resetPsProbeDiagnostics, terminateValidatedProcessGroup }} from {json.dumps(process_lib.as_uri())};
const stateFile = {json.dumps(str(state_file))};
resetPsProbeDiagnostics();
const identity = captureProcessIdentity(123456);
const originalKill = process.kill;
process.kill = (_pid, signal) => {{
  if (signal === "SIGTERM") {{
    fs.writeFileSync(stateFile, "sigterm");
  }}
  if (signal === "SIGKILL") {{
    fs.writeFileSync(stateFile, "sigkill");
  }}
  return true;
}};
try {{
  const result = terminateValidatedProcessGroup(123456, identity, {{ killGraceMs: 10 }});
  console.log(JSON.stringify({{ result, diagnostics: psProbeDiagnostics() }}));
}} finally {{
  process.kill = originalKill;
}}
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["result"]["ok"] is False
    assert payload["result"]["delivered"] is True
    assert payload["result"]["escalated"] is True
    assert "liveness probe inconclusive after SIGKILL" in payload["result"]["reason"]
    assert payload["diagnostics"]["groupScanFailures"] >= 1
    assert payload["diagnostics"]["lastGroupScanFailure"]["reason"].startswith("ps probe")


def test_process_group_scan_is_bounded_and_fail_closed_on_ps_timeout(tmp_path):
    process_lib = PLUGIN / "scripts" / "lib" / "process.mjs"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ps = fake_bin / "ps"
    fake_ps.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "time.sleep(1)\n",
        encoding="utf8",
    )
    fake_ps.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["CLAUDE_FOR_CODEX_PS_TIMEOUT_MS"] = "25"
    script = f"""
	import {{ captureProcessIdentity, processGroupHasLiveMembers, psProbeDiagnostics, resetPsProbeDiagnostics }} from {json.dumps(process_lib.as_uri())};
	resetPsProbeDiagnostics();
	const start = Date.now();
	console.log(JSON.stringify({{
	  identity: captureProcessIdentity(123456),
	  groupLive: processGroupHasLiveMembers(123456),
	  elapsedMs: Date.now() - start,
	  diagnostics: psProbeDiagnostics()
	}}));
	"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["identity"] is None
    assert payload["groupLive"] is True
    assert payload["elapsedMs"] < 1000
    assert payload["diagnostics"]["groupScanFailures"] == 1
    assert payload["diagnostics"]["lastGroupScanFailure"]["timeoutMs"] == 25
    assert payload["diagnostics"]["lastGroupScanFailure"]["reason"] == "ps probe failed"


def test_process_group_scan_fail_closed_on_ps_max_buffer(tmp_path):
    process_lib = PLUGIN / "scripts" / "lib" / "process.mjs"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ps = fake_bin / "ps"
    fake_ps.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '-eo' in sys.argv:\n"
        "    print('1 1 S')\n"
        "    print('x' * 20000)\n"
        "else:\n"
        "    print('123456 1 123456 S fake-command')\n",
        encoding="utf8",
    )
    fake_ps.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["CLAUDE_FOR_CODEX_PS_MAX_BUFFER_BYTES"] = "1024"
    script = f"""
	import {{ captureProcessIdentity, processGroupHasLiveMembers, psProbeDiagnostics, resetPsProbeDiagnostics }} from {json.dumps(process_lib.as_uri())};
	resetPsProbeDiagnostics();
	console.log(JSON.stringify({{
	  identity: captureProcessIdentity(123456),
	  groupLive: processGroupHasLiveMembers(123456),
	  diagnostics: psProbeDiagnostics()
	}}));
	"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["identity"]["pid"] == 123456
    assert payload["groupLive"] is True
    assert payload["diagnostics"]["groupScanFailures"] == 1
    assert payload["diagnostics"]["lastGroupScanFailure"]["maxBufferBytes"] == 1024
    assert payload["diagnostics"]["lastGroupScanFailure"]["reason"] == "ps probe failed"


def test_process_group_cancel_refuses_unvalidated_leaderless_group(tmp_path):
    process_lib = PLUGIN / "scripts" / "lib" / "process.mjs"
    info_file = tmp_path / "leaderless-cancel.json"
    leader = subprocess.Popen(
        [
            "python3",
            "-c",
            (
                "import json, os, pathlib, time\n"
                f"info = pathlib.Path({json.dumps(str(info_file))})\n"
                "leader_pid = os.getpid()\n"
                "child_pid = os.fork()\n"
                "if child_pid == 0:\n"
                "    info.write_text(json.dumps({'leaderPid': leader_pid, 'childPid': os.getpid(), 'pgid': os.getpgrp()}))\n"
                "    while True: time.sleep(1)\n"
                "time.sleep(0.1)\n"
            ),
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            if info_file.exists():
                break
            time.sleep(0.1)
        assert info_file.exists()
        info = json.loads(info_file.read_text(encoding="utf8"))
        leader_pid = int(info["leaderPid"])
        child_pid = int(info["childPid"])
        assert int(info["pgid"]) == leader_pid
        for _ in range(30):
            if leader.poll() is not None:
                break
            time.sleep(0.1)
        assert leader.poll() is not None
        assert process_is_running(child_pid)

        script = f"""
import {{ terminateValidatedProcessGroup }} from {json.dumps(process_lib.as_uri())};
const result = terminateValidatedProcessGroup({leader_pid}, {{
  pid: {leader_pid},
  pgid: {leader_pid},
  command: "exited leader",
  commandHash: "exited leader"
}}, {{ killGraceMs: 10 }});
console.log(JSON.stringify(result));
"""
        result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["delivered"] is False
        assert "leaderless process group" in payload["reason"]
        assert process_is_running(child_pid)
    finally:
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if leader.poll() is None:
            leader.kill()


def test_cancel_refuses_worker_signal_when_child_group_validation_fails(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    info_file = tmp_path / "leaderless-worker-cancel.json"
    worker_script = tmp_path / "claude-companion.mjs"
    repo.mkdir()
    data.mkdir()
    worker_script.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "while True:\n"
        "    time.sleep(1)\n",
        encoding="utf8",
    )
    worker_script.chmod(0o755)
    leader = subprocess.Popen(
        [
            "python3",
            "-c",
            (
                "import json, os, pathlib, time\n"
                f"info = pathlib.Path({json.dumps(str(info_file))})\n"
                "leader_pid = os.getpid()\n"
                "child_pid = os.fork()\n"
                "if child_pid == 0:\n"
                "    info.write_text(json.dumps({'leaderPid': leader_pid, 'childPid': os.getpid(), 'pgid': os.getpgrp()}))\n"
                "    while True: time.sleep(1)\n"
                "time.sleep(0.1)\n"
            ),
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    worker = subprocess.Popen(
        ["python3", str(worker_script), "__run-job", "job-leaderless-worker"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            if info_file.exists():
                break
            time.sleep(0.1)
        assert info_file.exists()
        info = json.loads(info_file.read_text(encoding="utf8"))
        leader_pid = int(info["leaderPid"])
        child_pid = int(info["childPid"])
        assert int(info["pgid"]) == leader_pid
        for _ in range(30):
            if leader.poll() is not None:
                break
            time.sleep(0.1)
        assert leader.poll() is not None
        assert process_is_running(child_pid)
        assert process_is_running(worker.pid)

        script = f"""
import {{ createJob, updateJob }} from {json.dumps(jobs.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
createJob(cwd, {{ id: "job-leaderless-worker", command: "review", args: ["leaderless"], cwd }}, env);
updateJob(cwd, "job-leaderless-worker", {{
  status: "running",
  phase: "running",
  workerPid: {worker.pid},
  childProcessGroupPid: {leader_pid},
  childProcessGroupIdentity: {{ pid: {leader_pid}, pgid: {leader_pid}, command: "exited leader", commandHash: "exited leader" }},
  startedAt: "2026-06-09T00:00:00.000Z",
  lastHeartbeatAt: "2026-06-09T00:00:00.000Z"
}}, env);
"""
        created = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
        assert created.returncode == 0, created.stderr
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_DATA"] = str(data)
        cancelled = subprocess.run([NODE, str(runtime), "cancel", "job-leaderless-worker"], cwd=repo, env=env, capture_output=True, text=True, timeout=5)
        assert cancelled.returncode == 1
        payload = json.loads(cancelled.stdout)
        assert payload["status"] == "cancel_failed"
        assert "before signaling the worker" in payload["reason"]
        assert process_is_running(worker.pid)
        assert process_is_running(child_pid)
    finally:
        for pgid in [worker.pid, leader.pid]:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if worker.poll() is None:
            worker.kill()
        if leader.poll() is None:
            leader.kill()


def test_process_identity_hash_takes_precedence_over_redacted_fallback(tmp_path):
    process_lib = PLUGIN / "scripts" / "lib" / "process.mjs"
    pid_file = tmp_path / "identity.pid"
    child = subprocess.Popen(
        ["python3", "-c", f"import os, pathlib, time\npathlib.Path({json.dumps(str(pid_file))}).write_text(str(os.getpid()))\nwhile True: time.sleep(1)\n"],
        start_new_session=True,
    )
    try:
        for _ in range(30):
            if pid_file.exists():
                break
            time.sleep(0.1)
        child_pid = int(pid_file.read_text(encoding="utf8"))
        script = f"""
import {{ captureProcessIdentity, validateProcessGroupLeader }} from {json.dumps(process_lib.as_uri())};
const identity = captureProcessIdentity({child_pid});
const suffix = identity.command.slice(-20);
const redactedWouldMatch = {{
  ...identity,
  command: `<redacted-home>${{suffix}}`
}};
const mismatchedHash = {{
  ...redactedWouldMatch,
  commandHash: "0".repeat(64)
}};
console.log(JSON.stringify({{
  fallback: validateProcessGroupLeader({child_pid}, redactedWouldMatch),
  strictHash: validateProcessGroupLeader({child_pid}, mismatchedHash)
}}));
"""
        result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["fallback"]["ok"] is True
        assert payload["strictHash"]["ok"] is False
        assert "process identity changed" in payload["strictHash"]["reason"]
    finally:
        if child.poll() is None:
            child.kill()


def test_release_check_knows_long_running_lifecycle_guards():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    result = subprocess.run([NODE, str(runtime), "release-check", "--ci-simulate"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    for name in [
        "job-lifecycle-helper",
        "atomic-job-claim-lock",
        "async-background-worker",
        "background-output-capped-in-memory",
        "short-wait-window",
        "wait-window-ceiling",
        "wait-cancelled-nonzero",
        "wait-timeout-stripped",
        "job-idempotency-reuse",
        "reserve-job-cap-idempotency",
        "job-idempotency-fingerprint-controls",
        "job-idempotency-top-model-controls",
        "job-idempotency-timeout-no-reuse",
        "review-gate-baseline-shared-fingerprint",
        "review-gate-fingerprint-timeout-fail-open",
        "review-gate-reviewable-git-timeout",
        "job-result-sanitized",
        "job-output-truncation-metadata",
        "job-output-worker-byte-metadata",
        "result-lock-contention-non-ok",
        "job-write-returns-sanitized",
        "queued-worker-bootstrap-reaper",
        "queued-reservation-expiry",
        "wait-reaps-lost-jobs",
        "progress-event-parser",
        "stderr-line-buffering",
        "sdk-progress-hook-point",
        "signal-child-group-cleanup",
        "worker-signal-handler-before-child-spawn",
        "child-process-identity-required",
        "unvalidated-child-no-negative-pgid",
        "process-identity-no-prefix-match",
        "cancel-child-worker-deferred",
        "cancel-queued-lock-reread",
        "cancel-queued-worker-terminated",
        "cancel-requires-delivered-signal",
        "cancel-persistence-required",
        "cancel-preserves-terminal-race",
        "cancel-request-finish-semantics",
        "leaderless-child-group-cleanup",
        "process-ps-probe-bounds",
        "hard-timeout-sigkill",
        "hard-timeout-nonzero-status",
        "cancel-sigkill-escalation",
        "cancel-final-liveness-check",
        "owner-aware-file-locks",
        "zombie-process-not-alive",
        "process-aware-reaper",
        "execution-mode-recommendation",
        "git-timeout-not-nonrepo",
        "background-concurrency-cap",
        "background-posix-platform-guard",
        "review-gate-no-background",
        "review-gate-no-reserve-job",
        "review-gate-bounded-fail-open",
    ]:
        assert checks[name]["ok"] is True


def run_fake_claude_plan(tmp_path, args, extra_env=None):
    return run_fake_claude_review(tmp_path, args, extra_env=extra_env, command="plan")


def run_fake_claude_rescue(tmp_path, args, extra_env=None):
    return run_fake_claude_review(tmp_path, args, extra_env=extra_env, command="rescue")


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


def write_fake_claude_sdk(tmp_path, *, stdout="SDK_OK", structured_output=None, extra_js=""):
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
  const resultEvent = {{
    type: 'result',
    subtype: 'success',
    result: {json.dumps(stdout)},
    session_id: 'sdk-session-secret',
    total_cost_usd: 0.01,
    usage: {{ input_tokens: 3, output_tokens: 4 }}
  }};
  const structuredOutput = {json.dumps(structured_output)};
  if (structuredOutput !== null) {{
    resultEvent.structured_output = structuredOutput;
  }}
  yield resultEvent;
}}
""",
        encoding="utf8",
    )
    return sdk_dir / "index.mjs", capture


def write_fake_claude_cli(tmp_path, *, help_text="--model <model> alias opus sonnet --effort <level>"):
    fake_cli_dir = tmp_path / "fake-claude-cli"
    fake_cli_dir.mkdir(exist_ok=True)
    fake_cli = fake_cli_dir / "claude"
    fake_cli.write_text(
        f"""#!/usr/bin/env python3
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
if sys.argv[1:] == ["--help"]:
    print({json.dumps(help_text)})
    raise SystemExit(0)
print("FAKE_CLAUDE_CLI_UNEXPECTED", file=sys.stderr)
raise SystemExit(17)
""",
        encoding="utf8",
    )
    fake_cli.chmod(0o755)
    return fake_cli


def structured_review_payload(summary="structured sdk review ok", verdict="approve", findings=None):
    return {
        "verdict": verdict,
        "summary": summary,
        "findings": [] if findings is None else findings,
        "next_steps": [],
    }


def structured_review_finding_payload():
    return {
        "severity": "high",
        "title": "Regression title",
        "body": "Regression body",
        "file": "calc.py",
        "line_start": 2,
        "line_end": 2,
        "confidence": 0.95,
        "recommendation": "Restore the expected behavior.",
    }


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
import time

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
if sys.argv[1:] == ["--help"]:
    marker = os.environ.get("HELP_PROBE_MARKER")
    if marker:
        pathlib.Path(marker).write_text("help-probed")
    if os.environ.get("FAIL_ON_HELP") == "1":
        print("unexpected help probe", file=sys.stderr)
        raise SystemExit(23)
    print(os.environ.get("FAKE_CLAUDE_HELP", "--model <model> alias opus sonnet --effort <level> --fallback-model <model> accepts a comma-separated list"))
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


def run_fake_claude_multi_review(
    tmp_path,
    args,
    commit_head=False,
    fail_roles=None,
    extra_env=None,
    extra_help=None,
    branch_file_count=0,
    branch_lines_per_file=0,
):
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
        for index in range(branch_file_count):
            lines = "\n".join(f"branch {index} line {line}" for line in range(branch_lines_per_file))
            (repo / f"branch-{index}.txt").write_text(f"base\n{lines}\n")
        subprocess.run(["git", "add", "branch.txt"], cwd=repo, check=True, capture_output=True, text=True)
        if branch_file_count:
            subprocess.run(
                ["git", "add", *[f"branch-{index}.txt" for index in range(branch_file_count)]],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
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
import time

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
if sys.argv[1:] == ["--help"]:
    marker = os.environ.get("HELP_PROBE_MARKER")
    if marker:
        pathlib.Path(marker).write_text("help-probed")
    if os.environ.get("FAIL_ON_HELP") == "1":
        print("unexpected help probe", file=sys.stderr)
        raise SystemExit(23)
    print(os.environ.get("FAKE_CLAUDE_HELP", "--model <model> alias opus sonnet --effort <level> --fallback-model <model> accepts a comma-separated list"))
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
    if extra_help is not None:
        env["FAKE_CLAUDE_HELP"] = extra_help
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
    "command_args",
    [
        ["review", "--backend", "sdk", "--fallback-model", "claude-sonnet-4-5"],
        ["adversarial-review", "--backend", "sdk", "--fallback-model", "claude-sonnet-4-5"],
        ["plan", "--backend", "sdk", "--fallback-model", "claude-sonnet-4-5", "make a plan"],
        ["rescue", "--backend", "sdk", "--fallback-model", "claude-sonnet-4-5"],
    ],
)
def test_prompt_commands_sdk_backend_reject_fallback_model(tmp_path, command_args):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"

    result = subprocess.run(
        [NODE, str(runtime), *command_args],
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


def test_multi_review_help_does_not_invoke_claude(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    fake_bin = tmp_path / "bin"
    marker = tmp_path / "claude-invoked.txt"
    fake_bin.mkdir()
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        f"""#!/usr/bin/env python3
import pathlib
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
pathlib.Path({json.dumps(str(marker))}).write_text("invoked\\n", encoding="utf8")
raise SystemExit(99)
""",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "multi-review", "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Usage: claude-companion.mjs multi-review" in result.stdout
    assert "--agent-team plugin|sdk-subagents" in result.stdout
    assert not marker.exists()


def test_unknown_review_option_fails_before_claude(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    fake_bin = tmp_path / "bin"
    marker = tmp_path / "claude-invoked.txt"
    fake_bin.mkdir()
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        f"""#!/usr/bin/env python3
import pathlib
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
pathlib.Path({json.dumps(str(marker))}).write_text("invoked\\n", encoding="utf8")
raise SystemExit(99)
""",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "review", "--definitely-not-a-real-option"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Unsupported option --definitely-not-a-real-option" in result.stderr
    assert not marker.exists()


def test_unknown_short_review_option_fails_before_claude(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    fake_bin = tmp_path / "bin"
    marker = tmp_path / "claude-invoked.txt"
    fake_bin.mkdir()
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        f"""#!/usr/bin/env python3
import pathlib
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
pathlib.Path({json.dumps(str(marker))}).write_text("invoked\\n", encoding="utf8")
raise SystemExit(99)
""",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "review", "-not-real"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Unsupported option -not-real" in result.stderr
    assert not marker.exists()


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


def test_ultrareview_runs_only_with_cost_consent(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
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
sys.stderr.write("session: https://claude.example/session\\n")
print('{"verdict":"ok"}')
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "ultrareview",
            "--confirm-cost",
            "--json",
            "--timeout",
            "60",
            "origin/main",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == '{"verdict":"ok"}\n'
    assert "session: https://claude.example/session" in result.stderr
    assert json.loads((capture_dir / "argv.json").read_text()) == [
        "ultrareview",
        "--json",
        "--timeout",
        "60",
        "origin/main",
    ]


def test_ultrareview_skill_style_invocation_splits_argument_string(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
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
print('{"verdict":"ok"}')
"""
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW"] = "1"
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "ultrareview",
            "--json origin/main",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == '{"verdict":"ok"}\n'
    assert json.loads((capture_dir / "argv.json").read_text()) == [
        "ultrareview",
        "--json",
        "origin/main",
    ]


def test_ultrareview_rejects_unsupported_option(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"

    result = subprocess.run(
        [NODE, str(runtime), "ultrareview", "--confirm-cost", "--model", "opus"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Unsupported ultrareview option: --model" in result.stderr


def test_ultrareview_rejects_invalid_timeout(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"

    result = subprocess.run(
        [NODE, str(runtime), "ultrareview", "--confirm-cost", "--timeout", "soon"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Missing or invalid --timeout minutes." in result.stderr


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


def prepare_gate_repo(tmp_path, *, with_change=True, extra_help=None):
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
import signal
import sys
import time

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
if sys.argv[1:] == ["--help"]:
    marker = os.environ.get("HELP_PROBE_MARKER")
    if marker:
        pathlib.Path(marker).write_text("help-probed")
    if os.environ.get("FAIL_ON_HELP") == "1":
        print("unexpected help probe", file=sys.stderr)
        raise SystemExit(23)
    print(os.environ.get("FAKE_CLAUDE_HELP", "--print --permission-mode --tools --allowedTools --disallowedTools --mcp-config --strict-mcp-config --model <model> alias opus sonnet --effort <level> --fallback-model <model> accepts a comma-separated list --output-format"))
    raise SystemExit(0)

capture = pathlib.Path(os.environ["CAPTURE_DIR"])
call_index = len(list(capture.glob("argv-*.json")))
prompt = sys.argv[-1]
(capture / f"argv-{call_index}.json").write_text(json.dumps(sys.argv[1:]))
(capture / f"prompt-{call_index}.txt").write_text(prompt)
if os.environ.get("IGNORE_TERM") == "1":
    signal.signal(signal.SIGTERM, lambda signum, frame: None)
sleep_ms = int(os.environ.get("SLEEP_MS", "0") or "0")
if sleep_ms > 0:
    time.sleep(sleep_ms / 1000)
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
    if extra_help is not None:
        env["FAKE_CLAUDE_HELP"] = extra_help
    return runtime, repo, capture_dir, env


def run_fake_review_gate(
    tmp_path,
    *,
    enable=True,
    with_change=True,
    hook_input=None,
    extra_env=None,
    extra_help=None,
    setup_extra_env=None,
    gate_extra_env=None,
    args=None,
):
    runtime, repo, capture_dir, env = prepare_gate_repo(tmp_path, with_change=with_change, extra_help=extra_help)
    setup_env = env.copy()
    gate_env = env.copy()
    # Keep extra_env gate-only so setup's capability report cannot pollute hook probe sentinels.
    if setup_extra_env:
        setup_env.update(setup_extra_env)
    if extra_env:
        gate_env.update(extra_env)
    if gate_extra_env:
        gate_env.update(gate_extra_env)
    if enable:
        setup = subprocess.run(
            ["node", str(runtime), "setup", "--enable-review-gate", "--review-gate-mode", "multi-role"],
            cwd=repo,
            env=setup_env,
            capture_output=True,
            text=True,
        )
        assert setup.returncode == 0, setup.stderr
    input_text = json.dumps(hook_input or {"hook_event_name": "Stop", "cwd": str(repo)})
    result = subprocess.run(
        [NODE, str(runtime), "review-gate", *(args or [])],
        cwd=repo,
        env=gate_env,
        input=input_text,
        capture_output=True,
        text=True,
    )
    prompts = [
        path.read_text()
        for path in sorted(capture_dir.glob("prompt-*.txt"), key=lambda p: int(p.stem.split("-")[1]))
    ]
    return result, prompts, capture_dir


def review_gate_argvs(capture_dir):
    return [
        json.loads(path.read_text())
        for path in sorted(capture_dir.glob("argv-*.json"), key=lambda p: int(p.stem.split("-")[1]))
    ]


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


def markdown_section_after(text, anchor, start_marker, end_marker, label):
    anchor_index = text.find(anchor)
    assert anchor_index >= 0, f"{anchor!r} missing from {label}"
    start = text.find(start_marker, anchor_index)
    assert start >= 0, f"{start_marker!r} missing from {label}"
    body_start = start + len(start_marker)
    end = text.find(end_marker, body_start)
    assert end >= 0, f"{end_marker!r} missing after {start_marker!r} in {label}"
    return text[body_start:end]


def test_claude_skills_encode_natural_language_routing():
    contract = json.loads((PLUGIN / "contracts" / "natural-language-routing.json").read_text(encoding="utf8"))

    for skill in contract["routedClaudeSkills"]:
        text = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf8")
        for anchor in contract["requiredAnchors"]:
            assert anchor in text, f"{anchor!r} missing from {skill}"
        for phrase in contract["requiredCommonPolicyPhrases"]:
            assert phrase in text, f"{phrase!r} missing from {skill}"
        for phrase in contract["requiredPolicyPhrasesBySkill"].get(skill, []):
            assert phrase in text, f"{phrase!r} missing from {skill}"
        for marker in contract["skillMarkers"].get(skill, []):
            assert marker in text, f"{marker!r} missing from {skill}"
        user_examples = markdown_section_after(
            text,
            "## Natural-Language Claude Routing",
            contract["userExamplesStart"],
            contract["userExamplesEnd"],
            skill,
        )
        for forbidden in contract["forbiddenUserExampleSubstrings"]:
            assert forbidden not in user_examples, f"{forbidden!r} leaked into user examples for {skill}"


def test_claude_docs_explain_natural_language_routing():
    contract = json.loads((PLUGIN / "contracts" / "natural-language-routing.json").read_text(encoding="utf8"))

    for relative_path, phrases in contract["docsRequiredPhrases"].items():
        text = (ROOT / relative_path).read_text(encoding="utf8")
        for phrase in phrases:
            assert phrase in text, f"{phrase!r} missing from {relative_path}"


def test_fable_natural_language_routing_documented():
    skill_files = [
        PLUGIN / "skills" / "claude-review" / "SKILL.md",
        PLUGIN / "skills" / "claude-multi-review" / "SKILL.md",
        PLUGIN / "skills" / "claude-adversarial-review" / "SKILL.md",
        PLUGIN / "skills" / "claude-plan" / "SKILL.md",
        PLUGIN / "skills" / "claude-rescue" / "SKILL.md",
        PLUGIN / "skills" / "claude-review-gate" / "SKILL.md",
        PLUGIN / "skills" / "claude-subagent-review" / "SKILL.md",
    ]
    for path in skill_files:
        text = path.read_text(encoding="utf8")
        assert "Fable" in text
        assert "--quality max" in text
        assert "capabilities" in text or "能力" in text
        assert "Stop hook" in text or "review-gate" in text or "Stop 钩子" in text


def test_fable_policy_documented_in_readmes():
    readmes = [
        PLUGIN / "README.md",
        ROOT / "README.md",
        ROOT / "docs" / "README.en.md",
        ROOT / "docs" / "README.zh-CN.md",
    ]
    for path in readmes:
        text = path.read_text(encoding="utf8")
        assert "Fable" in text
        assert "best" in text
        assert "fallback" in text.lower() or "回退" in text
        assert "Stop hook" in text or "Stop 钩子" in text


def test_plugin_manifest_is_valid():
    manifest_path = PLUGIN / ".codex-plugin" / "plugin.json"
    data = json.loads(manifest_path.read_text())
    assert data["name"] == "claude-for-codex"
    assert data["version"] == "0.18.1"
    assert data["skills"] == "./skills/"
    assert "hooks" not in data
    assert data["interface"]["displayName"] == "Claude for Codex"
    assert len(data["interface"]["defaultPrompt"]) <= 3
    asset_paths = [
        data["interface"]["composerIcon"],
        data["interface"]["logo"],
        *data["interface"]["screenshots"],
    ]
    assert all(path.startswith("./assets/") for path in asset_paths)


def test_claude_manifest_version_is_0160():
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf8"))
    assert manifest["version"] == "0.18.1"


def test_version_and_docs_describe_forwarding_and_mcp():
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf8"))
    assert manifest["version"] == "0.18.1"

    readme = (PLUGIN / "README.md").read_text(encoding="utf8")
    root_readme = (ROOT / "README.md").read_text(encoding="utf8")
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
        assert "--agent-team sdk-subagents" in text
        assert "--backend sdk" in text
        assert "@anthropic-ai/claude-agent-sdk" in text
        assert "--native-structured" in text
        assert "--stream-progress" in text
        assert "--confirm-cost" in text
        assert "claude-ultrareview" in text or "ultrareview" in text

    for text in (root_readme, readme, en, zh):
        assert "--agent-team sdk-subagents" in text
        assert "--backend sdk" in text
        assert "@anthropic-ai/claude-agent-sdk" in text
        assert "--native-structured" in text
        assert "--stream-progress" in text
        assert "--confirm-cost" in text
        assert "ultrareview" in text
        assert "model alias registry" in text
        assert "outcome classification" in text
        assert "doctor --json" in text
        assert "fork-safe CI dogfood" in text
        assert "fresh isolated context" in text

    assert "转发" in zh
    assert "MCP" in zh
    assert "只读 Git" in zh
    assert "--agent-team sdk-subagents" in zh
    assert "--backend sdk" in zh
    assert "@anthropic-ai/claude-agent-sdk" in zh
    assert "--native-structured" in zh
    assert "--stream-progress" in zh
    assert "--confirm-cost" in zh
    assert "ultrareview" in zh


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
    for command in ["setup", "capabilities", "doctor", "review", "adversarial-review", "multi-review", "ultrareview", "plan", "status", "review-gate", "jobs", "result", "cancel", "rescue", "report", "release-check", "github-actions", "roles", "mailbox", "leases", "reserve-job", "run-reserved-job", "subagent-command"]:
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
    assert "const runGit = options.gitRunner ?? git;" in text
    command_patterns = {
        "status": r'runGit\(\[\s*"status",\s*"--short",\s*"--untracked-files=all",\s*\.\.\.pathArgs\s*\]\)',
        "staged stat": r'runGit\(\[\s*"diff",\s*"--cached",\s*"--stat",\s*\.\.\.pathArgs\s*\]\)',
        "unstaged stat": r'runGit\(\[\s*"diff",\s*"--stat",\s*\.\.\.pathArgs\s*\]\)',
        "branch stat": r'runGit\(\[\s*"diff",\s*"--stat",\s*`\$\{base\}\.\.\.HEAD`,\s*\.\.\.pathArgs\s*\]\)',
        "branch names": r'runGit\(\[\s*"diff",\s*"--name-only",\s*`\$\{base\}\.\.\.HEAD`,\s*\.\.\.pathArgs\s*\]\)',
        "head names": r'runGit\(\[\s*"diff",\s*"--name-only",\s*"HEAD",\s*\.\.\.pathArgs\s*\]\)',
    }
    for label, pattern in command_patterns.items():
        assert re.search(pattern, text), label


def test_runtime_keeps_claude_tools_read_only():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    text = runtime.read_text()
    backend = (PLUGIN / "scripts" / "lib" / "claude-backend.mjs").read_text(encoding="utf8")
    assert '"Bash"' not in re.search(r"READ_ONLY_BUILTIN_TOOLS = Object\.freeze\(\[(.*?)\]\);", text, re.S).group(1)
    assert '"Bash"' not in re.search(r"READ_ONLY_MCP_TOOLS = Object\.freeze\(\[(.*?)\]\);", text, re.S).group(1)
    assert 'args.push("--disallowedTools", formattedDenyTools)' in text
    assert "formatDenyToolsForCli(denyTools)" in text
    assert 'export const WRITE_DENY_TOOLS = Object.freeze(["Edit", "Write", "MultiEdit", "Bash"])' in backend


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
        r'runGit\(\[\s*"rev-parse",\s*"--verify",\s*"HEAD"\s*\]\)',
        text,
    )
    assert re.search(r'runGit\(\[\s*"diff",\s*"--name-only",\s*"HEAD",\s*\.\.\.pathArgs\s*\]\)', text)
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
    assert "--disable-slash-commands" in argv
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
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


def test_read_only_review_does_not_create_planning_side_effect_files(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    argv_file = tmp_path / "argv.json"
    repo.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "calc.py").write_text("def div(a, b):\n    return a / b\n", encoding="utf8")
    subprocess.run(["git", "add", "calc.py"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "calc.py").write_text("def div(a, b):\n    return 0\n", encoding="utf8")

    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('fs');\n"
        f"fs.writeFileSync({json.dumps(str(argv_file))}, JSON.stringify(process.argv.slice(2)));\n"
        "for (const name of ['task_plan.md', 'findings.md', 'progress.md']) {\n"
        "  if (!process.argv.includes('--disable-slash-commands') || !process.argv.includes('--no-session-persistence')) {\n"
        "    fs.writeFileSync(name, 'side effect');\n"
        "  }\n"
        "}\n"
        "process.stdout.write('OK\\n');\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_CODE_PATH"] = str(fake_claude)

    result = subprocess.run(
        [NODE, str(runtime), "review", "--scope", "working-tree"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not (repo / "task_plan.md").exists()
    assert not (repo / "findings.md").exists()
    assert not (repo / "progress.md").exists()
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status == " M calc.py\n"


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


def test_review_gate_default_quality_is_conservative(tmp_path):
    result, prompts, capture_dir = run_fake_review_gate(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""
    assert prompts
    argv_files = sorted(capture_dir.glob("argv-*.json"))
    assert argv_files
    for argv_file in argv_files:
        argv = json.loads(argv_file.read_text())
        assert argv[argv.index("--model") + 1] == "sonnet"
        assert argv[argv.index("--effort") + 1] == "high"
        assert "ultrareview" not in argv


def test_review_gate_env_quality_max_is_capped_to_standard(tmp_path):
    result, prompts, capture_dir = run_fake_review_gate(
        tmp_path,
        extra_env={"CLAUDE_FOR_CODEX_QUALITY": "max"},
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert prompts
    argv_files = sorted(capture_dir.glob("argv-*.json"))
    assert argv_files
    for argv_file in argv_files:
        argv = json.loads(argv_file.read_text())
        assert argv[argv.index("--model") + 1] == "sonnet"
        assert argv[argv.index("--effort") + 1] == "high"


def test_review_gate_explicit_auto_quality_is_still_capped(tmp_path):
    result, prompts, capture_dir = run_fake_review_gate(
        tmp_path,
        args=["--quality", "auto"],
        extra_env={"CLAUDE_FOR_CODEX_QUALITY": "max"},
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert prompts
    argv_files = sorted(capture_dir.glob("argv-*.json"))
    assert argv_files
    for argv_file in argv_files:
        argv = json.loads(argv_file.read_text())
        assert argv[argv.index("--model") + 1] == "sonnet"
        assert argv[argv.index("--effort") + 1] == "high"


def test_review_gate_explicit_max_quality_can_escalate_manual_gate(tmp_path):
    result, prompts, capture_dir = run_fake_review_gate(
        tmp_path,
        args=["--quality", "max"],
        extra_env={"CLAUDE_FOR_CODEX_QUALITY": "standard"},
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert prompts
    argv_files = sorted(capture_dir.glob("argv-*.json"))
    assert argv_files
    for argv_file in argv_files:
        argv = json.loads(argv_file.read_text())
        assert argv[argv.index("--model") + 1] == "opus"
        assert argv[argv.index("--effort") + 1] == "max"


def test_review_gate_auto_does_not_select_fable_for_stop_hook(tmp_path):
    result, _prompts, capture_dir = run_fake_review_gate(
        tmp_path,
        extra_env={"CLAUDE_FOR_CODEX_QUALITY": "max"},
        extra_help="--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    argvs = review_gate_argvs(capture_dir)
    assert argvs
    for argv in argvs:
        assert argv[argv.index("--model") + 1] == "sonnet"
        assert argv[argv.index("--effort") + 1] == "high"
        assert "--fallback-model" not in argv


def test_review_gate_auto_does_not_probe_top_model_help(tmp_path):
    marker = tmp_path / "help-probed"
    result, _prompts, capture_dir = run_fake_review_gate(
        tmp_path,
        extra_env={"CLAUDE_FOR_CODEX_QUALITY": "max"},
        gate_extra_env={
            "FAIL_ON_HELP": "1",
            "HELP_PROBE_MARKER": str(marker),
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not marker.exists()
    argvs = review_gate_argvs(capture_dir)
    assert argvs
    for argv in argvs:
        assert "--fallback-model" not in argv


def test_review_gate_manual_max_can_select_top_model(tmp_path):
    result, _prompts, capture_dir = run_fake_review_gate(
        tmp_path,
        args=["--quality", "max"],
        extra_help="--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    argvs = review_gate_argvs(capture_dir)
    assert argvs
    for argv in argvs:
        assert argv[argv.index("--model") + 1] == "fable"
        assert argv[argv.index("--effort") + 1] == "max"
        assert argv[argv.index("--fallback-model") + 1] == "opus,sonnet"


def test_review_gate_invalid_quality_env_fails_open_without_claude(tmp_path):
    result, prompts, _capture_dir = run_fake_review_gate(
        tmp_path,
        extra_env={"CLAUDE_FOR_CODEX_QUALITY": "ultracode"},
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert "quality policy failed; allowing stop" in result.stderr
    assert prompts == []


def test_review_env_quality_max_escalates_manual_review(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--scope", "working-tree"],
        extra_env={"CLAUDE_FOR_CODEX_QUALITY": "max"},
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "max"


def test_quality_flag_overrides_quality_env(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "fast", "--scope", "working-tree"],
        extra_env={"CLAUDE_FOR_CODEX_QUALITY": "max"},
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "low"


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


def test_review_quality_strong_forwards_alias_and_effort(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "strong", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "xhigh"
    assert "ultracode" not in argv
    assert "ultrareview" not in argv


def test_review_quality_max_forwards_max_effort_without_ultrareview(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert {"Read", "Grep", "Glob"}.issubset(set(argv[argv.index("--tools") + 1].split(",")))
    assert argv[argv.index("--disallowedTools") + 1] == "Edit,Write,MultiEdit,Bash"
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "max"
    assert "ultrareview" not in argv


def test_review_quality_max_prefers_fable_when_supported(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
        extra_help="--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "fable"
    assert argv[argv.index("--effort") + 1] == "max"
    assert argv[argv.index("--fallback-model") + 1] == "opus,sonnet"
    assert "ultrareview" not in argv


def test_review_quality_max_prefers_best_before_fable_when_supported(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
        extra_help="--model <model> alias best fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "best"
    assert argv[argv.index("--effort") + 1] == "max"
    assert argv[argv.index("--fallback-model") + 1] == "opus,sonnet"


def test_review_quality_max_falls_back_to_opus_without_top_alias(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
        extra_help="--model <model> alias opus sonnet --fallback-model <model>",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "max"
    assert "--fallback-model" not in argv


def test_review_quality_max_does_not_treat_best_prose_as_alias(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
        extra_help="Use best practices for reviews. --model <model> alias opus sonnet --fallback-model <model>",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "opus"
    assert "--fallback-model" not in argv


def test_review_quality_max_does_not_treat_fable_prose_as_alias(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
        extra_help="Tell a fable about code review. --model <model> alias opus sonnet --fallback-model <model>",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "opus"
    assert "--fallback-model" not in argv


def test_review_quality_max_preserves_user_fallback_model(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--fallback-model", "sonnet", "--scope", "working-tree"],
        extra_help="--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "fable"
    assert argv[argv.index("--fallback-model") + 1] == "sonnet"
    assert argv.count("--fallback-model") == 1


def test_review_accepts_explicit_default_model_alias(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--model", "default", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "default"


def test_quality_max_preserves_user_fallback_model(tmp_path):
    result, _prompts, argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--quality", "max", "--fallback-model", "sonnet", "--roles", "correctness", "--scope", "working-tree"],
        extra_help="--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )

    assert result.returncode == 0, result.stderr
    assert len(argvs) == 1
    argv = argvs[0]
    assert argv[argv.index("--model") + 1] == "fable"
    assert argv[argv.index("--fallback-model") + 1] == "sonnet"
    assert argv.count("--fallback-model") == 1


def test_quality_max_does_not_emit_fallback_without_cli_support(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
        extra_help="--model <model> alias fable opus sonnet",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "fable"
    assert "--fallback-model" not in argv


def test_quality_max_uses_comma_separated_native_fallback_when_help_mentions_it(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
        extra_help="--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--fallback-model") + 1] == "opus,sonnet"


def test_quality_max_uses_custom_comma_native_fallback_when_configured(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
        extra_env={"CLAUDE_FOR_CODEX_TOP_MODEL_FALLBACK": "sonnet,opus"},
        extra_help="--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--fallback-model") + 1] == "sonnet,opus"


def test_quality_max_invalid_top_model_env_falls_back_to_detected_alias(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
        extra_env={"CLAUDE_FOR_CODEX_TOP_MODEL": "-inject"},
        extra_help="--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "fable"


def test_quality_max_uses_single_native_fallback_when_help_lacks_comma_support(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
        extra_help="--model <model> alias fable opus sonnet --fallback-model <model>",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--fallback-model") + 1] == "opus"


def test_quality_max_uses_first_custom_fallback_without_comma_support(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--scope", "working-tree"],
        extra_env={"CLAUDE_FOR_CODEX_TOP_MODEL_FALLBACK": "sonnet,opus"},
        extra_help="--model <model> alias fable opus sonnet --fallback-model <model>",
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--fallback-model") + 1] == "sonnet"


def test_quality_max_explicit_model_does_not_probe_help(tmp_path):
    marker = tmp_path / "help-probed"
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--model", "sonnet", "--scope", "working-tree"],
        extra_env={
            "FAIL_ON_HELP": "1",
            "HELP_PROBE_MARKER": str(marker),
        },
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert "--fallback-model" not in argv
    assert not marker.exists()


def quality_policy_probe(capabilities, env=None):
    module_uri = (PLUGIN / "scripts" / "lib" / "quality-policy.mjs").as_uri()
    script = f"""
import {{ resolveQualityPolicy }} from {json.dumps(module_uri)};
const policy = resolveQualityPolicy('review', {{ quality: 'max' }}, {json.dumps(env or {})}, {{}}, {json.dumps(capabilities)});
console.log(JSON.stringify(policy));
"""
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_model_registry_accepts_dynamic_aliases_without_dated_defaults():
    module_uri = (PLUGIN / "scripts" / "lib" / "model-registry.mjs").as_uri()
    code = f"""
import {{
  normalizeModelSelection,
  resolveTopModelFromCapabilities,
  MODEL_ALIAS_REGISTRY
}} from {json.dumps(module_uri)};

const aliases = ['default', 'best', 'fable', 'opus', 'sonnet', 'haiku', 'opusplan', 'opus[1m]', 'sonnet[1m]', 'inherit'];
for (const alias of aliases) {{
  const resolved = normalizeModelSelection(alias);
  if (!resolved.valid) throw new Error(alias + ' rejected');
}}
const top = resolveTopModelFromCapabilities({{ modelAliases: {{ best: true, fable: true, opus: true }} }}, {{}});
if (top.model !== 'best') throw new Error('expected best top model, got ' + top.model);
const invalidEnvFallback = resolveTopModelFromCapabilities({{ modelAliases: {{ fable: true, opus: true }} }}, {{ CLAUDE_FOR_CODEX_TOP_MODEL: '-inject' }});
if (invalidEnvFallback.model !== 'fable') throw new Error('invalid TOP_MODEL should fall back, got ' + invalidEnvFallback.model);
const defaultEnv = resolveTopModelFromCapabilities({{ modelAliases: {{ fable: true, opus: true }} }}, {{ CLAUDE_FOR_CODEX_TOP_MODEL: 'default' }});
if (defaultEnv.model !== 'default' || defaultEnv.fallbackModel !== '') throw new Error('default TOP_MODEL not preserved: ' + JSON.stringify(defaultEnv));
if (MODEL_ALIAS_REGISTRY.some((entry) => /claude-(opus|sonnet|haiku|fable)-\\d/.test(entry.alias))) {{
  throw new Error('registry contains dated model alias');
}}
console.log(JSON.stringify({{ ok: true, top, invalidEnvFallback, defaultEnv }}));
"""
    result = subprocess.run([NODE, "--input-type=module", "-e", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["top"]["model"] == "best"


def test_quality_policy_accepts_one_million_context_alias_and_opusplan_override():
    module_uri = (PLUGIN / "scripts" / "lib" / "quality-policy.mjs").as_uri()
    code = f"""
import {{ resolveQualityPolicy }} from {json.dumps(module_uri)};
const oneMillion = resolveQualityPolicy('review', {{ model: 'opus[1m]' }}, {{}}, {{}}, {{}});
const opusPlan = resolveQualityPolicy('plan', {{ model: 'opusplan', effort: 'max' }}, {{}}, {{}}, {{}});
if (oneMillion.model !== 'opus[1m]') throw new Error('opus[1m] override lost');
if (opusPlan.model !== 'opusplan' || opusPlan.effort !== 'max') throw new Error('opusplan/max override lost');
console.log(JSON.stringify({{ oneMillion, opusPlan }}));
"""
    result = subprocess.run([NODE, "--input-type=module", "-e", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_outcome_classifier_detects_refusal_and_fallback_served():
    module_uri = (PLUGIN / "scripts" / "lib" / "outcome-classifier.mjs").as_uri()
    code = f"""
import {{ classifyClaudeOutcome }} from {json.dumps(module_uri)};
const refusal = classifyClaudeOutcome({{
  status: 0,
  metadata: {{ sdkEvents: [{{ type: 'result', stop_reason: 'refusal', stop_details: {{ category: 'cyber' }} }}] }}
}});
const served = classifyClaudeOutcome({{
  status: 0,
  metadata: {{ sdkEvents: [{{ type: 'result', stop_reason: 'end_turn', usage: {{ iterations: [{{ type: 'message' }}, {{ type: 'fallback_message' }}] }} }}] }}
}});
const timeout = classifyClaudeOutcome({{ status: 1, stderr: 'Error: ETIMEDOUT' }});
const unknownDeny = classifyClaudeOutcome({{ status: 1, stderr: 'Permission deny rule "MultiEdit" matches no known tool.' }});
const successfulReviewText = classifyClaudeOutcome({{
  status: 0,
  stdout: 'Reviewed retry timeout handling, API key redaction, and 429 rate limit docs with no findings.'
}});
if (refusal.kind !== 'refusal' || refusal.refusalCategory !== 'cyber' || refusal.ok !== false) throw new Error(JSON.stringify(refusal));
if (served.kind !== 'success' || served.servedByFallback !== true || served.stopReason !== 'end_turn') throw new Error(JSON.stringify(served));
if (timeout.kind !== 'timeout') throw new Error(JSON.stringify(timeout));
if (unknownDeny.kind !== 'unknown_deny_tool') throw new Error(JSON.stringify(unknownDeny));
if (successfulReviewText.kind !== 'success' || successfulReviewText.ok !== true) throw new Error(JSON.stringify(successfulReviewText));
console.log(JSON.stringify({{ refusal, served, timeout, unknownDeny, successfulReviewText }}));
"""
    result = subprocess.run([NODE, "--input-type=module", "-e", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_sdk_backend_metadata_compacts_events_for_classification_without_raw_text(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sdk_module, _capture = write_fake_claude_sdk(
        tmp_path,
        extra_js="""
  yield {
    type: 'result',
    subtype: 'success',
    result: 'RAW_REFUSAL_TEXT_SHOULD_NOT_BE_IN_METADATA',
    text: 'RAW_TEXT_SHOULD_NOT_BE_IN_METADATA',
    stop_reason: 'refusal',
    stop_details: { category: 'cyber', raw_prompt: 'SECRET_PROMPT_SHOULD_NOT_BE_IN_METADATA' },
    usage: { input_tokens: 9, output_tokens: 1, iterations: [{ type: 'message', model: 'fable' }, { type: 'fallback_message', model: 'opus' }] }
  };
  return;
""",
    )
    backend_uri = (PLUGIN / "scripts" / "lib" / "claude-backend.mjs").as_uri()
    classifier_uri = (PLUGIN / "scripts" / "lib" / "outcome-classifier.mjs").as_uri()
    code = f"""
import {{ runSdkPrompt }} from {json.dumps(backend_uri)};
import {{ classifyClaudeOutcome }} from {json.dumps(classifier_uri)};
const result = await runSdkPrompt('prompt containing SECRET_PROMPT_SHOULD_NOT_BE_IN_METADATA', {{}}, {{ cwd: {json.dumps(str(repo))} }});
result.metadata.outcome = classifyClaudeOutcome(result);
console.log(JSON.stringify(result.metadata));
"""
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    result = subprocess.run([NODE, "--input-type=module", "-e", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    metadata = json.loads(result.stdout)
    assert metadata["sdkEvents"][0]["stop_reason"] == "refusal"
    assert metadata["sdkEvents"][0]["stop_details"] == {"category": "cyber"}
    assert metadata["sdkEvents"][0]["usage"]["iterations"][1]["type"] == "fallback_message"
    assert metadata["outcome"]["kind"] == "refusal"
    assert metadata["outcome"]["servedByFallback"] is True
    serialized = json.dumps(metadata)
    assert "RAW_REFUSAL_TEXT_SHOULD_NOT_BE_IN_METADATA" not in serialized
    assert "SECRET_PROMPT_SHOULD_NOT_BE_IN_METADATA" not in serialized


def test_cli_fallback_model_is_added_per_request_not_global_state(tmp_path):
    claude = tmp_path / "claude"
    claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('claude fake')\n"
        "    raise SystemExit(0)\n"
        "if sys.argv[1:] == ['--help']:\n"
        "    print('--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list --effort <level>')\n"
        "    raise SystemExit(0)\n"
        "print(json.dumps({'argv': sys.argv[1:]}))\n",
        encoding="utf8",
    )
    claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_CODE_PATH"] = str(claude)
    env["CLAUDE_FOR_CODEX_TOP_MODEL"] = "fable"
    env["CLAUDE_FOR_CODEX_TOP_MODEL_FALLBACK"] = "opus,sonnet"
    result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "claude-companion.mjs"), "review", "--quality", "max"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    argv = json.loads(result.stdout)["argv"]
    assert "--model" in argv and argv[argv.index("--model") + 1] == "fable"
    assert "--fallback-model" in argv and argv[argv.index("--fallback-model") + 1] == "opus,sonnet"


def test_quality_policy_max_prefers_fable_when_supported():
    policy = quality_policy_probe({"fable": True, "opus": True, "sonnet": True})
    assert policy["model"] == "fable"
    assert policy["effort"] == "max"
    assert policy["topModelProfile"] is True
    assert policy["topModelSelected"] is True
    assert policy["fallbackModel"] == "opus,sonnet"


def test_quality_policy_max_prefers_best_before_fable_when_supported():
    policy = quality_policy_probe({"best": True, "fable": True, "opus": True, "sonnet": True})
    assert policy["model"] == "best"
    assert policy["effort"] == "max"
    assert policy["topModelProfile"] is True
    assert policy["topModelSelected"] is True
    assert policy["fallbackModel"] == "opus,sonnet"


def test_quality_policy_max_falls_back_to_opus_without_top_alias():
    policy = quality_policy_probe({"opus": True, "sonnet": True})
    assert policy["model"] == "opus"
    assert policy["effort"] == "max"
    assert policy["topModelProfile"] is True
    assert policy["topModelSelected"] is False
    assert policy["fallbackModel"] == ""


def test_quality_policy_top_model_fallback_env_applies_to_capabilities():
    policy = quality_policy_probe(
        {"fable": True, "opus": True, "sonnet": True},
        {"CLAUDE_FOR_CODEX_TOP_MODEL_FALLBACK": "sonnet,opus"},
    )
    assert policy["model"] == "fable"
    assert policy["fallbackModel"] == "sonnet,opus"


def test_quality_policy_top_model_opus_env_suppresses_self_fallback():
    policy = quality_policy_probe(
        {"fable": True, "opus": True, "sonnet": True},
        {"CLAUDE_FOR_CODEX_TOP_MODEL": "opus"},
    )
    assert policy["model"] == "opus"
    assert policy["topModelSelected"] is False
    assert policy["fallbackModel"] == ""


def test_quality_preserves_explicit_model_and_effort_overrides(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "max", "--model", "sonnet", "--effort", "medium", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "medium"


def test_quality_auto_escalates_adversarial_review(tmp_path):
    result, _prompt, argv = run_fake_claude_adversarial_review(
        tmp_path,
        ["--quality", "auto", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "xhigh"


def test_quality_auto_defaults_to_standard_for_small_review(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "auto", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "high"


def test_quality_auto_branch_scope_counts_base_diff(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "auto", "--scope", "branch", "--base", "HEAD~1"],
        commit_head=True,
        branch_file_count=15,
        branch_lines_per_file=100,
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "xhigh"


def test_quality_auto_default_scope_counts_base_diff(tmp_path):
    result, _prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "auto", "--base", "HEAD~1"],
        commit_head=True,
        branch_file_count=15,
        branch_lines_per_file=100,
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "xhigh"


def test_invalid_quality_exits_2_without_calling_claude(tmp_path):
    result, prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--quality", "ultracode", "--scope", "working-tree"],
    )

    assert result.returncode == 2
    assert 'Invalid --quality "ultracode"' in result.stderr
    assert "Valid values: auto, fast, standard, strong, max" in result.stderr
    assert prompt == ""
    assert argv == []


def test_invalid_effort_ultracode_exits_2_without_calling_claude(tmp_path):
    result, prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--effort", "ultracode", "--scope", "working-tree"],
    )

    assert result.returncode == 2
    assert 'Invalid --effort "ultracode"' in result.stderr
    assert "Valid values: low, medium, high, xhigh, max" in result.stderr
    assert prompt == ""
    assert argv == []


def test_quality_env_invalid_exits_2_for_manual_review(tmp_path):
    result, prompt, argv = run_fake_claude_review(
        tmp_path,
        ["--scope", "working-tree"],
        extra_env={"CLAUDE_FOR_CODEX_QUALITY": "ultracode"},
    )

    assert result.returncode == 2
    assert 'Invalid CLAUDE_FOR_CODEX_QUALITY "ultracode"' in result.stderr
    assert prompt == ""
    assert argv == []


def test_plan_quality_auto_defaults_to_standard(tmp_path):
    result, _prompt, argv = run_fake_claude_plan(
        tmp_path,
        ["--quality", "auto", "make a plan"],
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "high"


def test_rescue_quality_auto_escalates_to_strong(tmp_path):
    result, _prompt, argv = run_fake_claude_rescue(
        tmp_path,
        ["--quality", "auto", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "xhigh"


def test_quality_auto_in_non_git_directory_does_not_crash(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    fake_bin = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    fake_bin.mkdir()
    capture_dir.mkdir()
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
print("FAKE_CLAUDE_OK")
"""
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CAPTURE_DIR"] = str(capture_dir)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "plan", "--quality", "auto", "review non git"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    argv = json.loads((capture_dir / "argv.json").read_text())
    assert "--model" in argv
    assert "--effort" in argv


def test_multi_review_quality_auto_uses_strong_for_risky_roles(tmp_path):
    result, _prompts, argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--quality", "auto", "--roles", "correctness,security,tests,release,adversarial", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    assert len(argvs) == 5
    for argv in argvs:
        assert argv[argv.index("--model") + 1] == "opus"
        assert argv[argv.index("--effort") + 1] == "xhigh"


def test_multi_review_quality_auto_can_escalate_to_top_model_for_large_diff(tmp_path):
    result, _prompts, argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--quality", "auto", "--roles", "correctness", "--scope", "branch", "--base", "HEAD~1"],
        commit_head=True,
        branch_file_count=15,
        branch_lines_per_file=100,
        extra_help="--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )

    assert result.returncode == 0, result.stderr
    assert len(argvs) == 1
    argv = argvs[0]
    assert argv[argv.index("--model") + 1] == "fable"
    assert argv[argv.index("--effort") + 1] == "max"
    assert argv[argv.index("--fallback-model") + 1] == "opus,sonnet"


def test_multi_review_quality_fast_can_be_requested_explicitly(tmp_path):
    result, _prompts, argvs = run_fake_claude_multi_review(
        tmp_path,
        ["--quality", "fast", "--roles", "correctness,tests", "--scope", "working-tree"],
    )

    assert result.returncode == 0, result.stderr
    for argv in argvs:
        assert argv[argv.index("--model") + 1] == "sonnet"
        assert argv[argv.index("--effort") + 1] == "low"


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
    env["HOME"] = str(tmp_path / "home")
    env["PATH"] = str(tmp_path / "bin")

    enabled = subprocess.run(
        [NODE, str(runtime), "setup", "--enable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert enabled.returncode == 0, enabled.stderr
    payload = json.loads(enabled.stdout)
    assert payload["claudeAvailable"] is False
    assert payload["gitAvailable"] is False
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
    assert payload["hooks"]["compatibility"]["codexSubset"] is True
    assert "PreToolUse" in payload["hooks"]["compatibility"]["knownClaudeEvents"]


def test_hook_compat_reports_codex_subset_without_mutating_hooks_json():
    module_uri = (PLUGIN / "scripts" / "lib" / "hook-compat.mjs").as_uri()
    code = f"""
import {{ hookCompatibilityReport }} from {json.dumps(module_uri)};
const report = hookCompatibilityReport({{
  installedEvents: ['SessionStart', 'SessionEnd', 'UserPromptSubmit', 'Stop']
}});
if (!report.supportedEvents.includes('Stop')) throw new Error('Stop missing');
if (!report.knownClaudeEvents.includes('PreToolUse')) throw new Error('Claude known event missing');
if (report.unsupportedInstalledEvents.length) throw new Error('unexpected unsupported installed event');
if (!report.codexSubset) throw new Error('codexSubset should be true');
if (report.decisionShapes.Stop.shape !== 'top-level-decision') throw new Error('Stop shape mismatch');
if (report.decisionShapes.PreToolUse.shape !== 'hook-specific-permission') throw new Error('PreToolUse shape mismatch');
console.log(JSON.stringify(report));
"""
    result = subprocess.run([NODE, "--input-type=module", "-e", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_doctor_json_is_cheap_and_reports_core_surfaces(tmp_path):
    claude = tmp_path / "claude"
    claude.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"--version\" ]]; then echo '2.1.173 (Claude Code)'; exit 0; fi\n"
        "if [[ \"$1\" == \"--help\" ]]; then echo '--model sonnet opus fable best opusplan opus[1m] sonnet[1m] --fallback-model comma-separated --effort low medium high xhigh max'; exit 0; fi\n"
        "echo 'doctor should not run a prompt' >&2\n"
        "exit 42\n",
        encoding="utf8",
    )
    claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_CODE_PATH"] = str(claude)
    result = subprocess.run(
        [NODE, str(PLUGIN / "scripts" / "claude-companion.mjs"), "doctor", "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["checks"]["claude"]["available"] is True
    assert payload["checks"]["modelAliases"]["opusplan"] is True
    assert payload["checks"]["modelAliases"]["opus1m"] is True
    assert payload["checks"]["modelAliases"]["sonnet1m"] is True
    assert payload["checks"]["fallbackModel"]["supported"] is True
    assert payload["checks"]["hooks"]["codexSubset"] is True
    assert payload["checks"]["hookFiles"]["hooksJson"] is True
    assert "semanticProviders" in payload["checks"]
    assert "doctor should not run a prompt" not in result.stderr


def test_capabilities_reports_quality_policy_metadata(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    result = subprocess.run(
        [NODE, str(runtime), "capabilities"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    policy = payload["qualityPolicy"]
    assert policy["defaultQualityEnv"] == "CLAUDE_FOR_CODEX_QUALITY"
    assert policy["topModelEnv"] == "CLAUDE_FOR_CODEX_TOP_MODEL"
    assert policy["topModelFallbackEnv"] == "CLAUDE_FOR_CODEX_TOP_MODEL_FALLBACK"
    assert policy["defaultTopModelFallback"] == "opus,sonnet"
    assert policy["qualities"] == ["auto", "fast", "standard", "strong", "max"]
    assert policy["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert "modelAliases" in policy
    assert "fallbackModelList" in policy
    assert policy["ultracodeEffortSupported"] is False
    assert policy["ultrareviewAutomatic"] is False


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
        "    print('--agent <agent> --agents <json> --json-schema <schema> --include-partial-messages --fallback-model <model> accepts a comma-separated list --max-budget-usd <amount> --resume --continue --session-id --fork-session --output-format --model alias best fable opus sonnet haiku opusplan opus[1m] sonnet[1m]')\n"
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
    assert payload["claude"]["modelAliases"]["best"] is True
    assert payload["claude"]["modelAliases"]["fable"] is True
    assert payload["claude"]["modelAliases"]["opus"] is True
    assert payload["claude"]["modelAliases"]["sonnet"] is True
    assert payload["claude"]["modelAliases"]["haiku"] is True
    assert payload["claude"]["modelAliases"]["opusplan"] is True
    assert payload["claude"]["modelAliases"]["opus1m"] is True
    assert payload["claude"]["modelAliases"]["sonnet1m"] is True
    assert payload["claude"]["fallbackModel"] is True
    assert payload["claude"]["fallbackModelList"] is True


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
    assert query["options"]["settingSources"] == []
    assert query["options"]["skills"] == []
    assert query["options"]["hooks"] == {}
    assert query["options"]["plugins"] == []
    assert query["options"]["persistSession"] is False
    assert query["options"]["env"]["CLAUDE_FOR_CODEX_ISOLATED_REVIEW"] == "1"
    assert query["options"]["env"]["HOME"] == os.environ["HOME"]
    assert query["options"]["env"]["PATH"] == env["PATH"]
    assert query["options"]["env"].get("CLAUDE_CONFIG_DIR") == os.environ.get("CLAUDE_CONFIG_DIR")
    assert set(["Read", "Grep", "Glob"]).issubset(set(query["allowedTools"]))
    assert set(["Edit", "Write", "MultiEdit", "Bash"]).issubset(set(query["disallowedTools"]))
    assert "Agent" not in query["allowedTools"]
    assert "Agent" not in query["options"]["allowedTools"]
    assert "agents" not in query
    assert "mcp__claude-for-codex-git__git_status" in query["allowedTools"]
    assert "claude-for-codex-git" in query["mcpServers"]


def test_sdk_subagent_definitions_follow_official_read_only_contract():
    module_uri = (PLUGIN / "scripts" / "lib" / "claude-native-review.mjs").as_uri()
    code = f"""
import {{ buildNativeReviewAgents }} from {json.dumps(module_uri)};
const agents = buildNativeReviewAgents(['security'], {{ model: 'opus[1m]', effort: 'max', structuredJson: true }});
const agent = agents.cfc_security;
if (!agent) throw new Error('missing cfc_security');
if (agent.model !== 'opus[1m]') throw new Error('model alias not preserved: ' + agent.model);
if (agent.effort !== 'max') throw new Error('effort not preserved');
if (!agent.tools.every((tool) => ['Read', 'Grep', 'Glob'].includes(tool))) throw new Error('non read-only tool allowed');
if (!agent.disallowedTools.includes('Agent')) throw new Error('subagent can spawn subagents');
if ('hooks' in agent || 'mcpServers' in agent || 'permissionMode' in agent) throw new Error('plugin-unsupported subagent fields leaked');
if (!agent.prompt.includes('fresh isolated context')) throw new Error('prompt does not state official context boundary');
if (!agent.prompt.includes('Do not invoke Agent')) throw new Error('prompt does not deny nested Agent use');
console.log(JSON.stringify(agent));
"""
    result = subprocess.run([NODE, "--input-type=module", "-e", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_sdk_backend_receives_quality_resolved_model_and_effort(tmp_path):
    sdk_entry, capture = write_fake_claude_sdk(tmp_path, stdout="SDK_OK")
    result, _prompt, _argv = run_fake_claude_review(
        tmp_path,
        ["--backend", "sdk", "--quality", "strong", "--scope", "working-tree"],
        extra_env={"CLAUDE_FOR_CODEX_SDK_MODULE": str(sdk_entry)},
    )

    assert result.returncode == 0, result.stderr
    query = json.loads((capture / "query.json").read_text())
    assert query["model"] == "opus"
    assert query["effort"] == "xhigh"


def test_sdk_subagent_review_passes_read_only_agent_definitions(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    sdk_module, capture = write_fake_claude_sdk(
        tmp_path,
        stdout=json.dumps({
            "role_results": [
                {"role": "correctness", "status": "success", "text": "correctness ok"},
                {"role": "security", "status": "success", "text": "security ok"},
            ]
        }),
    )

    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness,security",
            "--backend",
            "sdk",
            "--quality",
            "strong",
            "--scope",
            "working-tree",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "execution mode: sdk-subagents" in result.stdout
    assert "correctness ok" in result.stdout
    assert "security ok" in result.stdout

    query = json.loads((capture / "query.json").read_text(encoding="utf8"))
    assert "Agent" in query["allowedTools"]
    assert "Agent" in query["options"]["allowedTools"]
    assert query["options"]["settingSources"] == []
    assert query["options"]["skills"] == []
    assert query["options"]["hooks"] == {}
    assert query["options"]["plugins"] == []
    assert query["options"]["persistSession"] is False
    assert query["options"]["env"]["CLAUDE_FOR_CODEX_ISOLATED_REVIEW"] == "1"
    assert query["options"]["env"]["HOME"] == os.environ["HOME"]
    assert query["options"]["env"]["PATH"] == env["PATH"]
    assert query["options"]["env"].get("CLAUDE_CONFIG_DIR") == os.environ.get("CLAUDE_CONFIG_DIR")
    assert set(["Read", "Grep", "Glob"]).issubset(set(query["allowedTools"]))
    assert set(["Edit", "Write", "MultiEdit", "Bash"]).issubset(set(query["disallowedTools"]))
    assert "claude-for-codex-git" in query["mcpServers"]
    assert "Invoke every listed role agent exactly once" in query["prompt"]
    assert "outputFormat" not in query

    agents = query["agents"]
    assert set(agents) == {"cfc_correctness", "cfc_security"}
    assert query["options"]["agents"] == agents
    for definition in agents.values():
        assert definition["tools"] == ["Read", "Grep", "Glob"]
        assert "permissionMode" not in definition
        assert "hooks" not in definition
        assert "mcpServers" not in definition
        assert definition["maxTurns"] == 4
        assert definition["model"] == "opus"
        assert definition["effort"] == query["effort"]
        assert "fresh isolated context" in definition["prompt"]
        assert "Do not invoke Agent" in definition["prompt"]
        assert set(["Edit", "Write", "MultiEdit", "Bash", "Agent"]).issubset(
            set(definition["disallowedTools"])
        )
        assert "Agent" not in definition["tools"]
        assert not {"Edit", "Write", "MultiEdit", "Bash", "Agent"} & set(definition["tools"])

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
    assert report["nativeOrchestration"] == {
        "enabled": True,
        "mode": "sdk-subagents",
        "roleCount": 2,
    }
    serialized = json.dumps(report)
    assert "sdk-session-secret" not in serialized
    assert "correctness ok" not in serialized
    assert "security ok" not in serialized


def test_sdk_subagents_receive_quality_resolved_opus_model(tmp_path):
    structured = {
        "role_results": [
            {
                "agent": "cfc_correctness",
                "role": "correctness",
                "result": {
                    "status": "ok",
                    "review": structured_review_payload("correctness ok"),
                },
            },
            {
                "agent": "cfc_security",
                "role": "security",
                "result": {
                    "status": "ok",
                    "review": structured_review_payload("security ok"),
                },
            },
        ]
    }
    sdk_entry, capture = write_fake_claude_sdk(tmp_path, stdout=json.dumps(structured), structured_output=structured)
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "sample.txt").write_text("base\nchange\n")
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_entry)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--backend",
            "sdk",
            "--agent-team",
            "sdk-subagents",
            "--native-structured",
            "--json",
            "--roles",
            "correctness,security",
            "--quality",
            "strong",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    query = json.loads((capture / "query.json").read_text())
    assert query["model"] == "opus"
    assert query["effort"] == "xhigh"
    agents = query["agents"]
    assert agents["cfc_correctness"]["model"] == "opus"
    assert agents["cfc_security"]["model"] == "opus"
    assert agents["cfc_correctness"]["effort"] == "xhigh"
    assert agents["cfc_security"]["effort"] == "xhigh"


def test_sdk_subagents_receive_fable_for_max_quality(tmp_path):
    structured = {
        "role_results": [
            {
                "agent": "cfc_correctness",
                "role": "correctness",
                "result": {
                    "status": "ok",
                    "review": structured_review_payload("correctness ok"),
                },
            },
            {
                "agent": "cfc_security",
                "role": "security",
                "result": {
                    "status": "ok",
                    "review": structured_review_payload("security ok"),
                },
            },
        ]
    }
    sdk_entry, capture = write_fake_claude_sdk(tmp_path, stdout=json.dumps(structured), structured_output=structured)
    fake_claude = write_fake_claude_cli(
        tmp_path,
        help_text="--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_entry)
    env["CLAUDE_FOR_CODEX_TOP_MODEL"] = "fable"
    env["CLAUDE_CODE_PATH"] = str(fake_claude)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--backend",
            "sdk",
            "--agent-team",
            "sdk-subagents",
            "--native-structured",
            "--json",
            "--roles",
            "correctness,security",
            "--quality",
            "max",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    query = json.loads((capture / "query.json").read_text())
    assert query["model"] == "fable"
    assert query["agents"]["cfc_correctness"]["model"] == "fable"
    assert query["agents"]["cfc_security"]["model"] == "fable"
    assert query["agents"]["cfc_correctness"]["effort"] == "max"
    assert query["agents"]["cfc_security"]["effort"] == "max"
    assert "--fallback-model" not in json.dumps(query)


def test_sdk_native_agents_fall_back_to_inherit_for_custom_and_default_models():
    module_uri = (PLUGIN / "scripts" / "lib" / "claude-native-review.mjs").as_uri()
    code = f"""
import {{ buildNativeReviewAgents }} from {json.dumps(module_uri)};
const custom = buildNativeReviewAgents(['correctness'], {{ model: 'gpt-4' }});
const defaultModel = buildNativeReviewAgents(['correctness'], {{ model: 'default' }});
const modelId = buildNativeReviewAgents(['correctness'], {{ model: 'claude-opus-latest' }});
if (custom.cfc_correctness.model !== 'inherit') throw new Error('custom should inherit: ' + custom.cfc_correctness.model);
if (defaultModel.cfc_correctness.model !== 'inherit') throw new Error('default should inherit: ' + defaultModel.cfc_correctness.model);
if (modelId.cfc_correctness.model !== 'claude-opus-latest') throw new Error('model id should be preserved: ' + modelId.cfc_correctness.model);
console.log(JSON.stringify({{ custom, defaultModel, modelId }}));
"""
    result = subprocess.run([NODE, "--input-type=module", "-e", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_sdk_subagents_quality_max_does_not_inherit_cli_top_alias_without_env(tmp_path):
    structured = {
        "role_results": [
            {
                "agent": "cfc_correctness",
                "role": "correctness",
                "result": {
                    "status": "ok",
                    "review": structured_review_payload("correctness ok"),
                },
            },
        ]
    }
    sdk_entry, capture = write_fake_claude_sdk(tmp_path, stdout=json.dumps(structured), structured_output=structured)
    fake_claude = write_fake_claude_cli(
        tmp_path,
        help_text="--model <model> alias fable opus sonnet --fallback-model <model> accepts a comma-separated list",
    )
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_entry)
    env["CLAUDE_CODE_PATH"] = str(fake_claude)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--backend",
            "sdk",
            "--agent-team",
            "sdk-subagents",
            "--native-structured",
            "--json",
            "--roles",
            "correctness",
            "--quality",
            "max",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    query = json.loads((capture / "query.json").read_text())
    assert query["model"] == "opus"
    assert query["agents"]["cfc_correctness"]["model"] == "opus"
    assert query["agents"]["cfc_correctness"]["effort"] == "max"
    assert "--fallback-model" not in json.dumps(query)


def test_sdk_subagents_preserve_full_fable_model_id(tmp_path):
    structured = {
        "role_results": [
            {
                "agent": "cfc_correctness",
                "role": "correctness",
                "result": {
                    "status": "ok",
                    "review": structured_review_payload("ok"),
                },
            },
        ]
    }
    sdk_entry, capture = write_fake_claude_sdk(tmp_path, stdout=json.dumps(structured), structured_output=structured)
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_entry)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--backend",
            "sdk",
            "--agent-team",
            "sdk-subagents",
            "--native-structured",
            "--json",
            "--roles",
            "correctness",
            "--model",
            "claude-fable-5",
            "--effort",
            "max",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    query = json.loads((capture / "query.json").read_text())
    assert query["model"] == "claude-fable-5"
    assert query["agents"]["cfc_correctness"]["model"] == "claude-fable-5"
    assert query["agents"]["cfc_correctness"]["effort"] == "max"


def test_sdk_native_structured_output_passes_schema(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    sdk_module, capture = write_fake_claude_sdk(
        tmp_path,
        stdout=json.dumps({
            "role_results": [
                {
                    "agent": "cfc_correctness",
                    "role": "correctness",
                    "result": {
                        "status": "ok",
                        "review": structured_review_payload("schema structured ok"),
                        "error": "",
                    },
                },
            ]
        }),
    )
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness",
            "--backend",
            "sdk",
            "--json",
            "--native-structured",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    query = json.loads((capture / "query.json").read_text(encoding="utf8"))
    output_format = query["options"]["outputFormat"]
    assert query["outputFormat"] == output_format
    assert output_format["type"] == "json_schema"
    schema = output_format["schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["role_results"]
    assert schema["properties"]["role_results"]["type"] == "array"
    item_schema = schema["properties"]["role_results"]["items"]
    assert "agent" in item_schema["properties"]
    assert item_schema["properties"]["agent"]["type"] == "string"
    assert set(item_schema["required"]) == {"role", "result"}
    result_schema = item_schema["properties"]["result"]
    assert set(result_schema["required"]) == {"status"}
    assert {"ok", "failed"} == set(result_schema["properties"]["status"]["enum"])
    review_schema = result_schema["properties"]["review"]
    assert review_schema["type"] == "object"
    assert set(review_schema["required"]) == {"verdict", "summary", "findings", "next_steps"}
    assert review_schema["properties"]["verdict"]["enum"] == ["approve", "needs-attention"]
    assert "text" not in result_schema["properties"]
    assert "result.review" in query["prompt"]
    assert "Each result.review must be the exact JSON object" in query["prompt"]
    assert "Return exactly one JSON object and no Markdown" in query["agents"]["cfc_correctness"]["prompt"]
    assert '"verdict": "approve | needs-attention"' in query["agents"]["cfc_correctness"]["prompt"]


def test_sdk_native_structured_output_prefers_sdk_result_metadata(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    structured = {
        "role_results": [
            {
                "agent": "cfc_correctness",
                "role": "correctness",
                "result": {
                    "status": "ok",
                    "review": structured_review_payload(
                        "metadata structured ok",
                        verdict="needs-attention",
                        findings=[structured_review_finding_payload()],
                    ),
                    "error": "",
                },
            },
        ]
    }
    sdk_module, _capture = write_fake_claude_sdk(
        tmp_path,
        stdout="human-readable non-json transcript",
        structured_output=structured,
    )
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness",
            "--backend",
            "sdk",
            "--json",
            "--native-structured",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "needs-attention"
    assert payload["findings"][0]["role"] == "correctness"
    assert payload["findings"][0]["title"] == "Regression title"
    assert "metadata structured ok" in payload["summary"]
    latest = subprocess.run(
        [NODE, str(runtime), "report", "--latest"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert latest.returncode == 0, latest.stderr
    serialized = json.dumps(json.loads(latest.stdout)["report"])
    assert "structuredOutput" not in serialized
    assert "metadata structured ok" not in serialized
    assert "structuredReview" not in serialized
    assert "role_results" not in serialized
    report = json.loads(latest.stdout)["report"]
    assert report["structured"]["verdict"] == "needs-attention"
    assert report["roleResults"][0]["structured"]["verdict"] == "needs-attention"


def test_sdk_subagent_role_coverage_mismatch_is_reported(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    sdk_module, _capture = write_fake_claude_sdk(
        tmp_path,
        stdout=json.dumps({
            "role_results": [
                {"role": "correctness", "status": "ok", "text": "correctness ok"},
            ]
        }),
    )
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness,security",
            "--backend",
            "sdk",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "SDK subagent role coverage mismatch" in result.stderr
    assert "missing=security" in result.stderr
    latest = subprocess.run(
        [NODE, str(runtime), "report", "--latest"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert latest.returncode == 0, latest.stderr
    report = json.loads(latest.stdout)["report"]
    assert report["errorCode"] == "SDK_SUBAGENT_ROLE_COVERAGE"


def test_sdk_subagent_role_coverage_allows_reordered_results(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    sdk_module, _capture = write_fake_claude_sdk(
        tmp_path,
        stdout=json.dumps({
            "role_results": [
                {"role": "security", "status": "ok", "text": "security ok"},
                {"role": "correctness", "status": "ok", "text": "correctness ok"},
            ]
        }),
    )
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness,security",
            "--backend",
            "sdk",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.index("Role: correctness") < result.stdout.index("Role: security")
    assert "correctness ok" in result.stdout
    assert "security ok" in result.stdout


def test_sdk_native_structured_role_parse_failure_report_backend_is_sdk(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    sdk_module, _capture = write_fake_claude_sdk(
        tmp_path,
        stdout=json.dumps({
            "role_results": [
                {"role": "correctness", "status": "ok", "text": "plain text is not review JSON"},
            ]
        }),
    )
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness",
            "--backend",
            "sdk",
            "--json",
            "--native-structured",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Invalid SDK subagent JSON output" in result.stderr
    assert "role_results[].result.review" in result.stderr
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
    assert report["errorCode"] == "SDK_SUBAGENT_INVALID_JSON"
    assert report["nativeOrchestration"]["enabled"] is True
    assert report["roleResults"] == []


def test_sdk_native_structured_rejects_plain_text_role_result_with_actionable_error(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    sdk_module, _capture = write_fake_claude_sdk(
        tmp_path,
        structured_output={
            "role_results": [
                {
                    "agent": "cfc_correctness",
                    "role": "correctness",
                    "result": {
                        "status": "ok",
                        "text": "plain language review instead of structured JSON",
                        "error": "",
                    },
                },
            ]
        },
    )
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness",
            "--backend",
            "sdk",
            "--json",
            "--native-structured",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Invalid SDK subagent JSON output" in result.stderr
    assert "role_results[].result.review" in result.stderr
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
    assert report["errorCode"] == "SDK_SUBAGENT_INVALID_JSON"


def test_sdk_native_structured_preserves_failed_role_result_in_structured_mode(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    sdk_module, _capture = write_fake_claude_sdk(
        tmp_path,
        structured_output={
            "role_results": [
                {
                    "agent": "cfc_correctness",
                    "role": "correctness",
                    "result": {
                        "status": "failed",
                        "error": "rate limit hit",
                    },
                },
            ]
        },
    )
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness",
            "--backend",
            "sdk",
            "--json",
            "--native-structured",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "correctness: Claude exited 1: rate limit hit" in result.stderr
    assert "SDK_SUBAGENT_INVALID_JSON" not in result.stderr
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
    assert report["errorCode"] != "SDK_SUBAGENT_INVALID_JSON"
    assert report["roleResults"][0]["exitStatus"] == 1


def test_sdk_stream_progress_is_sanitized_and_does_not_print_raw_chunks(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    secret_chunk = "raw-secret-model-chunk"
    secret_session = "sdk-session-secret"
    sdk_module, capture = write_fake_claude_sdk(
        tmp_path,
        stdout=json.dumps({
            "role_results": [
                {"role": "correctness", "status": "ok", "text": "stream ok"},
            ]
        }),
        extra_js=f"""
  yield {{
    type: 'assistant',
    message: {{ content: [{{ type: 'text', text: {json.dumps(secret_chunk)} }}] }},
    session_id: {json.dumps(secret_session)}
  }};
""",
    )
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness",
            "--backend",
            "sdk",
            "--stream-progress",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "[claude-for-codex sdk progress] assistant" in result.stderr
    assert "[claude-for-codex sdk progress] result cost_usd=0.01" in result.stderr
    assert "[claude-for-codex progress]" in result.stderr
    assert secret_chunk not in result.stderr
    assert secret_session not in result.stderr
    assert secret_chunk not in result.stdout
    assert secret_session not in result.stdout
    query = json.loads((capture / "query.json").read_text(encoding="utf8"))
    assert query["includePartialMessages"] is True
    assert query["options"]["includePartialMessages"] is True
    latest = subprocess.run(
        [NODE, str(runtime), "report", "--latest"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert latest.returncode == 0, latest.stderr
    serialized = json.dumps(json.loads(latest.stdout)["report"])
    assert secret_chunk not in serialized
    assert secret_session not in serialized


def test_sdk_review_stream_progress_is_sanitized_and_report_omits_raw_chunks(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n")
    secret_chunk = "raw-secret-review-chunk"
    secret_session = "sdk-session-secret"
    sdk_module, capture = write_fake_claude_sdk(
        tmp_path,
        stdout="SDK_REVIEW_OK",
        extra_js=f"""
  yield {{
    type: 'assistant',
    message: {{ content: [{{ type: 'text', text: {json.dumps(secret_chunk)} }}] }},
    session_id: {json.dumps(secret_session)}
  }};
""",
    )
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "review",
            "--backend",
            "sdk",
            "--stream-progress",
            "--scope",
            "working-tree",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "[claude-for-codex sdk progress] assistant" in result.stderr
    assert "[claude-for-codex sdk progress] result cost_usd=0.01" in result.stderr
    assert "[claude-for-codex progress]" in result.stderr
    assert "SDK_REVIEW_OK" in result.stdout
    assert secret_chunk not in result.stderr
    assert secret_session not in result.stderr
    assert secret_chunk not in result.stdout
    assert secret_session not in result.stdout
    query = json.loads((capture / "query.json").read_text(encoding="utf8"))
    assert query["includePartialMessages"] is True
    assert query["options"]["includePartialMessages"] is True
    latest = subprocess.run(
        [NODE, str(runtime), "report", "--latest"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert latest.returncode == 0, latest.stderr
    serialized = json.dumps(json.loads(latest.stdout)["report"])
    assert secret_chunk not in serialized
    assert secret_session not in serialized


def test_sdk_background_review_auto_streams_progress_to_job(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n")
    sdk_module, capture = write_fake_claude_sdk(
        tmp_path,
        stdout="SDK_BACKGROUND_OK",
        extra_js="""
  yield {
    type: 'assistant',
    message: { content: [{ type: 'text', text: 'background progress chunk' }] },
    session_id: 'sdk-session-secret'
  };
""",
    )
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "review",
            "--backend",
            "sdk",
            "--background",
            "--wait",
            "--wait-timeout-ms",
            "10000",
            "--scope",
            "working-tree",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["job"]["status"] == "succeeded"
    assert payload["job"]["lastProgressAt"]
    assert payload["job"]["lastProgressMessage"] == "result event received"
    assert payload["job"]["progressPreview"] == ["result event received"]
    assert "SDK_BACKGROUND_OK" in payload["job"]["stdout"]
    query = json.loads((capture / "query.json").read_text(encoding="utf8"))
    assert query["includePartialMessages"] is True
    assert query["options"]["includePartialMessages"] is True


def test_sdk_subagent_mode_parses_nested_role_results_and_failed_status(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    sdk_module, _capture = write_fake_claude_sdk(
        tmp_path,
        stdout=json.dumps({
            "role_results": [
                {
                    "role": "correctness",
                    "result": {
                        "status": "success",
                        "text": "correctness nested ok",
                    },
                },
                {
                    "role": "security",
                    "result": {
                        "status": "failed",
                        "text": "security nested finding",
                        "error": "security nested error",
                    },
                },
            ]
        }),
    )
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness,security",
            "--backend",
            "sdk",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "correctness nested ok" in result.stdout
    assert "security nested finding" in result.stdout
    assert "security nested error" in result.stdout
    assert "security" in result.stdout
    assert "Role failed with exit status 1." in result.stdout


def test_sdk_subagent_mode_requires_sdk_backend(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "requires --backend sdk" in result.stderr


def test_sdk_subagent_mode_rejects_invalid_json_output(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    sdk_module, _capture = write_fake_claude_sdk(tmp_path, stdout="not json")
    env = os.environ.copy()
    env["CLAUDE_FOR_CODEX_SDK_MODULE"] = str(sdk_module)

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "multi-review",
            "--agent-team",
            "sdk-subagents",
            "--roles",
            "correctness",
            "--backend",
            "sdk",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Invalid SDK subagent JSON output" in result.stderr


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
    assert report["outcome"]["kind"] == "success"
    assert report["outcome"]["ok"] is True
    serialized = json.dumps(report)
    assert "sdk-session-secret" not in serialized
    assert "SDK_REVIEW_OK" not in serialized
    assert "sdkEvents" not in serialized


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


def test_repository_has_fork_safe_claude_for_codex_ci_workflow():
    workflow = ROOT / ".github" / "workflows" / "claude-for-codex-ci.yml"
    assert workflow.exists()
    text = workflow.read_text(encoding="utf8")
    assert "pull_request_target" not in text
    assert "pull_request:" in text
    assert "push:" in text
    assert "permissions:" in text
    assert "contents: read" in text
    assert "node --check plugins/claude-for-codex/scripts/claude-companion.mjs" in text
    assert "node --check plugins/claude-for-codex/scripts/lib/hook-compat.mjs" in text
    assert "node --check plugins/claude-for-codex/scripts/lib/doctor.mjs" in text
    assert "pytest -q tests/test_claude_permission_compat.py" in text
    assert 'pytest -q tests/test_claude_for_codex_plugin.py -k "' in text
    assert "outcome_classifier" in text
    assert "doctor_json" in text
    assert "hook_compat" in text
    assert "pytest -q tests/test_claude_for_codex_plugin.py tests/test_claude_permission_compat.py" not in text
    assert "release-check --ci-simulate --json" in text
    assert "git diff --check" in text


def test_release_check_knows_claude_0160_native_assets():
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
    assert checks["manifest-version"]["detail"] == "version=0.18.1"
    assert "claude-ultrareview" in checks["skill-inventory"]["detail"]
    detail = " ".join(check.get("detail", "") for check in payload["checks"])
    assert "claude-ultrareview" in detail
    assert "native assets/docs" in detail
    assert "--agent-team sdk-subagents" in detail
    assert "--confirm-cost" in detail
    assert "@anthropic-ai/claude-agent-sdk" in detail
    assert checks["manifest-defaultPrompt-limit"]["ok"] is True
    assert checks["manifest-asset-composerIcon"]["ok"] is True
    assert checks["manifest-asset-logo"]["ok"] is True
    assert checks["manifest-asset-screenshots.0"]["ok"] is True
    assert checks["manifest-asset-screenshots.1"]["ok"] is True
    assert checks["hook-compat-report"]["ok"] is True
    assert checks["doctor-command"]["ok"] is True
    assert checks["native-review-helper"]["ok"] is True
    assert checks["ultrareview-skill"]["ok"] is True
    assert checks["native-cli-flags"]["ok"] is True
    assert checks["native-sdk-package-compat"]["ok"] is True
    assert checks["native-docs"]["ok"] is True
    assert checks["native-maturity-docs"]["ok"] is True
    assert checks["outcome-success-stdout-not-failure"]["ok"] is True
    assert checks["model-registry-default-and-env-fallback"]["ok"] is True
    assert checks["native-sdk-explicit-opt-in"]["ok"] is True
    assert checks["native-sdk-explicit-opt-in"]["detail"] == "--backend sdk --agent-team sdk-subagents"
    assert checks["native-default-cli-preserved"]["ok"] is True
    assert "default backend=cli" in checks["native-default-cli-preserved"]["detail"]
    assert checks["ultrareview-cost-consent"]["ok"] is True
    assert "--confirm-cost" in checks["ultrareview-cost-consent"]["detail"]
    assert checks["ultrareview-not-hook-default"]["ok"] is True
    assert "generated workflow calls review" in checks["ultrareview-not-hook-default"]["detail"]
    assert checks["read-only-cli-isolation"]["ok"] is True
    assert checks["read-only-sdk-isolation"]["ok"] is True
    assert checks["read-only-no-config-dir-relocation"]["ok"] is True
    assert checks["read-only-sdk-structured-output"]["ok"] is True
    assert checks["sdk-native-structured-review-contract"]["ok"] is True
    assert checks["skills-natural-language-routing"]["ok"] is True


def test_release_check_knows_subagent_review_skill():
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    result = subprocess.run(
        [NODE, str(runtime), "release-check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    assert "claude-subagent-review" in checks["skill-inventory"]["detail"]
    assert checks["skill-claude-subagent-review"]["ok"] is True
    assert checks["subagent-review-docs"]["ok"] is True


def test_release_check_natural_language_routing_passes_for_installed_plugin_only(tmp_path):
    installed = tmp_path / "claude-for-codex"
    shutil.copytree(PLUGIN, installed)

    result = subprocess.run(
        [NODE, str(installed / "scripts" / "claude-companion.mjs"), "release-check", "--ci-simulate"],
        cwd=installed,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    checks = {item["name"]: item for item in payload["checks"]}
    assert checks["skills-natural-language-routing"]["ok"] is True


def test_release_check_rejects_repo_root_subagent_example_late_in_section(tmp_path):
    temp_root = tmp_path / "repo"
    temp_plugin = temp_root / "plugins" / "claude-for-codex"
    temp_root.mkdir()
    shutil.copy2(ROOT / "README.md", temp_root / "README.md")
    shutil.copytree(ROOT / "docs", temp_root / "docs")
    temp_plugin.parent.mkdir(parents=True)
    shutil.copytree(PLUGIN, temp_plugin)
    readme = temp_plugin / "README.md"
    text = readme.read_text(encoding="utf8")
    marker = 'node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command rescue "$ARGUMENTS"'
    assert marker in text
    readme.write_text(
        text.replace(
            marker,
            f"{marker}\nnode plugins/claude-for-codex/scripts/claude-companion.mjs subagent-command review \"$ARGUMENTS\"",
        ),
        encoding="utf8",
    )

    result = subprocess.run(
        [NODE, str(temp_plugin / "scripts" / "claude-companion.mjs"), "release-check"],
        cwd=temp_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["subagent-review-docs"]["ok"] is False


def test_release_check_config_dir_negative_control_fails():
    release_check_uri = (PLUGIN / "scripts" / "lib" / "release-check.mjs").as_uri()
    result = subprocess.run(
        [
            NODE,
            "--input-type=module",
            "-e",
            f"""
import {{ readOnlyIsolationChecksFromSource }} from {json.dumps(release_check_uri)};
const checks = readOnlyIsolationChecksFromSource({{
  companion: 'const x = 1;\\nprocess.env.CLAUDE_CONFIG_DIR = "/tmp/isolated";',
  backend: 'settingSources: []\\nskills: []\\nhooks: {{}}\\nplugins: []\\npersistSession: false\\nCLAUDE_FOR_CODEX_ISOLATED_REVIEW\\nmetadata.structuredOutput = event.structured_output;'
}});
console.log(JSON.stringify(checks));
""",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    checks = {check["name"]: check for check in json.loads(result.stdout)}
    assert checks["read-only-no-config-dir-relocation"]["ok"] is False


def test_release_check_sdk_native_structured_review_contract_negative_control_fails():
    release_check_uri = (PLUGIN / "scripts" / "lib" / "release-check.mjs").as_uri()
    result = subprocess.run(
        [
            NODE,
            "--input-type=module",
            "-e",
            f"""
import {{ readOnlyIsolationChecksFromSource }} from {json.dumps(release_check_uri)};
const checks = readOnlyIsolationChecksFromSource({{
  companion: 'aggregate.metadata?.structuredOutput',
  backend: 'settingSources: []\\nskills: []\\nhooks: {{}}\\nplugins: []\\npersistSession: false\\nCLAUDE_FOR_CODEX_ISOLATED_REVIEW\\nmetadata.structuredOutput = event.structured_output;'
}});
console.log(JSON.stringify(checks));
""",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    checks = {check["name"]: check for check in json.loads(result.stdout)}
    assert checks["sdk-native-structured-review-contract"]["ok"] is False


def test_release_check_remote_install_uses_requested_immutable_ref(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    fake_bin = tmp_path / "bin"
    log = tmp_path / "codex-calls.jsonl"
    installed_plugin = tmp_path / "installed" / "claude-for-codex"
    installed_runtime = installed_plugin / "scripts" / "claude-companion.mjs"
    fake_bin.mkdir()
    installed_runtime.parent.mkdir(parents=True)
    installed_runtime.write_text("#!/usr/bin/env node\n", encoding="utf8")
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
if sys.argv[1:] == ["plugin", "list", "--json"]:
    print(json.dumps({{
        "installed": [
            {{
                "pluginId": "claude-for-codex@external-models-for-codex",
                "name": "claude-for-codex",
                "marketplaceName": "external-models-for-codex",
                "source": {{"source": "local", "path": {json.dumps(str(installed_plugin))}}},
            }}
        ],
        "available": [],
    }}))
    raise SystemExit(0)
raise SystemExit(1)
""",
        encoding="utf8",
    )
    fake_codex.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "release-check", "--remote-install", "--ref", "claude-for-codex-v0.18.1"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text(encoding="utf8").splitlines()]
    assert ["plugin", "marketplace", "add", "yilibinbin/external-models-for-codex", "--ref", "claude-for-codex-v0.18.1"] in calls
    assert ["plugin", "list", "--json"] in calls
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["remote-install-smoke"]["detail"] == "installed ref=claude-for-codex-v0.18.1"
    assert checks["remote-install-plugin-list-schema"]["ok"] is True
    assert checks["remote-install-plugin-list-schema"]["detail"] == f"installed root={installed_plugin}"


def test_release_check_required_remote_install_fails_without_installed_source_path(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import sys

if sys.argv[1:] == ["--version"]:
    print("codex fake")
    raise SystemExit(0)
if sys.argv[1:3] == ["plugin", "marketplace"] and "--ref" in sys.argv:
    raise SystemExit(0)
if sys.argv[1:3] == ["plugin", "add"]:
    raise SystemExit(0)
if sys.argv[1:] == ["plugin", "list", "--json"]:
    print(json.dumps({
        "installed": [
            {
                "pluginId": "claude-for-codex@external-models-for-codex",
                "name": "claude-for-codex",
                "marketplaceName": "external-models-for-codex",
                "source": {"source": "git"},
            }
        ],
        "available": [],
    }))
    raise SystemExit(0)
raise SystemExit(1)
""",
        encoding="utf8",
    )
    fake_codex.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "release-check", "--require-remote-install", "--ref", "claude-for-codex-v0.18.1"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["remote-install-smoke"]["ok"] is True
    assert checks["remote-install-plugin-list-schema"]["ok"] is False
    assert checks["remote-install-plugin-list-schema"]["detail"] == "installed Claude plugin missing source.path"


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
    assert "codex plugin marketplace add yilibinbin/external-models-for-codex --ref claude-for-codex-v0.18.1" in text
    assert "codex plugin add claude-for-codex@external-models-for-codex" in text
    assert "codex plugin list --json" in text
    assert "CLAUDE_PLUGIN_ROOT=$CLAUDE_PLUGIN_ROOT" in text
    assert 'node "$CLAUDE_PLUGIN_ROOT/scripts/claude-companion.mjs"' in text
    assert "node plugins/claude-for-codex/scripts/" not in text
    assert "github.event.pull_request.base.sha" in text
    assert "fetch-depth: 0" in text
    assert "retention-days: 5" in text
    assert "github.*" not in text
    assert "/Users/fanghao" not in text
    assert 'claude-companion.mjs" review --json --quality standard --scope branch' in text
    assert "ultrareview" not in text
    assert "--quality max" not in text

    run_blocks = re.findall(r"run: \|\n((?:        .+\n)+)", text)
    assert run_blocks
    assert all("${{ github." not in block for block in run_blocks)
    assert "$BASE_SHA" in text
    assert '"$BASE_SHA"' in text
    assert "$HEAD_REPO" in text
    assert "$BASE_REPO" in text


def extract_claude_plugin_root_resolver_script(workflow_text):
    match = re.search(
        r"codex plugin list --json \| node -e '\n(?P<script>.*?)\n\s*'\n\s*\)",
        workflow_text,
        re.S,
    )
    assert match, "plugin root resolver script missing from rendered workflow"
    return match.group("script")


def test_github_actions_plugin_root_resolver_matches_codex_list_json_shape(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()

    rendered = subprocess.run(
        [NODE, str(runtime), "github-actions", "render"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert rendered.returncode == 0, rendered.stderr
    script = extract_claude_plugin_root_resolver_script(rendered.stdout)
    payload = {
        "installed": [
            {
                "pluginId": "claude-for-codex@external-models-for-codex",
                "name": "claude-for-codex",
                "marketplaceName": "external-models-for-codex",
                "source": {"path": "/tmp/codex/plugins/claude-for-codex"},
            }
        ]
    }
    result = subprocess.run(
        [NODE, "-e", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "/tmp/codex/plugins/claude-for-codex"


def test_github_actions_plugin_root_resolver_fails_without_source_path(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()

    rendered = subprocess.run(
        [NODE, str(runtime), "github-actions", "render"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert rendered.returncode == 0, rendered.stderr
    script = extract_claude_plugin_root_resolver_script(rendered.stdout)
    payload = {
        "installed": [
            {
                "pluginId": "claude-for-codex@external-models-for-codex",
                "name": "claude-for-codex",
                "marketplaceName": "external-models-for-codex",
                "source": {"source": "git"},
            }
        ]
    }
    result = subprocess.run(
        [NODE, "-e", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == ""


def test_github_actions_explicit_model_and_effort_are_forwarded_to_review_command(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()

    result = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--model", "opus", "--effort", "max"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    text = result.stdout
    assert 'CLAUDE_FOR_CODEX_MODEL: "opus"' in text
    assert 'CLAUDE_FOR_CODEX_EFFORT: "max"' in text
    assert "CLAUDE_MODEL:" not in text
    assert "CLAUDE_EFFORT:" not in text
    assert "MODEL_ARGS=()" in text
    assert 'MODEL_ARGS+=(--model "$CLAUDE_FOR_CODEX_MODEL")' in text
    assert 'MODEL_ARGS+=(--effort "$CLAUDE_FOR_CODEX_EFFORT")' in text
    assert '${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}' in text


def test_github_actions_explicit_fable_model_is_forwarded(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()

    result = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--model", "fable", "--effort", "max"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    text = result.stdout
    assert 'CLAUDE_FOR_CODEX_MODEL: "fable"' in text
    assert 'CLAUDE_FOR_CODEX_EFFORT: "max"' in text
    assert 'MODEL_ARGS+=(--model "$CLAUDE_FOR_CODEX_MODEL")' in text
    assert 'MODEL_ARGS+=(--effort "$CLAUDE_FOR_CODEX_EFFORT")' in text


def test_github_actions_default_keeps_model_and_effort_empty_but_wired(tmp_path):
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
    text = result.stdout
    assert 'CLAUDE_FOR_CODEX_MODEL: ""' in text
    assert 'CLAUDE_FOR_CODEX_EFFORT: ""' in text
    assert "CLAUDE_MODEL:" not in text
    assert "CLAUDE_EFFORT:" not in text
    assert "--quality standard" in text
    assert "--quality max" not in text
    assert 'MODEL_ARGS+=(--model "$CLAUDE_FOR_CODEX_MODEL")' in text
    assert 'MODEL_ARGS+=(--effort "$CLAUDE_FOR_CODEX_EFFORT")' in text
    assert '${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}' in text


def test_github_actions_model_args_empty_array_is_nounset_safe(tmp_path):
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
    assert '${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}' in result.stdout

    empty = subprocess.run(
        [
            "/bin/bash",
            "-uc",
            'MODEL_ARGS=(); argv=(node runtime --base "$BASE_SHA" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}) ; printf "%s\\n" "${argv[@]}"',
        ],
        env={**os.environ, "BASE_SHA": "base-sha"},
        capture_output=True,
        text=True,
    )
    assert empty.returncode == 0, empty.stderr
    assert empty.stdout.splitlines() == ["node", "runtime", "--base", "base-sha"]

    non_empty = subprocess.run(
        [
            "/bin/bash",
            "-uc",
            'MODEL_ARGS=(--model opus --effort max); argv=(node runtime --base "$BASE_SHA" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}) ; printf "%s\\n" "${argv[@]}"',
        ],
        env={**os.environ, "BASE_SHA": "base-sha"},
        capture_output=True,
        text=True,
    )
    assert non_empty.returncode == 0, non_empty.stderr
    assert non_empty.stdout.splitlines() == ["node", "runtime", "--base", "base-sha", "--model", "opus", "--effort", "max"]


def test_github_actions_rejects_invalid_effort_before_render(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()

    result = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--effort", "ultracode"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert 'Invalid --effort "ultracode"' in result.stderr


@pytest.mark.parametrize("model", ["-inject", "opus\nbad"])
def test_github_actions_rejects_invalid_model_before_render(tmp_path, model):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()

    result = subprocess.run(
        [NODE, str(runtime), "github-actions", "render", "--model", model],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Invalid --model value" in result.stderr


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
    assert "claude-for-codex-v0.18.1" in workflow.read_text(encoding="utf8")

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
        rendered.stdout.replace("--ref claude-for-codex-v0.18.1", "--ref main") + "\n# /Users/fanghao/leak\n",
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
    assert checks["github-actions-plugin-root-resolved"]["ok"] is True
    assert checks["github-actions-no-repo-relative-runtime-path"]["ok"] is True
    assert checks["github-actions-current-release-ref"]["ok"] is True
    assert checks["github-actions-model-effort-array"]["ok"] is True
    assert checks["github-actions-model-env-forwarded"]["ok"] is True
    assert checks["github-actions-effort-env-forwarded"]["ok"] is True
    assert checks["github-actions-model-effort-quoted"]["ok"] is True
    assert checks["github-actions-ci-present"]["ok"] is True
    assert checks["github-actions-ci-fork-safe"]["ok"] is True
    assert checks["github-actions-ci-minimal-permissions"]["ok"] is True
    assert checks["github-actions-ci-node-check"]["ok"] is True
    assert checks["github-actions-ci-pytest"]["ok"] is True
    assert checks["github-actions-ci-release-check"]["ok"] is True
    assert checks["github-actions-ci-whitespace"]["ok"] is True
    assert checks["quality-policy-assets"]["ok"] is True
    assert checks["quality-top-model-policy"]["ok"] is True
    assert checks["quality-no-concrete-model-defaults"]["ok"] is True
    assert checks["github-actions-current-release-ref"]["detail"] == "claude-for-codex-v0.18.1"


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


def test_result_marks_terminal_viewed_but_preserves_unread_running_jobs(tmp_path):
    import datetime

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
    terminal_job_file = jobs_dir / "job-terminal.json"
    terminal_job_file.write_text(json.dumps({
        "id": "job-terminal",
        "status": "succeeded",
        "createdAt": "2026-05-30T00:00:00.000Z",
        "result": "ready"
    }))

    result = subprocess.run(
        [NODE, str(runtime), "result", "job-terminal"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result_payload = json.loads(result.stdout)
    assert result_payload["status"] == "ok"
    assert result_payload["job"]["resultViewedAt"]
    assert json.loads(terminal_job_file.read_text())["resultViewedAt"]

    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    running_job_file = jobs_dir / "job-running.json"
    running_job_file.write_text(json.dumps({
        "id": "job-running",
        "status": "running",
        "createdAt": now,
        "startedAt": now,
        "lastHeartbeatAt": now
    }))

    running_result = subprocess.run(
        [NODE, str(runtime), "result", "job-running"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert running_result.returncode == 0, running_result.stderr
    running_payload = json.loads(running_result.stdout)
    assert running_payload["status"] == "ok"
    assert "resultViewedAt" not in running_payload["job"]
    assert "resultViewedAt" not in json.loads(running_job_file.read_text())

    finished_running_job = json.loads(running_job_file.read_text())
    finished_running_job.update({
        "status": "succeeded",
        "finishedAt": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "stdout": "done"
    })
    running_job_file.write_text(json.dumps(finished_running_job))
    prompt_hook = PLUGIN / "hooks" / "unread-result.mjs"
    prompted = subprocess.run(
        [NODE, str(prompt_hook)],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(repo)}),
        capture_output=True,
        text=True,
    )
    assert prompted.returncode == 0
    assert "job-running (succeeded)" in prompted.stderr


def test_result_and_cancel_accept_job_id_option_form(tmp_path):
    import datetime

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
    jobs_dir = pathlib.Path(json.loads(jobs.stdout)["stateDir"]) / "jobs"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    job_file = jobs_dir / "job-option.json"
    job_file.write_text(json.dumps({
        "id": "job-option",
        "status": "queued",
        "createdAt": now,
        "lastHeartbeatAt": now
    }))

    result = subprocess.run(
        [NODE, str(runtime), "result", "--json", "--job-id", "job-option"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["job"]["id"] == "job-option"

    cancel = subprocess.run(
        [NODE, str(runtime), "cancel", "--json", "--job-id", "job-option"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert cancel.returncode == 0, cancel.stderr
    assert json.loads(cancel.stdout)["status"] == "cancelled"


def test_cancel_persists_queued_job_state_after_result_peek(tmp_path):
    import datetime

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
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    job_file = jobs_dir / "job-queued.json"
    job_file.write_text(json.dumps({
        "id": "job-queued",
        "status": "queued",
        "createdAt": now,
        "lastHeartbeatAt": now
    }))

    result = subprocess.run(
        [NODE, str(runtime), "result", "job-queued"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result_payload = json.loads(result.stdout)
    assert result_payload["status"] == "ok"
    assert "resultViewedAt" not in result_payload["job"]
    assert "resultViewedAt" not in json.loads(job_file.read_text())

    cancel = subprocess.run(
        [NODE, str(runtime), "cancel", "job-queued"],
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


def test_cancel_queued_job_with_unvalidated_worker_pid_fails(tmp_path):
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
    job_file = jobs_dir / "job-queued-worker.json"
    job_file.write_text(json.dumps({
        "id": "job-queued-worker",
        "status": "queued",
        "command": "review",
        "args": ["queued"],
        "cwd": str(repo),
        "createdAt": "2026-05-30T00:00:00.000Z",
        "workerPid": os.getpid(),
    }))

    cancel = subprocess.run(
        [NODE, str(runtime), "cancel", "job-queued-worker"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert cancel.returncode == 1
    payload = json.loads(cancel.stdout)
    assert payload["status"] == "cancel_failed"
    assert "Queued worker cancellation failed" in payload["reason"]
    persisted = json.loads(job_file.read_text())
    assert persisted["status"] == "cancel_failed"
    assert "Queued worker cancellation requires process identity validation" in persisted["cancelFailureReason"]


def test_result_reports_locked_when_view_marker_cannot_be_persisted(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    jobs = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True, check=True)
    state_dir = pathlib.Path(json.loads(jobs.stdout)["stateDir"])
    jobs_dir = state_dir / "jobs"
    job_file = jobs_dir / "job-locked.json"
    job_file.write_text(json.dumps({
        "id": "job-locked",
        "status": "succeeded",
        "createdAt": "2026-05-30T00:00:00.000Z",
        "stdout": "ready"
    }))
    (jobs_dir / "job-locked.json.lock").write_text(json.dumps({
        "pid": os.getpid(),
        "createdAt": "2026-05-30T00:00:00.000Z"
    }))

    result = subprocess.run([NODE, str(runtime), "result", "job-locked"], cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "locked"
    assert "busy" in payload["reason"]
    assert "resultViewedAt" not in json.loads(job_file.read_text())


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


def test_background_progress_buffer_preserves_split_utf8_chunks(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
import time

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
line = "[claude-for-codex progress] " + json.dumps({
    "phase": "reviewing",
    "message": "进度测试",
    "role": "审阅"
}, ensure_ascii=False) + "\\n"
raw = line.encode("utf8")
split = raw.index("测".encode("utf8")) + 1
os.write(sys.stderr.fileno(), raw[:split])
sys.stderr.flush()
time.sleep(0.05)
os.write(sys.stderr.fileno(), raw[split:])
sys.stderr.flush()
print("UTF8_PROGRESS_OK")
"""
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [NODE, str(runtime), "review", "--background", "--wait", "split utf8 progress"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["job"]["stdout"].strip() == "UTF8_PROGRESS_OK"
    assert payload["job"]["lastProgressMessage"] == "进度测试"
    assert payload["job"]["lastProgressRole"] == "审阅"


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
    assert payload["cwd"] == str(repo)
    assert payload["workerCommand"][0] == node_exec_path()
    assert payload["workerCommand"][1] == str(runtime.resolve())
    assert payload["workerCommand"][2:4] == ["run-reserved-job", "--job-id"]
    assert payload["workerCommand"][4] == payload["job"]["id"]
    assert payload["workerCommand"][5:] == ["--cwd", str(repo)]
    assert payload["forwardingInstructions"].startswith("Dispatch exactly one forwarding subagent")
    assert "workerCommand once as argv" in payload["forwardingInstructions"]
    assert "returned cwd" in payload["forwardingInstructions"]


def test_reserve_job_reuses_existing_reserved_job(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("hello\n", encoding="utf8")
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    first = subprocess.run(
        [NODE, str(runtime), "reserve-job", "review", "same-request"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [NODE, str(runtime), "reserve-job", "review", "same-request"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert second_payload["reusedExisting"] is True
    assert second_payload["job"]["id"] == first_payload["job"]["id"]
    assert second_payload["cwd"] == str(repo)
    assert second_payload["workerCommand"] == first_payload["workerCommand"]
    jobs = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True, check=True)
    assert len(json.loads(jobs.stdout)["jobs"]) == 1


def test_reserved_worker_command_uses_embedded_cwd_when_launched_elsewhere(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("hello\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('claude fake')\n"
        "    raise SystemExit(0)\n"
        "print('RESERVED_CWD_DONE')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    reserved = subprocess.run(
        [NODE, str(runtime), "reserve-job", "review", "embedded-cwd"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert reserved.returncode == 0, reserved.stderr
    payload = json.loads(reserved.stdout)
    worker = subprocess.run(
        payload["workerCommand"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert worker.returncode == 0, worker.stderr
    assert json.loads(worker.stdout)["status"] == "succeeded"
    result = subprocess.run(
        [NODE, str(runtime), "result", payload["job"]["id"]],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    result_payload = json.loads(result.stdout)
    assert "RESERVED_CWD_DONE" in result_payload["job"]["stdout"]


def test_reserved_worker_retries_transient_workspace_lock_contention(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    jobs = PLUGIN / "scripts" / "lib" / "jobs.mjs"
    state = PLUGIN / "scripts" / "lib" / "state.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("hello\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('claude fake')\n"
        "    raise SystemExit(0)\n"
        "print('LOCK_RETRY_DONE')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    reserved = subprocess.run(
        [NODE, str(runtime), "reserve-job", "review", "lock-contention"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert reserved.returncode == 0, reserved.stderr
    payload = json.loads(reserved.stdout)
    holder_script = f"""
import {{ withWorkspaceJobLock }} from {json.dumps(jobs.as_uri())};
import {{ stateDirForCwd }} from {json.dumps(state.as_uri())};
const cwd = {json.dumps(str(repo))};
const env = {{ CLAUDE_PLUGIN_DATA: {json.dumps(str(data))}, HOME: {json.dumps(str(tmp_path / "home"))} }};
console.log(JSON.stringify({{ stateDir: stateDirForCwd(cwd, env) }}));
withWorkspaceJobLock(cwd, env, () => {{
  console.log("LOCK_HELD");
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 2300);
  return {{ status: "released" }};
}});
"""
    holder = subprocess.Popen(
        [NODE, "--input-type=module", "--eval", holder_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip()
        assert holder.stdout.readline().strip() == "LOCK_HELD"
        worker = subprocess.run(
            payload["workerCommand"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=5)

    assert worker.returncode == 0, worker.stderr
    assert json.loads(worker.stdout)["status"] == "succeeded"
    result = subprocess.run(
        [NODE, str(runtime), "result", payload["job"]["id"]],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "LOCK_RETRY_DONE" in json.loads(result.stdout)["job"]["stdout"]


def test_direct_background_does_not_reuse_queued_host_forwarded_reservation(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("hello\n", encoding="utf8")
    fake_worker = bin_dir / "fake-node"
    fake_worker.write_text("#!/usr/bin/env sh\nsleep 5\n", encoding="utf8")
    fake_worker.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_WORKER_NODE"] = str(fake_worker)

    reserved = subprocess.run(
        [NODE, str(runtime), "reserve-job", "review", "same-request"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    direct = subprocess.run(
        [NODE, str(runtime), "review", "--background", "same-request"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert reserved.returncode == 0, reserved.stderr
    assert direct.returncode == 0, direct.stderr
    reserved_payload = json.loads(reserved.stdout)
    direct_payload = json.loads(direct.stdout)
    assert reserved_payload["status"] == "reserved"
    assert direct_payload["status"] == "queued"
    assert direct_payload["job"]["id"] != reserved_payload["job"]["id"]
    assert direct_payload["job"].get("reusedExisting") is not True
    jobs = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True, check=True)
    assert len(json.loads(jobs.stdout)["jobs"]) == 2


def test_reserve_job_reuses_running_reserved_job_without_worker_command(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("hello\n", encoding="utf8")
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('claude fake')\n"
        "    raise SystemExit(0)\n"
        "time.sleep(1)\n"
        "print('RESERVED_DONE')\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    first = subprocess.run(
        [NODE, str(runtime), "reserve-job", "review", "same-running-reservation"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    worker = subprocess.Popen(
        first_payload["workerCommand"],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        job_id = first_payload["job"]["id"]
        running = None
        for _ in range(50):
            jobs = subprocess.run([NODE, str(runtime), "jobs"], cwd=repo, env=env, capture_output=True, text=True, check=True)
            running = next(job for job in json.loads(jobs.stdout)["jobs"] if job["id"] == job_id)
            if running["status"] == "running":
                break
            time.sleep(0.05)
        assert running["status"] == "running"

        second = subprocess.run(
            [NODE, str(runtime), "reserve-job", "review", "same-running-reservation"],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
        )
        assert second.returncode == 0, second.stderr
        second_payload = json.loads(second.stdout)
        assert second_payload["status"] == "running"
        assert second_payload["reusedExisting"] is True
        assert second_payload["job"]["id"] == job_id
        assert "workerCommand" not in second_payload
        assert "do not dispatch" in second_payload["message"]

        stdout, stderr = worker.communicate(timeout=5)
        assert worker.returncode == 0, stderr
        assert json.loads(stdout)["status"] == "succeeded"
        result = subprocess.run([NODE, str(runtime), "result", job_id], cwd=repo, env=env, capture_output=True, text=True, check=True)
        result_payload = json.loads(result.stdout)
        assert "RESERVED_DONE" in result_payload["job"]["stdout"]
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=5)


def test_reserve_job_respects_background_capacity_cap(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_MAX_ACTIVE_JOBS"] = "1"

    first = subprocess.run([NODE, str(runtime), "reserve-job", "review", "one"], cwd=repo, env=env, capture_output=True, text=True)
    second = subprocess.run([NODE, str(runtime), "reserve-job", "review", "two"], cwd=repo, env=env, capture_output=True, text=True)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 2
    payload = json.loads(second.stdout)
    assert payload["status"] == "capacity_blocked"
    assert payload["activeCount"] == 1
    assert payload["limit"] == 1


def test_reserve_job_does_not_return_worker_command_for_direct_active_job(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
    fake_worker = bin_dir / "fake-node"
    fake_worker.write_text("#!/usr/bin/env sh\nsleep 5\n", encoding="utf8")
    fake_worker.chmod(0o755)
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data)
    env["CLAUDE_FOR_CODEX_WORKER_NODE"] = str(fake_worker)

    started = subprocess.run([NODE, str(runtime), "review", "--background", "same-request"], cwd=repo, env=env, capture_output=True, text=True)
    reserved = subprocess.run([NODE, str(runtime), "reserve-job", "review", "same-request"], cwd=repo, env=env, capture_output=True, text=True)

    assert started.returncode == 0, started.stderr
    assert reserved.returncode == 0, reserved.stderr
    started_payload = json.loads(started.stdout)
    reserved_payload = json.loads(reserved.stdout)
    assert reserved_payload["status"] == "running"
    assert reserved_payload["reusedExisting"] is True
    assert reserved_payload["job"]["id"] == started_payload["job"]["id"]
    assert "workerCommand" not in reserved_payload
    assert "do not dispatch" in reserved_payload["message"]


def node_exec_path():
    result = subprocess.run(
        [NODE, "-e", "process.stdout.write(process.execPath)"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_subagent_command_prints_foreground_worker_command(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("hello\n", encoding="utf8")
    env = env_without("CLAUDE_FOR_CODEX_BACKEND", "CLAUDE_FOR_CODEX_QUALITY")

    result = subprocess.run(
        [NODE, str(runtime), "subagent-command", "review", "--base", "main", "--path", "file.txt"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ready"
    assert payload["mode"] == "foreground"
    assert payload["command"] == "review"
    assert payload["cwd"] == str(repo)
    assert payload["workerCommand"] == [
        node_exec_path(),
        str(runtime.resolve()),
        "review",
        "--base",
        "main",
        "--path",
        "file.txt",
        "--backend",
        "cli",
        "--quality",
        "auto",
    ]
    instructions = payload["forwardingInstructions"]
    assert "exactly one Codex subagent" in instructions
    assert "workerCommand exactly once as argv" in instructions
    assert "returned cwd" in instructions
    assert "preserve argv boundaries" in instructions
    assert "not inspect or reinterpret the repository first" in instructions
    assert "not replace it with raw claude or claude -p" in instructions


@pytest.mark.parametrize(
    ("delegated_args", "expected_command"),
    [
        (["review", "--scope", "working-tree", "delegated smoke"], "review"),
        (["rescue", "delegated rescue smoke"], "rescue"),
    ],
)
def test_subagent_command_worker_command_executes_with_fake_claude(tmp_path, delegated_args, expected_command):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    argv_file = tmp_path / "argv.json"
    repo.mkdir()
    bin_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("base\n", encoding="utf8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "file.txt").write_text("base\nchanged\n", encoding="utf8")

    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env node\n"
        "const fs = require('fs');\n"
        "const argv = process.argv.slice(2);\n"
        "if (argv.length === 1 && argv[0] === '--version') {\n"
        "  process.stdout.write('claude fake\\n');\n"
        "  process.exit(0);\n"
        "}\n"
        f"fs.writeFileSync({json.dumps(str(argv_file))}, JSON.stringify(argv));\n"
        "process.stdout.write('SUBAGENT CLAUDE OK\\n');\n",
        encoding="utf8",
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["CLAUDE_CODE_PATH"] = str(fake_claude)

    command_result = subprocess.run(
        [NODE, str(runtime), "subagent-command", *delegated_args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert command_result.returncode == 0, command_result.stderr
    payload = json.loads(command_result.stdout)
    assert payload["status"] == "ready"
    assert payload["command"] == expected_command
    assert payload["cwd"] == str(repo)

    worker_result = subprocess.run(
        payload["workerCommand"],
        cwd=payload["cwd"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert worker_result.returncode == 0, worker_result.stderr
    assert "SUBAGENT CLAUDE OK" in worker_result.stdout
    argv = json.loads(argv_file.read_text(encoding="utf8"))
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert "--strict-mcp-config" in argv
    assert "--disable-slash-commands" in argv
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    disallowed_tools = argv[argv.index("--disallowedTools") + 1].split(",")
    for tool in ["Edit", "Write", "MultiEdit", "Bash"]:
        assert tool in disallowed_tools


def test_subagent_command_normalizes_relative_parent_runtime_paths(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = env_without("CLAUDE_FOR_CODEX_BACKEND", "CLAUDE_FOR_CODEX_QUALITY")

    result = subprocess.run(
        [NODE, "claude-companion.mjs", "subagent-command", "rescue", "fix flaky test"],
        cwd=runtime.parent,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["command"] == "rescue"
    assert payload["workerCommand"][1] == str(runtime.resolve())
    assert payload["workerCommand"][2:] == ["rescue", "fix flaky test", "--backend", "cli", "--quality", "auto"]


def test_subagent_command_validates_delegated_command_not_top_level_command(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()

    result = subprocess.run(
        [NODE, str(runtime), "subagent-command", "status"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert 'Command "status" cannot be delegated to a Codex subagent.' in result.stderr


@pytest.mark.parametrize(
    ("command_args", "stderr_marker"),
    [
        (
            ["ultrareview", "--confirm-cost"],
            'Command "ultrareview" cannot be delegated to a Codex subagent.',
        ),
        (
            ["review", "--background", "--base", "main"],
            "subagent-command is foreground-only; use reserve-job for --background delegation.",
        ),
        (
            ["review", "--write", "--base", "main"],
            "--write cannot be delegated to a Codex subagent;",
        ),
        (
            ["adversarial-review", "--write", "--base", "main"],
            "--write cannot be delegated to a Codex subagent;",
        ),
        (
            ["multi-review", "--write", "--base", "main"],
            "--write cannot be delegated to a Codex subagent;",
        ),
        (
            ["rescue", "--write", "fix flaky test"],
            "--write cannot be delegated to a Codex subagent;",
        ),
    ],
)
def test_subagent_command_rejects_unsafe_modes(tmp_path, command_args, stderr_marker):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()

    result = subprocess.run(
        [NODE, str(runtime), "subagent-command", *command_args],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert stderr_marker in result.stderr


def test_subagent_command_materializes_backend_env_dependency(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = env_without("CLAUDE_FOR_CODEX_QUALITY")
    env["CLAUDE_FOR_CODEX_BACKEND"] = "sdk"

    result = subprocess.run(
        [NODE, str(runtime), "subagent-command", "review", "--scope", "working-tree"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["workerCommand"][2:] == [
        "review",
        "--scope",
        "working-tree",
        "--backend",
        "sdk",
        "--quality",
        "auto",
    ]


def test_subagent_command_materializes_quality_env_dependency(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = env_without("CLAUDE_FOR_CODEX_BACKEND", "CLAUDE_FOR_CODEX_QUALITY")
    env["CLAUDE_FOR_CODEX_QUALITY"] = "strong"

    result = subprocess.run(
        [NODE, str(runtime), "subagent-command", "review", "--scope", "working-tree"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["workerCommand"][2:] == [
        "review",
        "--scope",
        "working-tree",
        "--backend",
        "cli",
        "--quality",
        "strong",
    ]


def test_subagent_command_rejects_sdk_subagents_without_sdk_backend(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()

    result = subprocess.run(
        [NODE, str(runtime), "subagent-command", "multi-review", "--agent-team", "sdk-subagents"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--agent-team sdk-subagents requires --backend sdk or CLAUDE_FOR_CODEX_BACKEND=sdk." in result.stderr


def test_reserve_job_rejects_sdk_subagents_without_sdk_backend(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    repo.mkdir()
    data.mkdir()
    env = env_without("CLAUDE_FOR_CODEX_BACKEND")
    env["CLAUDE_PLUGIN_DATA"] = str(data)

    result = subprocess.run(
        [NODE, str(runtime), "reserve-job", "multi-review", "--agent-team", "sdk-subagents"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--agent-team sdk-subagents requires --backend sdk or CLAUDE_FOR_CODEX_BACKEND=sdk." in result.stderr
    jobs = subprocess.run(
        [NODE, str(runtime), "jobs"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert jobs.returncode == 0, jobs.stderr
    assert json.loads(jobs.stdout)["jobs"] == []


def test_subagent_command_allows_sdk_subagents_with_explicit_sdk_backend(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = env_without("CLAUDE_FOR_CODEX_BACKEND", "CLAUDE_FOR_CODEX_QUALITY")

    result = subprocess.run(
        [
            NODE,
            str(runtime),
            "subagent-command",
            "multi-review",
            "--backend",
            "sdk",
            "--agent-team",
            "sdk-subagents",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ready"
    assert payload["command"] == "multi-review"
    assert payload["workerCommand"][2:] == [
        "multi-review",
        "--backend",
        "sdk",
        "--agent-team",
        "sdk-subagents",
        "--quality",
        "auto",
    ]


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


def test_internal_job_worker_reports_finish_failure_when_job_file_disappears(tmp_path):
    runtime = PLUGIN / "scripts" / "claude-companion.mjs"
    repo = tmp_path / "repo"
    data = tmp_path / "plugin-data"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    data.mkdir()
    fake_bin.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "changed.txt").write_text("change\n", encoding="utf8")
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
    job_file = state_dir / "jobs" / "job-finish-missing.json"
    job_file.write_text(json.dumps({
        "id": "job-finish-missing",
        "status": "queued",
        "command": "review",
        "args": ["finish missing"],
        "cwd": str(repo)
    }))

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import sys

if sys.argv[1:] == ["--version"]:
    print("claude fake")
    raise SystemExit(0)
pathlib.Path(os.environ["JOB_FILE_TO_DELETE"]).unlink()
print("CLAUDE FINISHED")
"""
    )
    fake_claude.chmod(0o755)
    env["JOB_FILE_TO_DELETE"] = str(job_file)

    worker = subprocess.run(
        [NODE, str(runtime), "__run-job", "job-finish-missing"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert worker.returncode == 1
    payload = json.loads(worker.stdout)
    assert payload["status"] == "finish_failed"
    assert payload["jobId"] == "job-finish-missing"
    assert "final state could not be persisted" in payload["message"]
    assert not job_file.exists()


def test_running_cancel_treats_absent_worker_as_cancel_failed_and_corrupt_result_is_reported(tmp_path):
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
    assert "did not deliver a signal" in cancel_payload["reason"]

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
time.sleep(120)
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
        if job["status"] == "running" and job.get("childProcessGroupPid"):
            break
        time.sleep(0.1)
    assert job["status"] == "running"
    assert job["pidIdentity"]["command"]
    assert job["childProcessGroupIdentity"]["command"]

    cancel = run_cancel_with_lock_retry(runtime, job_id, cwd=repo, env=env)
    assert cancel.returncode == 0, cancel.stderr
    cancel_payload = json.loads(cancel.stdout)
    assert cancel_payload["status"] == "cancelled"
    assert cancel_payload["job"]["cancelWorkerIdentity"]["pid"] == job["workerPid"]
    assert cancel_payload["job"]["cancelChildIdentity"]["pid"] == job["childProcessGroupPid"]
    assert cancel_payload["job"]["cancelWorkerDeferred"] is True
    assert cancel_payload["job"]["cancelWorkerDelivered"] is False
    for _ in range(30):
        if not process_is_running(job["workerPid"]):
            break
        time.sleep(0.1)
    assert not process_is_running(job["workerPid"])


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
time.sleep(120)
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
            if job["status"] == "running" and job.get("childProcessGroupPid"):
                break
            time.sleep(0.1)
        assert job["status"] == "running"
        assert "run-reserved-job" in job["pidIdentity"]["command"]
        assert job["childProcessGroupIdentity"]["command"]

        cancel = run_cancel_with_lock_retry(runtime, job_id, cwd=repo, env=env)
        assert cancel.returncode == 0, cancel.stderr
        cancel_payload = json.loads(cancel.stdout)
        assert cancel_payload["status"] == "cancelled"
        assert cancel_payload["job"]["cancelWorkerIdentity"]["pid"] == worker.pid
        assert "run-reserved-job" in cancel_payload["job"]["cancelWorkerIdentity"]["command"]
        assert cancel_payload["job"]["cancelChildIdentity"]["pid"] == job["childProcessGroupPid"]
        assert cancel_payload["job"]["cancelWorkerDeferred"] is True
        assert cancel_payload["job"]["cancelWorkerDelivered"] is False
        worker_stdout, worker_stderr = worker.communicate(timeout=5)
        assert worker.returncode == 0, worker_stderr
        worker_payload = json.loads(worker_stdout)
        assert worker_payload["status"] == "cancelled"
        assert worker_payload["exitStatus"] == 0
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


def test_claude_subagent_review_skill_documents_safe_plugin_delegation():
    text = (PLUGIN / "skills" / "claude-subagent-review" / "SKILL.md").read_text(encoding="utf8")

    required_phrases = [
        "subagent-command",
        "workerCommand",
        "returned `cwd`",
        "run `workerCommand` exactly once",
        "must not inspect or reinterpret the repository",
        "must not replace it with raw claude",
        "claude -p",
        "reserve-job",
        "--background",
        "--write",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_review_skills_cross_link_subagent_delegation_without_raw_claude():
    skill_commands = {
        "claude-review": "review",
        "claude-adversarial-review": "adversarial-review",
        "claude-multi-review": "multi-review",
        "claude-rescue": "rescue",
    }

    for skill_name, command in skill_commands.items():
        text = (PLUGIN / "skills" / skill_name / "SKILL.md").read_text(encoding="utf8")
        assert "claude-subagent-review" in text
        assert f'subagent-command {command} "$ARGUMENTS"' in text
        assert "raw `claude -p`" in text


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


def test_hook_fingerprint_options_clamp_work_under_prompt_hook_timeout():
    module_uri = (PLUGIN / "scripts" / "lib" / "worktree-fingerprint.mjs").as_uri()
    script = f"""
import {{ hookFingerprintOptions }} from {json.dumps(module_uri)};
const options = hookFingerprintOptions({{
  CLAUDE_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS: "50",
  CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_BYTES: "9999999",
  CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_FILES: "999"
}});
console.log(JSON.stringify(options.env));
"""
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["CLAUDE_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS"] == "500"
    assert env["CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_BYTES"] == str(512 * 1024)
    assert env["CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_FILES"] == "128"


def test_prompt_and_stop_hooks_share_bounded_fingerprint_options():
    runtime = (PLUGIN / "scripts" / "claude-companion.mjs").read_text(encoding="utf8")
    unread = (PLUGIN / "hooks" / "unread-result.mjs").read_text(encoding="utf8")
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf8"))
    prompt_commands = hooks["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert any(command.get("timeout") == 5 for command in prompt_commands)
    assert "workingTreeFingerprint(cwd, [], hookFingerprintOptions())" in unread
    assert "const hookOptions = hookFingerprintOptions()" in runtime
    assert "workingTreeFingerprintDetails(cwd, [], hookOptions)" in runtime


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
    (state_dir / "turn-baseline.json").write_text(json.dumps({
        "sessionId": "s1",
        "workingTreeFingerprint": companion_working_tree_fingerprint(repo)
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


def test_review_gate_skips_when_legacy_raw_turn_baseline_matches_current_diff(tmp_path):
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
    (state_dir / "turn-baseline.json").write_text(json.dumps({
        "sessionId": "s1",
        "workingTreeFingerprint": legacy_raw_hook_working_tree_fingerprint(repo)
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


def test_review_gate_fingerprint_timeout_fails_open_without_claude(tmp_path):
    runtime, repo, capture_dir, env = prepare_gate_repo(tmp_path)
    setup = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr

    real_git = shutil.which("git")
    assert real_git
    fake_git = pathlib.Path(env["PATH"].split(os.pathsep)[0]) / "git"
    fake_git.write_text(
        f"""#!/usr/bin/env python3
import os
import sys
import time

args = sys.argv[1:]
if args == ["rev-parse", "--is-inside-work-tree"]:
    print("true")
    raise SystemExit(0)
if args == ["rev-parse", "HEAD"]:
    print("abc123")
    raise SystemExit(0)
if args[:1] == ["status"]:
    print(" M file.txt")
    raise SystemExit(0)
if args[:1] == ["diff"]:
    time.sleep(1)
    raise SystemExit(0)
if args[:1] == ["ls-files"]:
    raise SystemExit(0)
os.execv({json.dumps(real_git)}, ["git", *args])
""",
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    env["CLAUDE_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS"] = "100"

    result = subprocess.run(
        ["node", str(runtime), "review-gate"],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "Stop", "cwd": str(repo)}),
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert "fingerprint timed out; allowing stop" in result.stderr
    assert list(capture_dir.glob("prompt-*.txt")) == []


def test_review_gate_inconclusive_fingerprint_failure_does_not_claim_timeout(tmp_path):
    runtime, repo, capture_dir, env = prepare_gate_repo(tmp_path)
    setup = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr

    real_git = shutil.which("git")
    assert real_git
    fake_git = pathlib.Path(env["PATH"].split(os.pathsep)[0]) / "git"
    fake_git.write_text(
        f"""#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
if args == ["rev-parse", "--is-inside-work-tree"]:
    print("true")
    raise SystemExit(0)
if args == ["rev-parse", "HEAD"]:
    print("abc123")
    raise SystemExit(0)
if args[:1] == ["status"]:
    print(" M file.txt")
    raise SystemExit(0)
if args[:1] == ["diff"]:
    print("fatal: synthetic diff failure", file=sys.stderr)
    raise SystemExit(2)
if args[:1] == ["ls-files"]:
    raise SystemExit(0)
os.execv({json.dumps(real_git)}, ["git", *args])
""",
        encoding="utf8",
    )
    fake_git.chmod(0o755)

    result = subprocess.run(
        ["node", str(runtime), "review-gate"],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "Stop", "cwd": str(repo)}),
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert "fingerprint inconclusive; allowing stop" in result.stderr
    assert "fingerprint timed out" not in result.stderr
    assert list(capture_dir.glob("prompt-*.txt")) == []


def test_review_gate_reviewable_git_timeout_fails_open_without_claude(tmp_path):
    runtime, repo, capture_dir, env = prepare_gate_repo(tmp_path)
    setup = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr

    real_git = shutil.which("git")
    assert real_git
    fake_git = pathlib.Path(env["PATH"].split(os.pathsep)[0]) / "git"
    fake_git.write_text(
        f"""#!/usr/bin/env python3
import os
import sys
import time

args = sys.argv[1:]
if args == ["rev-parse", "--is-inside-work-tree"]:
    print("true")
    raise SystemExit(0)
if args[:1] == ["status"]:
    time.sleep(1)
    raise SystemExit(0)
os.execv({json.dumps(real_git)}, ["git", *args])
""",
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    env["CLAUDE_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS"] = "100"

    result = subprocess.run(
        ["node", str(runtime), "review-gate"],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "Stop", "cwd": str(repo)}),
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert "git status timed out; allowing stop" in result.stderr
    assert list(capture_dir.glob("prompt-*.txt")) == []


def test_review_gate_reviewable_git_uses_hook_safe_timeout_cap(tmp_path):
    runtime, repo, capture_dir, env = prepare_gate_repo(tmp_path)
    setup = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr

    real_git = shutil.which("git")
    assert real_git
    fake_git = pathlib.Path(env["PATH"].split(os.pathsep)[0]) / "git"
    fake_git.write_text(
        f"""#!/usr/bin/env python3
import os
import sys
import time

args = sys.argv[1:]
if args == ["rev-parse", "--is-inside-work-tree"]:
    print("true")
    raise SystemExit(0)
if args[:1] == ["status"]:
    time.sleep(1)
    print(" M file.txt")
    raise SystemExit(0)
os.execv({json.dumps(real_git)}, ["git", *args])
""",
        encoding="utf8",
    )
    fake_git.chmod(0o755)
    env["CLAUDE_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS"] = "60000"

    result = subprocess.run(
        ["node", str(runtime), "review-gate"],
        cwd=repo,
        env=env,
        input=json.dumps({"hook_event_name": "Stop", "cwd": str(repo)}),
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert "git status timed out; allowing stop" in result.stderr
    assert list(capture_dir.glob("prompt-*.txt")) == []


def test_review_gate_untracked_budget_exceeded_still_runs_review_without_cache(tmp_path):
    runtime, repo, capture_dir, env = prepare_gate_repo(tmp_path)
    (repo / "large-untracked.txt").write_text("12345\n", encoding="utf8")
    env["CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_BYTES"] = "4"
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
    assert first.stdout == ""
    assert second.stdout == ""
    assert "fingerprint budget exceeded; running review without cached gate decision" in first.stderr
    assert "fingerprint budget exceeded; running review without cached gate decision" in second.stderr
    assert len(list(capture_dir.glob("prompt-*.txt"))) == 10


def test_review_gate_all_allow_exits_without_block(tmp_path):
    result, prompts, _capture_dir = run_fake_review_gate(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert len(prompts) == 5
    for role in ["correctness", "security", "tests", "release", "adversarial"]:
        assert f"<role_name>{role}</role_name>" in "\n".join(prompts)
        assert "Your first line must be exactly one of:" in "\n".join(prompts)


def test_review_gate_aggregate_timeout_stops_later_roles(tmp_path):
    result, prompts, _capture_dir = run_fake_review_gate(
        tmp_path,
        extra_env={
            "CLAUDE_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS": "1000",
            "SLEEP_MS": "1200",
        },
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert len(prompts) == 1
    assert "role correctness timed out; allowing stop" in result.stderr
    assert "review gate aggregate timeout reached; allowing stop" in result.stderr


def test_review_gate_aggregate_timeout_does_not_cache_allow(tmp_path):
    runtime, repo, capture_dir, env = prepare_gate_repo(tmp_path)
    setup = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate", "--review-gate-mode", "multi-role"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert setup.returncode == 0, setup.stderr
    hook_input = json.dumps({"hook_event_name": "Stop", "cwd": str(repo)})
    first_env = env.copy()
    first_env["CLAUDE_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS"] = "1000"
    first_env["SLEEP_MS"] = "1200"

    first = subprocess.run(
        [NODE, str(runtime), "review-gate"],
        cwd=repo,
        env=first_env,
        input=hook_input,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [NODE, str(runtime), "review-gate"],
        cwd=repo,
        env=env,
        input=hook_input,
        capture_output=True,
        text=True,
    )

    assert first.returncode == 0
    assert second.returncode == 0
    assert first.stdout == ""
    assert second.stdout == ""
    assert "review gate aggregate timeout reached; allowing stop" in first.stderr
    assert second.stderr == ""
    assert len(list(capture_dir.glob("prompt-*.txt"))) == 6


def test_review_gate_timeout_kills_child_that_ignores_sigterm(tmp_path):
    result, prompts, _capture_dir = run_fake_review_gate(
        tmp_path,
        extra_env={
            "CLAUDE_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS": "1000",
            "CLAUDE_FOR_CODEX_CLAUDE_KILL_GRACE_MS": "100",
            "IGNORE_TERM": "1",
            "SLEEP_MS": "30000",
        },
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert len(prompts) == 1
    assert "role correctness timed out; allowing stop" in result.stderr
    assert "review gate aggregate timeout reached; allowing stop" in result.stderr


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


def test_review_gate_allow_cache_is_role_pack_aware(tmp_path):
    runtime, repo, capture_dir, env = prepare_gate_repo(tmp_path)
    setup = subprocess.run(
        ["node", str(runtime), "setup", "--enable-review-gate", "--review-gate-mode", "multi-role"],
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
        ["node", str(runtime), "review-gate", "--role-pack", "minimal"],
        cwd=repo,
        env=env,
        input=hook_input,
        capture_output=True,
        text=True,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == ""
    assert second.stdout == ""
    prompts = [
        path.read_text()
        for path in sorted(capture_dir.glob("prompt-*.txt"), key=lambda p: int(p.stem.split("-")[1]))
    ]
    assert len(prompts) == 6
    assert sum("<role_name>correctness</role_name>" in prompt for prompt in prompts) == 2
    assert sum("<role_name>security</role_name>" in prompt for prompt in prompts) == 1
    assert prompts[-1].count("<role_name>correctness</role_name>") == 1


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
        "claude-subagent-review": "subagent-command",
        "claude-ultrareview": "ultrareview",
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
        "claude-subagent-review",
        "claude-ultrareview",
    }
    for skill in skills:
        text = skill.read_text()
        frontmatter = parse_skill_frontmatter(text)
        assert frontmatter["name"] == skill.parent.name
        assert frontmatter["description"]
        assert "claude-companion.mjs" in text
        assert f'claude-companion.mjs" {expected_commands[skill.parent.name]}' in text
        if skill.parent.name == "claude-ultrareview":
            assert "CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1" in text
