import process from "node:process";
import { spawnSyncWithRetry } from "./spawn-retry.mjs";

const DEFAULT_PS_TIMEOUT_MS = 2000;
const MIN_PS_TIMEOUT_MS = 1;
const MAX_PS_TIMEOUT_MS = 30000;
const MAX_PROBE_BUFFER_BYTES = 20 * 1024 * 1024;
const SIGTERM_GRACE_MS = 250;
const SIGKILL_GRACE_MS = 750;
const WINDOWS_KILL_GRACE_MS = 1000;

const psProbeState = {
  failures: 0,
  lastFailure: null
};

function spawnSync(command, args, options) {
  return spawnSyncWithRetry(command, args, options);
}

function boundedInteger(value, fallback, min, max) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, Math.trunc(numericValue)));
}

function psTimeoutMs(env = process.env) {
  return boundedInteger(
    env.ANTIGRAVITY_FOR_CODEX_PS_TIMEOUT_MS,
    DEFAULT_PS_TIMEOUT_MS,
    MIN_PS_TIMEOUT_MS,
    MAX_PS_TIMEOUT_MS
  );
}

function sleepMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function diagnosticExcerpt(value) {
  return String(value || "").slice(0, 4096);
}

function recordProbeFailure(label, command, args, result, timeoutMs) {
  const diagnostic = {
    label,
    command,
    args,
    timeoutMs,
    status: result.status,
    signal: result.signal || "",
    error: result.error ? String(result.error.message || result.error) : "",
    stderr: diagnosticExcerpt(result.stderr),
    stdout: diagnosticExcerpt(result.stdout),
    at: new Date().toISOString()
  };
  psProbeState.failures += 1;
  psProbeState.lastFailure = diagnostic;
  return diagnostic;
}

function commandProbeFailed(result) {
  return Boolean(result.error || result.signal || result.status === null);
}

function runProbeCommand(command, args, env = process.env, label = command) {
  const timeoutMs = psTimeoutMs(env);
  const result = spawnSync(command, args, {
    encoding: "utf8",
    env,
    timeout: timeoutMs,
    killSignal: "SIGKILL",
    maxBuffer: MAX_PROBE_BUFFER_BYTES
  });
  if (commandProbeFailed(result)) {
    result.diagnostic = recordProbeFailure(label, command, args, result, timeoutMs);
  }
  return result;
}

function resultEmittedDiagnostics(result) {
  return Boolean(String(result.stderr || "").trim() || String(result.stdout || "").trim());
}

function nonzeroProbeDiagnostic(label, command, args, result, timeoutMs) {
  return recordProbeFailure(label, command, args, result, timeoutMs);
}

export function psProbeDiagnostics() {
  return {
    failures: psProbeState.failures,
    lastFailure: psProbeState.lastFailure ? { ...psProbeState.lastFailure } : null
  };
}

function notRunningProbeResult() {
  return { ok: false, notRunning: true, inconclusive: false, identity: null };
}

function inconclusiveProbeResult(diagnostic) {
  return {
    ok: false,
    notRunning: false,
    inconclusive: true,
    identity: null,
    diagnostic
  };
}

function okProbeResult(identity) {
  return {
    ok: true,
    notRunning: false,
    inconclusive: false,
    identity
  };
}

