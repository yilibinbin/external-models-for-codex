import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { stateDirForCwd } from "./state.mjs";
import { hasTrustedExpectedIdentity, terminateValidatedCompanionChild, terminateValidatedJobWorker } from "./process.mjs";
import { classifyJobLiveness } from "./job-lifecycle.mjs";

export const TERMINAL_JOB_STATUSES = new Set(["succeeded", "failed", "cancelled", "cancel_failed"]);
export const RESERVABLE_COMMANDS = new Set(["review", "adversarial-review", "multi-review", "plan", "rescue"]);

const OUTPUT_CAP_BYTES = 256 * 1024;
const TRUNCATION_MARKER = `\n[output truncated to ${OUTPUT_CAP_BYTES} bytes]`;
const JOB_LOCK_WAIT_MS = 1000;
const JOB_LOCK_STALE_MS = 30_000;

function jobsDir(cwd = process.cwd(), env = process.env) {
  return path.join(stateDirForCwd(cwd, env), "jobs");
}

function ensureJobsDir(cwd = process.cwd(), env = process.env) {
  const dir = jobsDir(cwd, env);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function jobPath(jobId, cwd = process.cwd(), env = process.env) {
  const id = String(jobId || "");
  if (!/^[a-zA-Z0-9._-]+$/.test(id)) {
    throw new Error("Invalid job id.");
  }
  return path.join(ensureJobsDir(cwd, env), `${id}.json`);
}

function jobLockPath(jobId, cwd = process.cwd(), env = process.env) {
  return `${jobPath(jobId, cwd, env)}.lock`;
}

function sleepMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function lockStaleMs(env = process.env) {
  const value = Number(env.ANTIGRAVITY_FOR_CODEX_JOB_LOCK_STALE_MS);
  return Number.isFinite(value) && value >= 0 ? value : JOB_LOCK_STALE_MS;
}

function acquireJobLock(jobId, cwd = process.cwd(), env = process.env, waitMs = JOB_LOCK_WAIT_MS) {
  const lockFile = jobLockPath(jobId, cwd, env);
  const deadline = Date.now() + waitMs;
  while (Date.now() <= deadline) {
    try {
      const handle = fs.openSync(lockFile, "wx");
      fs.writeFileSync(handle, JSON.stringify({ pid: process.pid, createdAt: now() }));
      return { handle, lockFile };
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      try {
        const stat = fs.statSync(lockFile);
        if (Date.now() - stat.mtimeMs > lockStaleMs(env)) {
          const staleFile = `${lockFile}.stale-${process.pid}-${randomBytes(4).toString("hex")}`;
          fs.renameSync(lockFile, staleFile);
          fs.unlinkSync(staleFile);
          continue;
        }
      } catch (statError) {
        if (statError.code !== "ENOENT") throw statError;
        continue;
      }
      if (Date.now() >= deadline) {
        return null;
      }
      sleepMs(25);
    }
  }
  return null;
}

function releaseJobLock(lock) {
  if (!lock) return;
  fs.closeSync(lock.handle);
  try {
    fs.unlinkSync(lock.lockFile);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

function withJobLock(jobId, cwd = process.cwd(), env = process.env, callback) {
  const lock = acquireJobLock(jobId, cwd, env);
  if (!lock) return null;
  try {
    return callback();
  } finally {
    releaseJobLock(lock);
  }
}

function now() {
  return new Date().toISOString();
}

function newJobId() {
  return `agy-${Date.now().toString(36)}-${randomBytes(6).toString("hex")}`;
}

function writeJob(job, cwd = process.cwd(), env = process.env) {
  const file = jobPath(job.id, cwd, env);
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(job, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, file);
  return job;
}

function capText(value) {
  const text = String(value || "").trimEnd();
  const bytes = Buffer.from(text, "utf8");
  if (bytes.length < OUTPUT_CAP_BYTES) {
    return text;
  }
  const markerBytes = Buffer.byteLength(TRUNCATION_MARKER, "utf8");
  return `${bytes.subarray(0, OUTPUT_CAP_BYTES - markerBytes).toString("utf8")}${TRUNCATION_MARKER}`;
}

function positiveIntegerOrNull(value) {
  const numericValue = Number(value);
  return Number.isInteger(numericValue) && numericValue > 0 ? numericValue : null;
}

function recordedWorkerPid(job) {
  return positiveIntegerOrNull(job?.worker?.pid)
    ?? positiveIntegerOrNull(job?.workerPid)
    ?? positiveIntegerOrNull(job?.worker?.identity?.pid);
}

function requiresTrustedWorkerIdentityForCancellation(job) {
  const status = String(job?.status || "");
  const submissionState = String(job?.submissionState || "");
  const hasRecordedWorker = Boolean(recordedWorkerPid(job));
  return ["starting", "running"].includes(status)
    || ["starting", "running"].includes(submissionState)
    || (status === "queued" && hasRecordedWorker);
}

function isMetadataOnlyCancellable(job) {
  const status = String(job?.status || "");
  const submissionState = String(job?.submissionState || "");
  const hasRecordedWorker = Boolean(recordedWorkerPid(job));
  return (status === "reserved" && !hasRecordedWorker && !["starting", "running"].includes(submissionState))
    || (status === "queued" && !hasRecordedWorker && !["starting", "running"].includes(submissionState));
}

function missingTrustedWorkerIdentityResult(expected) {
  return {
    status: "failed",
    error: "missing trusted worker identity",
    phase: "initial",
    diagnostic: { expected: expected || null }
  };
}

function workerPidIdentityMismatchResult(workerPid, expected) {
  return {
    status: "failed",
    error: "worker pid does not match trusted identity",
    phase: "initial",
    diagnostic: {
      workerPid,
      expected: expected || null
    }
  };
}

function terminateRecordedTrustedWorker(worker, env = process.env, terminator = terminateValidatedJobWorker) {
  if (!worker) return null;
  const expected = worker.identity;
  const workerPid = positiveIntegerOrNull(worker.pid) ?? positiveIntegerOrNull(expected?.pid);
  if (!workerPid) return null;
  const expectedPid = positiveIntegerOrNull(expected?.pid);
  if (expectedPid && workerPid !== expectedPid) {
    return workerPidIdentityMismatchResult(workerPid, expected);
  }
  return terminator(workerPid, expected, env);
}

export function createJob({ command, args = [], cwd = process.cwd() }, env = process.env) {
  const createdAt = now();
  const job = {
    id: newJobId(),
    command,
    args,
    cwd,
    status: "queued",
    viewed: false,
    createdAt,
    updatedAt: createdAt,
    startedAt: "",
    endedAt: "",
    worker: null,
    idempotencyKey: "",
    workspaceFingerprint: "",
    executionControls: {},
    timeout: null,
    workerPid: null,
    lastHeartbeatAt: "",
    lastProgressAt: "",
    resultViewedAt: "",
    submissionState: "created",
    stdout: "",
    stderr: "",
    error: ""
  };
  return writeJob(job, cwd, env);
}

export function updateJob(jobId, updater, cwd = process.cwd(), env = process.env) {
  if (env.ANTIGRAVITY_FOR_CODEX_TEST_UPDATE_JOB_FAILURE === "1") {
    return null;
  }
  return withJobLock(jobId, cwd, env, () => {
    const job = readJob(jobId, cwd, env);
    if (!job) return null;
    const updated = updater(job) || job;
    return writeJob(updated, cwd, env);
  });
}

export function findReusableJob({ command, args = [], cwd = process.cwd(), idempotencyKey = "" }, env = process.env) {
  if (!idempotencyKey) return null;
  const expectedArgs = JSON.stringify(args.map(String));
  return listJobs(cwd, env).find((job) => {
    const liveness = classifyJobLiveness(job, { now: Date.now(), env });
    return job.command === command
      && job.cwd === cwd
      && job.idempotencyKey === idempotencyKey
      && JSON.stringify((job.args || []).map(String)) === expectedArgs
      && (liveness.state === "queued" || liveness.state === "healthy");
  }) || null;
}

export function withWorkspaceJobLock(cwd = process.cwd(), env = process.env, callback) {
  return withJobLock("workspace", cwd, env, callback);
}

export function markJobMetadataPersistenceFailed(jobId, message, cwd = process.cwd(), env = process.env) {
  return withJobLock(jobId, cwd, env, () => {
    const job = readJob(jobId, cwd, env);
    if (!job) return null;
    job.status = "failed";
    job.submissionState = "metadata_failed";
    job.error = capText(message || "Metadata persistence failed before worker start.");
    job.endedAt = now();
    job.updatedAt = job.endedAt;
    return writeJob(job, cwd, env);
  });
}

export function reserveJob({ command, args = [], cwd = process.cwd(), timeout = null }, env = process.env) {
  if (!RESERVABLE_COMMANDS.has(command)) {
    throw new Error(`Command "${command}" cannot be reserved.`);
  }
  const job = createJob({ command, args, cwd }, env);
  job.status = "reserved";
  if (Number.isFinite(timeout) && timeout > 0) {
    job.timeout = timeout;
  }
  job.updatedAt = now();
  return writeJob(job, cwd, env);
}

export function claimReservedJob(cwd = process.cwd(), jobId, env = process.env) {
  return withJobLock(jobId, cwd, env, () => {
    const job = readJob(jobId, cwd, env);
    if (!job || job.status !== "reserved") {
      return null;
    }
    job.status = "queued";
    job.submissionState = "queued";
    job.updatedAt = now();
    return writeJob(job, cwd, env);
  });
}

export function listJobs(cwd = process.cwd(), env = process.env) {
  const dir = ensureJobsDir(cwd, env);
  return fs.readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => {
      try {
        return JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
      } catch {
        return null;
      }
    })
    .filter(Boolean)
    .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
}

export function readJob(jobId, cwd = process.cwd(), env = process.env) {
  const file = jobPath(jobId, cwd, env);
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

export function markJobViewed(jobId, cwd = process.cwd(), env = process.env) {
  return withJobLock(jobId, cwd, env, () => {
    const job = readJob(jobId, cwd, env);
    if (!job) return null;
    job.viewed = true;
    job.resultViewedAt = now();
    job.updatedAt = job.resultViewedAt;
    return writeJob(job, cwd, env);
  });
}

export function markJobRunning(jobId, worker, cwd = process.cwd(), env = process.env) {
  return withJobLock(jobId, cwd, env, () => {
    const job = readJob(jobId, cwd, env);
    if (!job) return null;
    if (TERMINAL_JOB_STATUSES.has(job.status)) {
      return job;
    }
    job.status = "running";
    job.worker = worker;
    job.workerPid = recordedWorkerPid({ worker });
    job.submissionState = "running";
    job.startedAt = job.startedAt || now();
    job.lastHeartbeatAt = job.lastHeartbeatAt || now();
    job.updatedAt = job.lastHeartbeatAt;
    return writeJob(job, cwd, env);
  });
}

export function finishJob(jobId, result, cwd = process.cwd(), env = process.env) {
  return withJobLock(jobId, cwd, env, () => {
    const job = readJob(jobId, cwd, env);
    if (!job) return null;
    if (TERMINAL_JOB_STATUSES.has(job.status)) {
      return job;
    }
    job.status = result.status === 0 ? "succeeded" : "failed";
    job.stdout = capText(result.stdout);
    job.stderr = capText(result.stderr);
    job.error = capText(result.error);
    job.submissionState = "finished";
    job.endedAt = now();
    job.updatedAt = job.endedAt;
    return writeJob(job, cwd, env);
  });
}

export function cancelJob(jobId, cwd = process.cwd(), env = process.env) {
  return withJobLock(jobId, cwd, env, () => {
    const refreshed = readJob(jobId, cwd, env);
    if (!refreshed) return null;
    if (TERMINAL_JOB_STATUSES.has(refreshed.status)) {
      return refreshed;
    }

    const supervisedTermination = terminateRecordedTrustedWorker(refreshed.supervisedWorker, env, terminateValidatedCompanionChild);
    let termination;
    if (isMetadataOnlyCancellable(refreshed)) {
      termination = { status: "not_running" };
    } else {
      const expectedIdentity = refreshed.worker?.identity;
      const workerPid = recordedWorkerPid(refreshed);
      if (requiresTrustedWorkerIdentityForCancellation(refreshed)
        && !hasTrustedExpectedIdentity(expectedIdentity)) {
        termination = missingTrustedWorkerIdentityResult(expectedIdentity);
      } else if (hasTrustedExpectedIdentity(expectedIdentity)
        && workerPid !== Number(expectedIdentity.pid)) {
        termination = workerPidIdentityMismatchResult(workerPid, expectedIdentity);
      } else {
        termination = terminateValidatedJobWorker(workerPid, expectedIdentity, env);
      }
    }
    const failedTermination = [supervisedTermination, termination]
      .filter(Boolean)
      .find((item) => item.status === "failed");
    if (failedTermination) {
      refreshed.status = "cancel_failed";
      refreshed.error = capText(failedTermination.error || "Failed to cancel job.");
    } else {
      refreshed.status = "cancelled";
    }
    refreshed.submissionState = "finished";
    refreshed.cancel = supervisedTermination ? { worker: termination, supervisedWorker: supervisedTermination } : termination;
    refreshed.endedAt = now();
    refreshed.updatedAt = refreshed.endedAt;
    return writeJob(refreshed, cwd, env);
  });
}
