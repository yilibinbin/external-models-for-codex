import { spawnSync } from "node:child_process";
import process from "node:process";

const PS_TIMEOUT_MS = 2000;

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
    return { status: "terminated", current };
  } catch (error) {
    if (error?.code === "ESRCH") {
      return { status: "not_running" };
    }
    return { status: "failed", error: error.message || String(error), current };
  }
}