export function captureProcessIdentityProbe(pid, env = process.env) {
  const numericPid = Number(pid);
  if (!Number.isInteger(numericPid) || numericPid <= 0) {
    return notRunningProbeResult();
  }

  if (process.platform === "win32") {
    const script = [
      `$p = Get-CimInstance Win32_Process -Filter "ProcessId = ${numericPid}"`,
      "if ($null -eq $p) { exit 1 }",
      "$p | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    ].join("; ");
    const result = runProbeCommand(
      "powershell.exe",
      ["-NoProfile", "-Command", script],
      env,
      "powershell process identity"
    );
    if (result.diagnostic) {
      return inconclusiveProbeResult(result.diagnostic);
    }
    if (result.status !== 0) {
      const firstDiagnostic = resultEmittedDiagnostics(result)
        ? nonzeroProbeDiagnostic(
            "powershell process identity nonzero",
            "powershell.exe",
            ["-NoProfile", "-Command", script],
            result,
            psTimeoutMs(env)
          )
        : null;
      const existsScript = [
        `$p = Get-Process -Id ${numericPid} -ErrorAction SilentlyContinue`,
        "if ($null -eq $p) { exit 1 }",
        "exit 0"
      ].join("; ");
      const existsResult = runProbeCommand(
        "powershell.exe",
        ["-NoProfile", "-Command", existsScript],
        env,
        "powershell process exists"
      );
      if (existsResult.diagnostic) {
        return inconclusiveProbeResult(existsResult.diagnostic);
      }
      if (existsResult.status === 0) {
        return inconclusiveProbeResult(firstDiagnostic || nonzeroProbeDiagnostic(
          "powershell process identity nonzero",
          "powershell.exe",
          ["-NoProfile", "-Command", script],
          result,
          psTimeoutMs(env)
        ));
      }
      if (resultEmittedDiagnostics(existsResult)) {
        return inconclusiveProbeResult(nonzeroProbeDiagnostic(
          "powershell process exists nonzero",
          "powershell.exe",
          ["-NoProfile", "-Command", existsScript],
          existsResult,
          psTimeoutMs(env)
        ));
      }
      if (firstDiagnostic) {
        return inconclusiveProbeResult(firstDiagnostic);
      }
      return notRunningProbeResult();
    }
    try {
      const payload = JSON.parse(String(result.stdout || "").trim());
      return okProbeResult({
        pid: Number(payload.ProcessId || numericPid),
        ppid: Number(payload.ParentProcessId || 0),
        command: String(payload.CommandLine || "")
      });
    } catch (error) {
      return inconclusiveProbeResult({
        label: "powershell process identity parse",
        error: error.message || String(error),
        stdout: diagnosticExcerpt(result.stdout),
        at: new Date().toISOString()
      });
    }
  }

  const result = runProbeCommand(
    "ps",
    ["-p", String(numericPid), "-o", "pid=,ppid=,command="],
    env,
    "ps process identity"
  );
  if (result.diagnostic) {
    return inconclusiveProbeResult(result.diagnostic);
  }
  if (result.status !== 0) {
    if (processExists(numericPid) || resultEmittedDiagnostics(result)) {
      return inconclusiveProbeResult(nonzeroProbeDiagnostic(
        "ps process identity nonzero",
        "ps",
        ["-p", String(numericPid), "-o", "pid=,ppid=,command="],
        result,
        psTimeoutMs(env)
      ));
    }
    return notRunningProbeResult();
  }
  const line = String(result.stdout || "").trim();
  const match = line.match(/^(\d+)\s+(\d+)\s+(.*)$/s);
  if (!match) {
    return inconclusiveProbeResult({
      label: "ps process identity parse",
      status: result.status,
      stdout: diagnosticExcerpt(result.stdout),
      stderr: diagnosticExcerpt(result.stderr),
      at: new Date().toISOString()
    });
  }
  return okProbeResult({
    pid: Number(match[1]),
    ppid: Number(match[2]),
    command: match[3] || ""
  });
}

export function captureProcessIdentity(pid) {
  const numericPid = Number(pid);
  const probe = captureProcessIdentityProbe(pid);
  return probe.identity || {
    pid: Number.isInteger(numericPid) && numericPid > 0 ? numericPid : 0,
    ppid: 0,
    command: ""
  };
}

function processGroupExists(pid) {
  try {
    process.kill(-pid, 0);
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") {
      return false;
    }
    return true;
  }
}

function processExists(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") {
      return false;
    }
    return true;
  }
}

function waitForExit(check, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (check()) {
      return true;
    }
    sleepMs(25);
  }
  return check();
}

