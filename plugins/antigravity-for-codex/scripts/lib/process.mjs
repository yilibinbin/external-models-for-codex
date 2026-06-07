import { spawnSync } from "node:child_process";
import process from "node:process";

const PS_TIMEOUT_MS = 2000;
const SIGTERM_GRACE_MS = 250;
const SIGKILL_GRACE_MS = 750;

export function captureProcessIdentity(pid) {
  const numericPid = Number(pid);
  if (!Number.isInteger(numericPid) || numericPid <= 0) {
    return { pid: 0, ppid: 0, command: "" };
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

  if (!current.command) {
    return { status: "not_running" };
  }
  if (!current.command.includes("antigravity-companion.mjs")) {
    return {
      status: "failed",
      error: "process identity mismatch: pid is not an Antigravity companion worker",
      current
    };
  }
  if (expectedIdentity?.pid && current.pid !== expectedIdentity.pid) {
    return {
      status: "failed",
      error: "process identity mismatch: pid changed",
      current,
      expected: expectedIdentity
    };
  }
  if (expectedIdentity?.ppid && current.ppid !== expectedIdentity.ppid) {
    return {
      status: "failed",
      error: "process identity mismatch: ppid changed",
      current,
      expected: expectedIdentity
    };
  }
  if (expectedIdentity?.command && current.command !== expectedIdentity.command) {
    return {
      status: "failed",
      error: "process identity mismatch: command changed",
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
    if (!afterSigterm.command.includes("antigravity-companion.mjs")) {
      return {
        status: "failed",
        error: "process identity mismatch after SIGTERM",
        current: afterSigterm,
        expected: expectedIdentity
      };
    }
    if (expectedIdentity?.pid && afterSigterm.pid !== expectedIdentity.pid) {
      return {
        status: "failed",
        error: "process identity mismatch after SIGTERM: pid changed",
        current: afterSigterm,
        expected: expectedIdentity
      };
    }
    if (expectedIdentity?.ppid && afterSigterm.ppid !== expectedIdentity.ppid) {
      return {
        status: "failed",
        error: "process identity mismatch after SIGTERM: ppid changed",
        current: afterSigterm,
        expected: expectedIdentity
      };
    }
    if (expectedIdentity?.command && afterSigterm.command !== expectedIdentity.command) {
      return {
        status: "failed",
        error: "process identity mismatch after SIGTERM: command changed",
        current: afterSigterm,
        expected: expectedIdentity
      };
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
