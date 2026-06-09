import { spawnSync } from "node:child_process";
import process from "node:process";

function ps(pid) {
  const result = spawnSync("ps", ["-p", String(pid), "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "stat=", "-o", "command="], {
    encoding: "utf8"
  });
  if (result.status !== 0 || !result.stdout.trim()) {
    return null;
  }
  const line = result.stdout.trim();
  const match = line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+([\s\S]*)$/);
  if (!match) {
    return null;
  }
  if (String(match[4]).startsWith("Z")) {
    return null;
  }
  return {
    pid: Number(match[1]),
    ppid: Number(match[2]),
    pgid: Number(match[3]),
    stat: match[4],
    command: match[5]
  };
}

export function captureProcessIdentity(pid) {
  return ps(pid);
}

function sleepSync(ms) {
  const view = new Int32Array(new SharedArrayBuffer(4));
  Atomics.wait(view, 0, 0, ms);
}

function parseGraceMs(value, fallback = 3_000) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.min(Math.trunc(parsed), 30_000) : fallback;
}

export function isProcessAlive(pid) {
  return Number.isInteger(pid) && Boolean(captureProcessIdentity(pid));
}

function processGroupHasLiveMembers(pgid) {
  if (!Number.isInteger(pgid)) {
    return false;
  }
  const result = spawnSync("ps", ["-axo", "pid=", "-o", "pgid=", "-o", "stat="], {
    encoding: "utf8"
  });
  if (result.status !== 0 || !result.stdout.trim()) {
    return false;
  }
  return result.stdout.split(/\r?\n/).some((line) => {
    const match = line.trim().match(/^(\d+)\s+(\d+)\s+(\S+)/);
    if (!match) {
      return false;
    }
    return Number(match[2]) === pgid && !String(match[3]).startsWith("Z");
  });
}

export function captureProcessGroupIdentity(pid, options = {}) {
  const deadline = Date.now() + (options.timeoutMs ?? 1_000);
  while (Date.now() <= deadline) {
    const identity = captureProcessIdentity(pid);
    if (!identity) {
      return null;
    }
    if (identity.pid === pid && identity.pgid === pid) {
      return identity;
    }
    sleepSync(25);
  }
  return null;
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
  if (!identity.command.includes("claude-companion.mjs")) {
    return { ok: false, reason: "process command is not a Claude for Codex job worker", identity };
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
  return { ok: false, reason: "process command is not a Claude for Codex job worker", identity };
}

function commandIdentityMatches(currentCommand, expectedCommand) {
  const current = String(currentCommand ?? "");
  const expected = String(expectedCommand ?? "");
  if (current === expected || current.includes(expected) || expected.includes(current)) {
    return true;
  }
  if (expected.includes("<redacted-")) {
    const suffixes = expected
      .split(/<redacted-(?:home|workspace|path)>/g)
      .slice(1)
      .filter((suffix) => suffix.length >= 3);
    if (suffixes.length && suffixes.every((suffix) => current.includes(suffix))) {
      return true;
    }
    const stableTokens = expected
      .split(/\s+/)
      .filter((token) => token && !token.includes("<redacted-"));
    return stableTokens.length > 0 && stableTokens.every((token) => current.includes(token));
  }
  return false;
}

export function validateProcessGroupLeader(pid, expectedIdentity) {
  if (!expectedIdentity) {
    return { ok: false, reason: "missing saved process identity; refusing to signal possible PID reuse" };
  }
  const identity = captureProcessIdentity(pid);
  if (!identity) {
    return { ok: false, reason: "process not found" };
  }
  if (identity.pid !== pid || identity.pgid !== pid) {
    return { ok: false, reason: "process is not the expected process-group leader", identity };
  }
  if (
    identity.pid !== expectedIdentity.pid ||
    identity.pgid !== expectedIdentity.pgid ||
    !commandIdentityMatches(identity.command, expectedIdentity.command)
  ) {
    return { ok: false, reason: "process identity changed; refusing to signal possible PID reuse", identity };
  }
  return { ok: true, identity, signalPid: -pid };
}

export function terminateValidatedProcessGroup(pid, expectedIdentity, options = {}) {
  let validation = validateProcessGroupLeader(pid, expectedIdentity);
  let groupStillAlive = () => validateProcessGroupLeader(pid, expectedIdentity).ok;
  if (!validation.ok) {
    if (validation.reason === "process not found") {
      if (expectedIdentity && processGroupHasLiveMembers(pid)) {
        validation = {
          ok: true,
          identity: expectedIdentity,
          signalPid: -pid,
          leaderless: true
        };
        groupStillAlive = () => processGroupHasLiveMembers(pid);
      } else {
        return { ok: true, delivered: false, reason: "process already absent" };
      }
    } else {
      return validation;
    }
  }
  const graceMs = parseGraceMs(options.killGraceMs ?? process.env.CLAUDE_FOR_CODEX_KILL_GRACE_MS);
  let escalated = false;
  try {
    process.kill(validation.signalPid, "SIGTERM");
    const deadline = Date.now() + graceMs;
    while (Date.now() < deadline && groupStillAlive()) {
      sleepSync(25);
    }
    escalated = true;
    try {
      process.kill(validation.signalPid, "SIGKILL");
    } catch (error) {
      if (error?.code !== "ESRCH") {
        throw error;
      }
    }
    const killDeadline = Date.now() + 1_000;
    while (Date.now() < killDeadline && groupStillAlive()) {
      sleepSync(25);
    }
    return { ok: true, delivered: true, escalated, identity: validation.identity };
  } catch (error) {
    return {
      ok: false,
      delivered: false,
      escalated,
      reason: error.message || String(error),
      identity: validation.identity
    };
  }
}

export function terminateValidatedJobWorker(pid, jobId, options = {}) {
  const validation = validateJobWorkerProcess(pid, jobId);
  if (!validation.ok) {
    if (validation.reason === "process not found") {
      return { ok: true, delivered: false, reason: "worker process already absent" };
    }
    return validation;
  }
  const graceMs = parseGraceMs(options.killGraceMs ?? process.env.CLAUDE_FOR_CODEX_KILL_GRACE_MS);
  let escalated = false;
  try {
    process.kill(validation.signalPid, "SIGTERM");
    const deadline = Date.now() + graceMs;
    while (Date.now() < deadline && isProcessAlive(pid)) {
      sleepSync(25);
    }
    if (isProcessAlive(pid)) {
      escalated = true;
      process.kill(validation.signalPid, "SIGKILL");
      const killDeadline = Date.now() + 1_000;
      while (Date.now() < killDeadline && isProcessAlive(pid)) {
        sleepSync(25);
      }
    }
    return { ok: !isProcessAlive(pid), delivered: true, escalated, identity: validation.identity };
  } catch (error) {
    return {
      ok: false,
      delivered: false,
      escalated,
      reason: error.message || String(error),
      identity: validation.identity
    };
  }
}