function sameProcessIdentity(expected, actual) {
  const expectedPid = Number(expected?.pid);
  const actualPid = Number(actual?.pid);
  if (!Number.isInteger(expectedPid) || expectedPid <= 0 || actualPid !== expectedPid) {
    return false;
  }
  const expectedPpid = Number(expected?.ppid);
  if (Number.isInteger(expectedPpid) && expectedPpid > 0 && Number(actual?.ppid) !== expectedPpid) {
    return false;
  }
  const expectedCommand = String(expected?.command || "");
  const actualCommand = String(actual?.command || "");
  return Boolean(expectedCommand && actualCommand && actualCommand === expectedCommand);
}

export function hasTrustedExpectedIdentity(expected) {
  const expectedPid = Number(expected?.pid);
  const expectedCommand = String(expected?.command || "");
  return Number.isInteger(expectedPid)
    && expectedPid > 0
    && expectedCommand.includes("antigravity-companion.mjs")
    && expectedCommand.includes("__run-job");
}

export function hasTrustedCompanionChildIdentity(expected) {
  const expectedPid = Number(expected?.pid);
  const expectedCommand = String(expected?.command || "");
  return Number.isInteger(expectedPid)
    && expectedPid > 0
    && expectedCommand.includes("antigravity-companion.mjs")
    && /\b(review|adversarial-review|multi-review|plan|rescue)\b/.test(expectedCommand)
    && !expectedCommand.includes("__run-job");
}

function failedProbeResult(phase, probe) {
  return {
    status: "failed",
    error: `process identity probe inconclusive during ${phase}`,
    phase,
    diagnostic: probe.diagnostic || null
  };
}

function identityMismatchResult(phase, current, expected, reason = "process identity mismatch") {
  return {
    status: "failed",
    error: `${reason} during ${phase}`,
    phase,
    current,
    expected
  };
}

function signalWorkerTree(pid, signal, env = process.env) {
  if (process.platform === "win32") {
    const timeoutMs = psTimeoutMs(env);
    const result = spawnSync("taskkill.exe", ["/PID", String(pid), "/T", "/F"], {
      encoding: "utf8",
      env,
      timeout: timeoutMs,
      killSignal: "SIGKILL",
      maxBuffer: MAX_PROBE_BUFFER_BYTES
    });
    if (result.error || result.signal || result.status !== 0) {
      return {
        ok: false,
        error: result.error ? String(result.error.message || result.error) : String(result.stderr || "taskkill failed").trim(),
        diagnostic: {
          command: "taskkill.exe",
          args: ["/PID", String(pid), "/T", "/F"],
          timeoutMs,
          status: result.status,
          signal: result.signal || "",
          stderr: diagnosticExcerpt(result.stderr),
          stdout: diagnosticExcerpt(result.stdout)
        }
      };
    }
    return { ok: true, signal: "TASKKILL" };
  }

  try {
    process.kill(-pid, signal);
    return { ok: true, signal };
  } catch (error) {
    if (error?.code === "ESRCH") {
      return { ok: true, notRunning: true, signal };
    }
    return { ok: false, error: error.message || String(error) };
  }
}

