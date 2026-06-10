import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import process from "node:process";

const DEFAULT_PS_TIMEOUT_MS = 2_000;
const DEFAULT_PS_MAX_BUFFER_BYTES = 20 * 1024 * 1024;
const psProbeState = {
  groupScanFailures: 0,
  lastGroupScanFailure: null
};

export function supportsPosixProcessGroups(platform = process.platform) {
  return platform !== "win32";
}

export function currentProcessPlatform(env = process.env) {
  return env.CLAUDE_FOR_CODEX_PROCESS_PLATFORM || process.platform;
}

function parseBoundedInteger(value, fallback, { min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(min, Math.min(Math.trunc(parsed), max));
}

function psProbeTimeoutMs(env = process.env) {
  return parseBoundedInteger(env.CLAUDE_FOR_CODEX_PS_TIMEOUT_MS, DEFAULT_PS_TIMEOUT_MS, { min: 1, max: 30_000 });
}

function psProbeMaxBufferBytes(env = process.env) {
  return parseBoundedInteger(env.CLAUDE_FOR_CODEX_PS_MAX_BUFFER_BYTES, DEFAULT_PS_MAX_BUFFER_BYTES, { min: 1_024, max: 100 * 1024 * 1024 });
}

function runPs(args) {
  return spawnSync("ps", args, {
    encoding: "utf8",
    timeout: psProbeTimeoutMs(),
    killSignal: "SIGKILL",
    maxBuffer: psProbeMaxBufferBytes()
  });
}

function psProbeFailure(result) {
  if (result.error || result.errorCode || result.signal || result.status === null || result.status === undefined) {
    return {
      status: result.status ?? null,
      signal: result.signal ?? "",
      error: result.error ? String(result.error.message ?? result.error) : "",
      errorCode: result.error?.code ? String(result.error.code) : result.errorCode ? String(result.errorCode) : "",
      stderr: String(result.stderr ?? "").slice(0, 500)
    };
  }
  return null;
}

function recordGroupScanProbeFailure(pgid, args, result, reason) {
  const diagnostic = {
    pgid,
    args,
    reason,
    status: result.status ?? null,
    signal: result.signal ?? "",
    error: result.error ? String(result.error.message ?? result.error) : "",
    errorCode: result.error?.code ? String(result.error.code) : result.errorCode ? String(result.errorCode) : "",
    stderr: String(result.stderr ?? "").slice(0, 500),
    timeoutMs: psProbeTimeoutMs(),
    maxBufferBytes: psProbeMaxBufferBytes()
  };
  psProbeState.groupScanFailures += 1;
  psProbeState.lastGroupScanFailure = diagnostic;
  return diagnostic;
}

export function psProbeDiagnostics() {
  return {
    groupScanFailures: psProbeState.groupScanFailures,
    lastGroupScanFailure: psProbeState.lastGroupScanFailure ? { ...psProbeState.lastGroupScanFailure } : null
  };
}

export function resetPsProbeDiagnostics() {
  psProbeState.groupScanFailures = 0;
  psProbeState.lastGroupScanFailure = null;
}

function ps(pid) {
  const result = runPs(["-p", String(pid), "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "stat=", "-o", "command="]);
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
    command: match[5],
    commandHash: commandHash(match[5])
  };
}

export function captureProcessIdentity(pid) {
  return ps(pid);
}

function commandHash(command) {
  return createHash("sha256").update(String(command ?? "")).digest("hex");
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

export function processGroupLiveness(pgid) {
  if (!Number.isInteger(pgid)) {
    return { live: false, inconclusive: false, diagnostic: null };
  }
  const args = ["-eo", "pid=", "-o", "pgid=", "-o", "stat="];
  const result = runPs(args);
  const failure = psProbeFailure(result);
  if (failure || result.status !== 0 || !result.stdout.trim()) {
    const diagnostic = recordGroupScanProbeFailure(
      pgid,
      args,
      result,
      failure ? "ps probe failed" : "ps probe returned no process rows"
    );
    return { live: true, inconclusive: true, diagnostic };
  }
  const live = result.stdout.split(/\r?\n/).some((line) => {
    const match = line.trim().match(/^(\d+)\s+(\d+)\s+(\S+)/);
    if (!match) {
      return false;
    }
    return Number(match[2]) === pgid && !String(match[3]).startsWith("Z");
  });
  return { live, inconclusive: false, diagnostic: null };
}

export function processGroupHasLiveMembers(pgid) {
  return processGroupLiveness(pgid).live;
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
  if (current === expected) {
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

function processIdentityCommandMatches(currentIdentity, expectedIdentity) {
  if (currentIdentity?.commandHash && expectedIdentity?.commandHash) {
    return currentIdentity.commandHash === expectedIdentity.commandHash;
  }
  return commandIdentityMatches(currentIdentity?.command, expectedIdentity?.command);
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
    !processIdentityCommandMatches(identity, expectedIdentity)
  ) {
    return { ok: false, reason: "process identity changed; refusing to signal possible PID reuse", identity };
  }
  return { ok: true, identity, signalPid: -pid };
}

function leaderlessProcessGroupRefusal(pid, expectedIdentity) {
  return {
    ok: false,
    delivered: false,
    reason: "process-group leader is absent but live members remain; refusing to signal leaderless process group without member identity validation",
    identity: expectedIdentity,
    leaderless: true,
    signalPid: -pid
  };
}

export function terminateValidatedProcessGroup(pid, expectedIdentity, options = {}) {
  let validation = validateProcessGroupLeader(pid, expectedIdentity);
  let lastGroupLiveness = null;
  let groupStillAlive = () => {
    if (validateProcessGroupLeader(pid, expectedIdentity).ok) {
      lastGroupLiveness = null;
      return true;
    }
    lastGroupLiveness = processGroupLiveness(pid);
    return lastGroupLiveness.live;
  };
  if (!validation.ok) {
    if (validation.reason === "process not found") {
      if (expectedIdentity && processGroupHasLiveMembers(pid)) {
        return leaderlessProcessGroupRefusal(pid, expectedIdentity);
      } else {
        return { ok: true, delivered: false, reason: "process already absent" };
      }
    } else {
      return validation;
    }
  }
  const graceMs = parseGraceMs(options.killGraceMs ?? process.env.CLAUDE_FOR_CODEX_KILL_GRACE_MS);
  let escalated = false;
  let deliveredToValidatedGroup = false;
  const revalidateForSignal = () => {
    const current = validateProcessGroupLeader(pid, expectedIdentity);
    if (current.ok) {
      return current;
    }
    if (current.reason === "process not found" && deliveredToValidatedGroup && processGroupHasLiveMembers(pid)) {
      return { ok: true, identity: expectedIdentity, signalPid: -pid, leaderlessAfterValidatedSignal: true };
    }
    if (current.reason === "process not found" && expectedIdentity && processGroupHasLiveMembers(pid)) {
      return leaderlessProcessGroupRefusal(pid, expectedIdentity);
    }
    return current;
  };
  try {
    process.kill(validation.signalPid, "SIGTERM");
    deliveredToValidatedGroup = true;
    const deadline = Date.now() + graceMs;
    while (Date.now() < deadline && groupStillAlive()) {
      sleepSync(25);
    }
    if (groupStillAlive()) {
      const refreshed = revalidateForSignal();
      if (!refreshed.ok) {
        if (refreshed.reason === "process already absent") {
          return { ok: true, delivered: true, escalated, identity: validation.identity };
        }
        return { ok: false, delivered: true, escalated, reason: refreshed.reason, identity: refreshed.identity ?? validation.identity };
      }
      validation = refreshed;
      escalated = true;
      try {
        process.kill(validation.signalPid, "SIGKILL");
      } catch (error) {
        if (error?.code !== "ESRCH") {
          throw error;
        }
      }
    }
    const killDeadline = Date.now() + 1_000;
    while (Date.now() < killDeadline && groupStillAlive()) {
      sleepSync(25);
    }
    const stillAlive = groupStillAlive();
    return {
      ok: !stillAlive,
      delivered: true,
      escalated,
      reason: stillAlive
        ? lastGroupLiveness?.inconclusive
          ? "process group liveness probe inconclusive after SIGKILL; preserving capacity"
          : "process group still alive after SIGKILL"
        : undefined,
      identity: validation.identity
    };
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
  let validation = validateJobWorkerProcess(pid, jobId);
  if (!validation.ok) {
    if (validation.reason === "process not found") {
      return { ok: true, delivered: false, reason: "worker process already absent" };
    }
    return validation;
  }
  const graceMs = parseGraceMs(options.killGraceMs ?? process.env.CLAUDE_FOR_CODEX_KILL_GRACE_MS);
  let escalated = false;
  const workerStillAlive = () => validateJobWorkerProcess(pid, jobId).ok;
  try {
    process.kill(validation.signalPid, "SIGTERM");
    const deadline = Date.now() + graceMs;
    while (Date.now() < deadline && workerStillAlive()) {
      sleepSync(25);
    }
    if (workerStillAlive()) {
      const refreshed = validateJobWorkerProcess(pid, jobId);
      if (!refreshed.ok) {
        if (refreshed.reason === "process not found") {
          return { ok: true, delivered: true, escalated, identity: validation.identity };
        }
        return { ok: false, delivered: true, escalated, reason: refreshed.reason, identity: refreshed.identity ?? validation.identity };
      }
      validation = refreshed;
      escalated = true;
      try {
        process.kill(validation.signalPid, "SIGKILL");
      } catch (error) {
        if (error?.code !== "ESRCH") {
          throw error;
        }
      }
      const killDeadline = Date.now() + 1_000;
      while (Date.now() < killDeadline && workerStillAlive()) {
        sleepSync(25);
      }
    }
    const stillAlive = workerStillAlive();
    return {
      ok: !stillAlive,
      delivered: true,
      escalated,
      reason: stillAlive ? "worker process still alive after SIGKILL" : undefined,
      identity: validation.identity
    };
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
