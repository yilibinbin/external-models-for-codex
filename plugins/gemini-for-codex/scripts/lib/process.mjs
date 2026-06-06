import { spawnSync } from "node:child_process";
import process from "node:process";

function ps(pid) {
  const result = spawnSync("ps", ["-p", String(pid), "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "command="], {
    encoding: "utf8",
    timeout: 5000,
    killSignal: "SIGKILL"
  });
  if (result.status !== 0 || !result.stdout.trim()) {
    return null;
  }
  const line = result.stdout.trim();
  const match = line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+([\s\S]*)$/);
  if (!match) {
    return null;
  }
  return {
    pid: Number(match[1]),
    ppid: Number(match[2]),
    pgid: Number(match[3]),
    command: match[4]
  };
}

export function captureProcessIdentity(pid) {
  return ps(pid);
}

function commandTokens(command) {
  return String(command ?? "")
    .split(/\s+/)
    .filter(Boolean);
}

function tokenAfter(tokens, token) {
  const index = tokens.indexOf(token);
  return index >= 0 ? tokens[index + 1] : undefined;
}

export function reservedJobIdFromCommandTokens(tokens) {
  const commandIndex = tokens.indexOf("run-reserved-job");
  if (commandIndex < 0) {
    return undefined;
  }
  const jobIdIndex = tokens.indexOf("--job-id", commandIndex + 1);
  return jobIdIndex >= 0 ? tokens[jobIdIndex + 1] : undefined;
}

export function validateJobWorkerProcess(pid, jobId) {
  const identity = captureProcessIdentity(pid);
  if (!identity) {
    return { ok: false, reason: "process not found" };
  }
  if (!identity.command.includes("gemini-companion.mjs")) {
    return { ok: false, reason: "process command is not a Gemini for Codex job worker", identity };
  }
  const tokens = commandTokens(identity.command);
  if (tokens.includes("__run-job")) {
    if (tokenAfter(tokens, "__run-job") !== String(jobId)) {
      return { ok: false, reason: "process command does not match the requested job id", identity };
    }
    if (identity.pgid !== identity.pid) {
      return { ok: false, reason: "worker is not the process-group leader", identity };
    }
    return { ok: true, identity, signalPid: -pid };
  }
  if (tokens.includes("run-reserved-job")) {
    if (reservedJobIdFromCommandTokens(tokens) !== String(jobId)) {
      return { ok: false, reason: "process command does not match the requested reserved job id", identity };
    }
    return { ok: true, identity, signalPid: pid };
  }
  return { ok: false, reason: "process command is not a Gemini for Codex job worker", identity };
}

export function terminateValidatedJobWorker(pid, jobId) {
  const validation = validateJobWorkerProcess(pid, jobId);
  if (!validation.ok) {
    return validation;
  }
  try {
    process.kill(validation.signalPid, "SIGTERM");
    return { ok: true, identity: validation.identity };
  } catch (error) {
    return {
      ok: false,
      reason: error.message || String(error),
      identity: validation.identity
    };
  }
}