function terminateValidatedProcess(pid, expectedIdentity = {}, env = process.env, trustFn = hasTrustedExpectedIdentity, trustError = "missing trusted worker identity") {
  const numericPid = Number(pid);
  if (!trustFn(expectedIdentity)) {
    return {
      status: "failed",
      error: trustError,
      phase: "initial",
      diagnostic: { expected: expectedIdentity || null }
    };
  }
  if (!Number.isInteger(numericPid) || numericPid <= 0) {
    return { status: "not_running" };
  }

  const initialProbe = captureProcessIdentityProbe(numericPid, env);
  if (initialProbe.inconclusive) {
    return failedProbeResult("initial", initialProbe);
  }
  if (initialProbe.notRunning) {
    return { status: "not_running" };
  }
  if (!sameProcessIdentity(expectedIdentity, initialProbe.identity)) {
    return identityMismatchResult("initial", initialProbe.identity, expectedIdentity);
  }

  if (process.platform === "win32") {
    const killed = signalWorkerTree(numericPid, "TASKKILL", env);
    if (!killed.ok) {
      const postTaskkillFailureProbe = captureProcessIdentityProbe(numericPid, env);
      if (postTaskkillFailureProbe.inconclusive) {
        return failedProbeResult("post-taskkill-failure", postTaskkillFailureProbe);
      }
      if (postTaskkillFailureProbe.notRunning) {
        return {
          status: "not_running",
          phase: "taskkill",
          current: initialProbe.identity,
          diagnostic: killed.diagnostic || null
        };
      }
      return {
        status: "failed",
        error: killed.error || "taskkill failed",
        phase: "taskkill",
        current: postTaskkillFailureProbe.identity || initialProbe.identity,
        diagnostic: killed.diagnostic || null
      };
    }
    if (waitForExit(() => !processExists(numericPid), WINDOWS_KILL_GRACE_MS)) {
      return { status: "terminated", signal: killed.signal, current: initialProbe.identity };
    }
    const finalProbe = captureProcessIdentityProbe(numericPid, env);
    if (finalProbe.inconclusive) {
      return failedProbeResult("post-taskkill", finalProbe);
    }
    return {
      status: "failed",
      error: "process remained running after taskkill",
      phase: "post-taskkill",
      current: finalProbe.identity || initialProbe.identity,
      expected: expectedIdentity
    };
  }

  const sigterm = signalWorkerTree(numericPid, "SIGTERM", env);
  if (!sigterm.ok) {
    return {
      status: "failed",
      error: sigterm.error || "SIGTERM failed",
      phase: "sigterm",
      current: initialProbe.identity
    };
  }
  if (sigterm.notRunning || waitForExit(() => !processGroupExists(numericPid), SIGTERM_GRACE_MS)) {
    return { status: "terminated", signal: "SIGTERM", current: initialProbe.identity };
  }

  const afterSigtermProbe = captureProcessIdentityProbe(numericPid, env);
  if (afterSigtermProbe.inconclusive) {
    return failedProbeResult("post-sigterm", afterSigtermProbe);
  }
  if (afterSigtermProbe.ok && !sameProcessIdentity(expectedIdentity, afterSigtermProbe.identity)) {
    return identityMismatchResult("post-sigterm", afterSigtermProbe.identity, expectedIdentity);
  }

  const sigkill = signalWorkerTree(numericPid, "SIGKILL", env);
  if (!sigkill.ok) {
    return {
      status: "failed",
      error: sigkill.error || "SIGKILL failed",
      phase: "sigkill",
      current: afterSigtermProbe.identity || initialProbe.identity
    };
  }
  if (sigkill.notRunning || waitForExit(() => !processGroupExists(numericPid), SIGKILL_GRACE_MS)) {
    return { status: "terminated", signal: "SIGKILL", current: initialProbe.identity };
  }

  const finalProbe = captureProcessIdentityProbe(numericPid, env);
  if (finalProbe.inconclusive) {
    return failedProbeResult("post-sigkill", finalProbe);
  }
  if (finalProbe.ok && !sameProcessIdentity(expectedIdentity, finalProbe.identity)) {
    return identityMismatchResult("post-sigkill", finalProbe.identity, expectedIdentity);
  }
  return {
    status: "failed",
    error: "process group remained running after SIGKILL",
    phase: "post-sigkill",
    current: finalProbe.identity || initialProbe.identity,
    expected: expectedIdentity
  };
}

export function terminateValidatedJobWorker(pid, expectedIdentity = {}, env = process.env) {
  return terminateValidatedProcess(pid, expectedIdentity, env, hasTrustedExpectedIdentity, "missing trusted worker identity");
}

export function terminateValidatedCompanionChild(pid, expectedIdentity = {}, env = process.env) {
  return terminateValidatedProcess(pid, expectedIdentity, env, hasTrustedCompanionChildIdentity, "missing trusted supervised worker identity");
}
