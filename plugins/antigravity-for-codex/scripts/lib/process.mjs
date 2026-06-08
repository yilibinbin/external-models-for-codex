import { spawnSync } from "node:child_process";
import process from "node:process";

const PS_TIMEOUT_MS = 2000;
const SIGTERM_GRACE_MS = 250;
const SIGKILL_GRACE_MS = 750;
const WINDOWS_KILL_GRACE_MS = 1000;

export function captureProcessIdentity(pid) {
  const numericPid = Number(pid);
  if (!Number.isInteger(numericPid) || numericPid <= 0) {
    return { pid: 0, ppid: 0, command: "" };
  }
  if (process.platform === "win32") {
    const script = [
      `$p = Get-CimInstance Win32_Process -Filter "ProcessId = ${numericPid}"`,
      "if ($null -eq $p) { exit 1 }",
      "$p | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    ].join("; ");
    const result = spawnSync("powershell.exe", ["-NoProfile", "-Command", script], {
      encoding: "utf8",
      timeout: PS_TIMEOUT_MS,
      killSignal: "SIGKILL"
    });
    if (result.error || result.status !== 0) {
      return { pid: numericPid, ppid: 0, command: "" };
    }
    try {
      const payload = JSON.parse(String(result.stdout || "").trim());
      return {
        pid: Number(payload.ProcessId || numericPid),
        ppid: Number(payload.ParentProcessId || 0),
        command: String(payload.CommandLine || "")
      };
    } catch {
      return { pid: numericPid, ppid: 0, command: "" };
    }
  }
  const result = spawnSync("ps", ["-p", String(numericPid), "-o", "pid=,ppid=,command="], {
    encoding: "utf8",
    timeout: PS_TIMEOUT_MS,
    killSignal: "SIGKILL"
  });
  if (result.error || result.status !== 0) {
    return { pid: numericPid, ppid: 0, command: "" };
  }
  const line = String(result.stdout || "").trim();
  const match = line.match(/^(\d+)\s+(\d+)\s+(.*)$/s);
  if (!match) {
    return { pid: numericPid, ppid: 0, command: "" };
  }
  return {
    pid: Number(match[1]),
    ppid: Number(match[2]),
    command: match[3] || ""
  };
}

function sleepMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
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

function waitForProcessGroupExit(pid, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!processGroupExists(pid)) {
      return true;
    }
    sleepMs(25);
  }
  return !processGroupExists(pid);
}

function waitForProcessExit(pid, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!processExists(pid)) {
      return true;
    }
    sleepMs(25);
  }
  return !processExists(pid);
}

function validateWorkerIdentity(current, expectedIdentity = {}, suffix = "") {
  if (!current.command) {
    return { ok: false, status: "not_running" };
  }
  if (!current.command.includes("antigravity-companion.mjs")) {
    return {
      ok: false,
      status: "failed",
      error: `process identity mismatch${suffix}: pid is not an Antigravity companion worker`,
      current
    };
  }
  if (expectedIdentity?.pid && current.pid !== expectedIdentity.pid) {
    return {
      ok: false,
      status: "failed",
      error: `process identity mismatch${suffix}: pid changed`,
      current,
      expected: expectedIdentity
    };
  }
  if (expectedIdentity?.command && current.command !== expectedIdentity.command) {
    return {
      ok: false,
      status: "failed",
      error: `process identity mismatch${suffix}: command changed`,
      current,
      expected: expectedIdentity
    };
  }
  return { ok: true };
}

export function terminateValidatedJobWorker(pid, expectedIdentity = {}) {
  const numericPid = Number(pid);
  if (!Number.isInteger(numericPid) || numericPid <= 0) {
    return { status: "not_running" };
  }

  let current;
  try {
    current = captureProcessIdentity(numericPid);
  } catch (error) {
    if (error?.code === "ESRCH") {
      return { status: "not_running" };
    }
    return { status: "failed", error: error.message || String(error) };
  }

  const initialValidation = validateWorkerIdentity(current, expectedIdentity);
  if (!initialValidation.ok) {
    return initialValidation.status === "not_running"
      ? { status: "not_running" }
      : initialValidation;
  }

  if (process.platform === "win32") {
    const result = spawnSync("taskkill.exe", ["/PID", String(numericPid), "/T", "/F"], {
      encoding: "utf8",
      timeout: PS_TIMEOUT_MS,
      killSignal: "SIGKILL"
    });
    if (result.error || result.status !== 0) {
      return {
        status: "failed",
        error: result.error ? String(result.error.message || result.error) : String(result.stderr || "taskkill failed").trim(),
        current
      };
    }
    if (waitForProcessExit(numericPid, WINDOWS_KILL_GRACE_MS)) {
      return { status: "terminated", signal: "TASKKILL", current };
    }
    return {
      status: "failed",
      error: "process remained running after taskkill",
      current,
      expected: expectedIdentity
    };
  }

  try {
    process.kill(-numericPid, "SIGTERM");
  } catch (error) {
    if (error?.code === "ESRCH") {
      return { status: "not_running" };
    }
    return { status: "failed", error: error.message || String(error), current };
  }

  if (waitForProcessGroupExit(numericPid, SIGTERM_GRACE_MS)) {
    return { status: "terminated", signal: "SIGTERM", current };
  }

  const afterSigterm = captureProcessIdentity(numericPid);
  if (afterSigterm.command) {
    const sigtermValidation = validateWorkerIdentity(afterSigterm, expectedIdentity, " after SIGTERM");
    if (!sigtermValidation.ok && sigtermValidation.status !== "not_running") {
      return sigtermValidation;
    }
  }

  try {
    process.kill(-numericPid, "SIGKILL");
  } catch (error) {
    if (error?.code === "ESRCH") {
      return { status: "terminated", signal: "SIGTERM", current };
    }
    return { status: "failed", error: error.message || String(error), current };
  }

  if (waitForProcessGroupExit(numericPid, SIGKILL_GRACE_MS)) {
    return { status: "terminated", signal: "SIGKILL", current };
  }
  return {
    status: "failed",
    error: "process group remained running after SIGKILL",
    current,
    expected: expectedIdentity
  };
}
