import fs from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import {
  captureProcessIdentity,
  processGroupHasLiveMembers,
  terminateValidatedJobWorker,
  terminateValidatedProcessGroup,
  validateJobWorkerProcess,
  validateProcessGroupLeader
} from "./process.mjs";
import { jobsDirForCwd, stateDirForCwd } from "./state.mjs";
import {
  DEFAULT_MAX_ACTIVE_JOBS,
  MAX_STORED_OUTPUT_BYTES,
  classifyJobLiveness,
  deriveJobIdempotencyKey,
  isTerminalJobStatus,
  queuedLostAfterMs
} from "./job-lifecycle.mjs";
import { sanitizeSummary } from "./sanitize.mjs";

const JOB_ID_PATTERN = /^[A-Za-z0-9._-]+$/;
const LOCK_STALE_AFTER_MS = 30_000;
const LOCK_RETRY_UNTIL_MS = 2_000;
const SANITIZED_JOB_STRING_FIELDS = Object.freeze({
  stdout: MAX_STORED_OUTPUT_BYTES,
  stderr: MAX_STORED_OUTPUT_BYTES,
  error: 4096,
  reason: 1024,
  stateError: 1024,
  detail: 1024,
  failureReason: 1024,
  phase: 80,
  lastProgressMessage: 512,
  lastProgressRole: 80,
  cancelFailureReason: 1024,
  message: 512
});

function ensureJobsDir(cwd = process.cwd(), env = process.env) {
  const dir = jobsDirForCwd(cwd, env);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  return dir;
}

function jobFile(cwd, jobId, env = process.env) {
  if (!JOB_ID_PATTERN.test(jobId)) {
    throw new Error(`Invalid job id "${jobId}".`);
  }
  return path.join(ensureJobsDir(cwd, env), `${jobId}.json`);
}

function sleepSync(ms) {
  const view = new Int32Array(new SharedArrayBuffer(4));
  Atomics.wait(view, 0, 0, ms);
}

function lockPathForFile(file) {
  return `${file}.lock`;
}

function lockOwner(lockFile) {
  try {
    return JSON.parse(fs.readFileSync(lockFile, "utf8"));
  } catch {
    return null;
  }
}

function removeStaleLock(lockFile) {
  try {
    const stat = fs.statSync(lockFile);
    if (Date.now() - stat.mtimeMs <= LOCK_STALE_AFTER_MS) {
      return false;
    }
    const owner = lockOwner(lockFile);
    const identity = owner?.pid ? captureProcessIdentity(Number(owner.pid)) : null;
    if (identity && lockOwnerMatches(owner, identity)) {
      return false;
    }
    fs.rmSync(lockFile, { force: true });
    return true;
  } catch {
    return false;
  }
}

function lockOwnerMatches(owner, identity) {
  if (!identity) {
    return false;
  }
  if (typeof owner?.commandHash === "string" && owner.commandHash) {
    return identity.commandHash === owner.commandHash;
  }
  if (typeof owner?.command === "string" && owner.command) {
    return identity.command === owner.command;
  }
  return /\bnode(?:\s|$)/.test(identity.command) || identity.command.includes("claude-companion.mjs");
}

function currentLockOwner() {
  const command = process.argv.join(" ");
  return {
    pid: process.pid,
    commandHash: createHash("sha256").update(command).digest("hex"),
    executable: path.basename(process.argv[0] || ""),
    entrypoint: process.argv[1] ? path.basename(process.argv[1]) : "",
    createdAt: new Date().toISOString()
  };
}

function withFileLock(file, operation, options = {}) {
  const lockFile = lockPathForFile(file);
  const deadline = Date.now() + LOCK_RETRY_UNTIL_MS;
  let fd;
  while (fd === undefined) {
    try {
      fd = fs.openSync(lockFile, "wx", 0o600);
      try {
        fs.writeFileSync(fd, `${JSON.stringify(currentLockOwner())}\n`, "utf8");
      } catch (writeError) {
        try {
          fs.closeSync(fd);
        } finally {
          fs.rmSync(lockFile, { force: true });
          fd = undefined;
        }
        throw writeError;
      }
    } catch (error) {
      if (error?.code !== "EEXIST") {
        throw error;
      }
      if (Date.now() >= deadline) {
        removeStaleLock(lockFile);
        return options.onBusy ? options.onBusy() : { status: "locked", reason: "lock busy" };
      }
      if (removeStaleLock(lockFile)) {
        continue;
      }
      sleepSync(25);
    }
  }
  try {
    return operation();
  } finally {
    try {
      fs.closeSync(fd);
    } finally {
      fs.rmSync(lockFile, { force: true });
    }
  }
}

function withJobLock(cwd, jobId, env, operation) {
  return withFileLock(jobFile(cwd, jobId, env), operation, {
    onBusy: () => ({ status: "locked", jobId, reason: "Job state is busy; retry later." })
  });
}

export function withWorkspaceJobLock(cwd = process.cwd(), env = process.env, operation) {
  const dir = ensureJobsDir(cwd, env);
  return withFileLock(path.join(dir, ".workspace.lock"), operation, {
    onBusy: () => ({ status: "workspace_locked", reason: "Workspace job state is busy; retry later." })
  });
}

function readJobFileDirect(file, jobId) {
  if (!fs.existsSync(file)) {
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    return { id: jobId, status: "corrupt", stateError: error.message || String(error) };
  }
}

function sanitizeJobForPersistentWrite(job, cwd) {
  const sanitized = { ...job };
  for (const [field, maxBytes] of Object.entries(SANITIZED_JOB_STRING_FIELDS)) {
    if (sanitized[field] !== undefined) {
      sanitized[field] = sanitizeSummary(sanitized[field], { cwd, maxBytes });
    }
  }
  for (const field of ["pidIdentity", "childProcessGroupIdentity", "cancelIdentity", "cancelChildIdentity", "cancelWorkerIdentity"]) {
    if (sanitized[field]?.command !== undefined) {
      sanitized[field] = {
        ...sanitized[field],
        command: sanitizeSummary(sanitized[field].command, { cwd, maxBytes: 2048 })
      };
    }
  }
  return sanitized;
}

function storedOutput(raw, cwd, metadata = {}) {
  const text = String(raw ?? "");
  const stored = sanitizeSummary(text, { cwd, maxBytes: MAX_STORED_OUTPUT_BYTES });
  const bytes = Number.isFinite(metadata.bytes) && metadata.bytes >= 0
    ? Math.trunc(metadata.bytes)
    : Buffer.byteLength(text, "utf8");
  return {
    text: stored,
    bytes,
    storedBytes: Buffer.byteLength(stored, "utf8"),
    truncated: Boolean(metadata.truncated) || stored.endsWith("...<truncated>")
  };
}

function writeJobFileDirect(file, job, cwd = job.cwd ?? process.cwd()) {
  const tmpFile = `${file}.${process.pid}.tmp`;
  const sanitized = sanitizeJobForPersistentWrite(job, cwd);
  fs.writeFileSync(tmpFile, `${JSON.stringify(sanitized, null, 2)}\n`, "utf8");
  fs.renameSync(tmpFile, file);
  return sanitized;
}

function writeJob(cwd, job, env = process.env) {
  const file = jobFile(cwd, job.id, env);
  return writeJobFileDirect(file, job, cwd);
}

export function createJob(cwd, job, env = process.env) {
  const id = job.id ?? `job-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 8)}`;
  const now = new Date().toISOString();
  const payload = {
    id,
    status: "queued",
    createdAt: now,
    updatedAt: now,
    ...job,
    id
  };
  return writeJob(cwd, payload, env);
}

export function reserveJob(cwd, job, workerCommand, env = process.env) {
  return createJob(cwd, {
    ...job,
    reservationMode: "host-forwarded",
    reservedBy: "codex-host",
    workerCommand
  }, env);
}

function isValidReservedWorkerCommand(job, jobId) {
  if (job.reservationMode !== "host-forwarded") {
    return false;
  }
  if (!Array.isArray(job.workerCommand)) {
    return false;
  }
  const commandIndex = job.workerCommand.indexOf("run-reserved-job");
  if (commandIndex < 0) {
    return false;
  }
  if (!String(job.workerCommand[commandIndex - 1] ?? "").endsWith("claude-companion.mjs")) {
    return false;
  }
  const jobIdFlagIndex = job.workerCommand.indexOf("--job-id", commandIndex + 1);
  return jobIdFlagIndex >= 0 && job.workerCommand[jobIdFlagIndex + 1] === jobId;
}

function sanitizeProgressUpdates(updates, cwd) {
  const sanitized = { ...updates };
  if (sanitized.message !== undefined && sanitized.lastProgressMessage === undefined) {
    sanitized.lastProgressMessage = sanitized.message;
    delete sanitized.message;
  }
  if (sanitized.role !== undefined && sanitized.lastProgressRole === undefined) {
    sanitized.lastProgressRole = sanitized.role;
    delete sanitized.role;
  }
  for (const key of ["phase", "lastProgressMessage", "lastProgressRole"]) {
    if (sanitized[key] !== undefined) {
      sanitized[key] = sanitizeSummary(sanitized[key], { cwd, maxBytes: key === "phase" ? 80 : 512 });
    }
  }
  return sanitized;
}

function claimQueuedJob(cwd, jobId, workerPid, env, validate = () => true) {
  return withJobLock(cwd, jobId, env, () => {
    const file = jobFile(cwd, jobId, env);
    const job = readJobFileDirect(file, jobId);
    if (!job) {
      return { status: "not_found", jobId };
    }
    if (job.status !== "queued") {
      return { status: "not_claimed", jobId, job, reason: `Job is ${job.status}.` };
    }
    const validation = validate(job);
    if (validation !== true) {
      return { status: "not_claimed", jobId, job, reason: validation.reason ?? String(validation) };
    }
    const now = new Date().toISOString();
    const running = writeJobFileDirect(file, {
      ...job,
      status: "running",
      phase: "starting",
      workerPid,
      pidIdentity: captureProcessIdentity(workerPid),
      startedAt: job.startedAt ?? now,
      updatedAt: now,
      lastHeartbeatAt: now,
      heartbeatSeq: Number(job.heartbeatSeq ?? 0),
      submissionState: "in-flight",
      submittedAt: now,
      idempotencyKey: job.idempotencyKey ?? deriveJobIdempotencyKey(job)
    }, cwd);
    return { status: "claimed", job: running };
  });
}

export function claimJobForRun(cwd, jobId, workerPid = process.pid, env = process.env) {
  return claimQueuedJob(cwd, jobId, workerPid, env);
}

export function claimReservedJob(cwd, jobId, workerPid = process.pid, env = process.env) {
  return claimQueuedJob(cwd, jobId, workerPid, env, (job) => (
    isValidReservedWorkerCommand(job, jobId)
      ? true
      : { reason: "Job is not a valid host-forwarded reservation." }
  ));
}

function mutateJobUnderLock(cwd, jobId, env, mutator) {
  return withJobLock(cwd, jobId, env, () => {
    const file = jobFile(cwd, jobId, env);
    const job = readJobFileDirect(file, jobId);
    if (!job) {
      return null;
    }
    const updated = mutator(job);
    if (!updated) {
      return job;
    }
    return writeJobFileDirect(file, { ...updated, updatedAt: new Date().toISOString() }, cwd);
  });
}

export function updateJob(cwd, jobId, updates, env = process.env) {
  return mutateJobUnderLock(cwd, jobId, env, (job) => ({
    ...job,
    ...updates
  }));
}

export function updateJobUnlessTerminal(cwd, jobId, updates, env = process.env) {
  return mutateJobUnderLock(cwd, jobId, env, (job) => {
    if (isTerminalJobStatus(job.status)) {
      return null;
    }
    return { ...job, ...updates };
  });
}

function hasEffectiveCancelRequest(job) {
  if (!job?.cancelRequestedAt) {
    return false;
  }
  if (!job.cancelFailedAt) {
    return true;
  }
  const requestedAt = Date.parse(job.cancelRequestedAt);
  const failedAt = Date.parse(job.cancelFailedAt);
  return Number.isFinite(requestedAt) && Number.isFinite(failedAt) && requestedAt > failedAt;
}

export function recordJobHeartbeat(cwd, jobId, updates = {}, env = process.env) {
  return mutateJobUnderLock(cwd, jobId, env, (job) => {
    if (isTerminalJobStatus(job.status)) {
      return null;
    }
    return {
      ...job,
      ...sanitizeProgressUpdates(updates, cwd),
      heartbeatSeq: Number(job.heartbeatSeq ?? 0) + 1,
      lastHeartbeatAt: new Date().toISOString()
    };
  });
}

export function recordJobProgress(cwd, jobId, updates = {}, env = process.env) {
  return recordJobHeartbeat(cwd, jobId, {
    ...updates,
    lastProgressAt: new Date().toISOString()
  }, env);
}

export function markJobRunning(cwd, jobId, workerPid, env = process.env) {
  return updateJob(cwd, jobId, {
    status: "running",
    phase: "starting",
    workerPid,
    pidIdentity: captureProcessIdentity(workerPid),
    startedAt: new Date().toISOString(),
    lastHeartbeatAt: new Date().toISOString(),
    submissionState: "in-flight",
    submittedAt: new Date().toISOString()
  }, env);
}

export function finishJob(cwd, jobId, result, env = process.env) {
  return mutateJobUnderLock(cwd, jobId, env, (current) => {
    if (isTerminalJobStatus(current.status)) {
      return null;
    }
    const succeeded = result.status === 0;
    const cancelledByRequest = hasEffectiveCancelRequest(current);
    const stdout = storedOutput(result.stdout ?? "", cwd, {
      bytes: result.stdoutBytes,
      truncated: result.stdoutTruncated
    });
    const stderr = storedOutput(result.stderr ?? "", cwd, {
      bytes: result.stderrBytes,
      truncated: result.stderrTruncated
    });
    return {
      ...current,
      status: cancelledByRequest ? "cancelled" : succeeded ? "succeeded" : "failed",
      phase: cancelledByRequest ? "cancelled" : succeeded ? "succeeded" : "failed",
      submissionState: cancelledByRequest ? "cancelled" : succeeded ? "completed" : "failed",
      exitStatus: result.status,
      stdout: stdout.text,
      stderr: stderr.text,
      stdoutBytes: stdout.bytes,
      stderrBytes: stderr.bytes,
      stdoutStoredBytes: stdout.storedBytes,
      stderrStoredBytes: stderr.storedBytes,
      stdoutTruncated: stdout.truncated,
      stderrTruncated: stderr.truncated,
      error: sanitizeSummary(result.error ?? "", { cwd, maxBytes: 4096 }),
      finishedAt: new Date().toISOString(),
      workerPid: null,
      childPid: null
    };
  });
}

export function listJobs(cwd = process.cwd(), env = process.env) {
  const dir = ensureJobsDir(cwd, env);
  const jobs = fs.readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => {
      try {
        return JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
      } catch {
        return {
          id: name.slice(0, -".json".length),
          status: "corrupt"
        };
      }
    })
    .sort((left, right) => String(right.createdAt ?? "").localeCompare(String(left.createdAt ?? "")));
  return {
    stateDir: stateDirForCwd(cwd, env),
    jobs
  };
}

export function readJob(cwd, jobId, env = process.env) {
  return readJobFileDirect(jobFile(cwd, jobId, env), jobId);
}

export function activeJobs(cwd = process.cwd(), env = process.env) {
  return listJobs(cwd, env).jobs.filter((job) => job.status === "queued" || job.status === "running");
}

export function canStartBackgroundJob(cwd = process.cwd(), env = process.env, limit = DEFAULT_MAX_ACTIVE_JOBS) {
  const active = activeJobs(cwd, env);
  return { ok: active.length < limit, activeCount: active.length, limit, active };
}

export function findActiveJobByIdempotencyKey(cwd = process.cwd(), idempotencyKey, env = process.env) {
  if (!idempotencyKey) {
    return null;
  }
  return activeJobs(cwd, env).find((job) => job.idempotencyKey === idempotencyKey) ?? null;
}

export function enrichJobLifecycle(job, options = {}) {
  return { ...job, lifecycle: classifyJobLiveness(job, options) };
}

export function reapLostJobs(cwd = process.cwd(), options = {}, env = process.env) {
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const updates = [];
  for (const job of listJobs(cwd, env).jobs) {
    const lifecycle = classifyJobLiveness(job, { now, env, queuedLostAfterMs: queuedLostAfterMs(env) });
    if (lifecycle.state !== "lost") {
      continue;
    }
    if (job.status === "queued") {
      const missingDirectWorker = !Number.isInteger(job.workerPid) && job.reservationMode !== "host-forwarded" && job.submissionState === "starting";
      const workerGone = Number.isInteger(job.workerPid) && !validateJobWorkerProcess(job.workerPid, job.id).ok;
      const abandonedReservation = !Number.isInteger(job.workerPid) && job.reservationMode === "host-forwarded";
      if (workerGone || missingDirectWorker || abandonedReservation) {
        updates.push(updateJobUnlessTerminal(cwd, job.id, {
          status: "failed",
          phase: abandonedReservation ? "reservation-expired" : "worker-launch-failed",
          error: abandonedReservation
            ? "Host-forwarded reserved job was not claimed before the queued timeout; releasing background capacity without resubmitting the Claude request."
            : "Background worker exited or never exposed a valid worker before claiming the queued job; the plugin did not resubmit the Claude request.",
          finishedAt: new Date(now).toISOString(),
          workerPid: null
        }, env));
      }
      continue;
    }
    if (Number.isInteger(job.workerPid) && validateJobWorkerProcess(job.workerPid, job.id).ok) {
      updates.push(updateJobUnlessTerminal(cwd, job.id, {
        phase: "lost",
        lastProgressMessage: "Worker heartbeat is stale, but a validated worker process is still alive.",
        lifecycleState: "lost"
      }, env));
      continue;
    }
    const childGroupPid = Number.isInteger(job.childProcessGroupPid) ? job.childProcessGroupPid : job.childPid;
    const childGroupValidation = Number.isInteger(childGroupPid) && job.childProcessGroupIdentity
      ? validateProcessGroupLeader(childGroupPid, job.childProcessGroupIdentity)
      : { ok: false };
    if (childGroupValidation.ok || (Number.isInteger(childGroupPid) && job.childProcessGroupIdentity && processGroupHasLiveMembers(childGroupPid))) {
      updates.push(updateJobUnlessTerminal(cwd, job.id, {
        phase: childGroupValidation.ok ? "orphaned" : "leaderless-orphaned",
        lastProgressMessage: childGroupValidation.ok
          ? "Worker heartbeat is lost but the supervised process-group leader still exists; not freeing capacity or resubmitting."
          : "Worker heartbeat is lost but live members remain in the supervised process group; not freeing capacity or resubmitting.",
        lifecycleState: "lost",
        workerPid: null
      }, env));
      continue;
    }
    if (Number.isInteger(childGroupPid) && !job.childProcessGroupIdentity && captureProcessIdentity(childGroupPid)) {
      updates.push(updateJobUnlessTerminal(cwd, job.id, {
        phase: "unsafe-child-identity",
        lastProgressMessage: "A child PID still exists but has no saved identity; refusing to signal possible PID reuse and preserving capacity until manual inspection.",
        lifecycleState: "lost",
        workerPid: null
      }, env));
      continue;
    }
    updates.push(updateJobUnlessTerminal(cwd, job.id, {
      status: "failed",
      phase: "lost",
      error: "Tracked worker heartbeat was lost and no validated worker or supervised process group remains; the plugin did not resubmit the Claude request.",
      finishedAt: new Date(now).toISOString(),
      workerPid: null,
      childPid: null
    }, env));
  }
  return updates.filter(Boolean);
}

export function resultForJob(cwd, jobId, env = process.env) {
  const job = readJob(cwd, jobId, env);
  if (!job) {
    return { status: "not_found", jobId };
  }
  if (job.status === "corrupt") {
    return { status: "corrupt", jobId, job };
  }
  const updated = updateJob(cwd, jobId, {
    resultViewedAt: new Date().toISOString()
  }, env) ?? job;
  if (updated?.status === "locked") {
    return { status: "locked", jobId, reason: updated.reason ?? "Job state is busy; retry later.", job: updated };
  }
  return { status: "ok", job: updated };
}

function childProcessGroupPidForJob(job) {
  if (Number.isInteger(job.childProcessGroupPid)) {
    return job.childProcessGroupPid;
  }
  if (Number.isInteger(job.childPid)) {
    return job.childPid;
  }
  return null;
}

function cancelFailure(cwd, jobId, reason, env, options = {}) {
  const updated = updateJobUnlessTerminal(cwd, jobId, {
    ...(options.preserveActive ? {} : { status: "cancel_failed" }),
    phase: "cancel_failed",
    cancelFailedAt: new Date().toISOString(),
    cancelFailureReason: reason
  }, env);
  if (updated && isTerminalJobStatus(updated.status) && updated.status !== "cancel_failed") {
    return {
      status: updated.status,
      jobId,
      reason: "Job reached a terminal state before cancellation failure could be persisted.",
      job: updated
    };
  }
  return { status: "cancel_failed", jobId, reason, job: updated };
}

function cancelQueuedJob(cwd, jobId, env) {
  return mutateJobUnderLock(cwd, jobId, env, (current) => {
    if (current.status !== "queued") {
      return null;
    }
    const cancelQueuedWorkerPid = Number.isInteger(current.workerPid) ? current.workerPid : undefined;
    return {
      ...current,
      status: "cancelled",
      cancelledAt: new Date().toISOString(),
      ...(cancelQueuedWorkerPid ? { cancelQueuedWorkerPid } : {})
    };
  });
}

export function cancelJob(cwd, jobId, env = process.env) {
  const job = readJob(cwd, jobId, env);
  if (!job) {
    return { status: "not_found", jobId };
  }
  if (job.status === "queued") {
    const updated = cancelQueuedJob(cwd, jobId, env);
    if (updated?.status === "cancelled") {
      if (Number.isInteger(updated.cancelQueuedWorkerPid)) {
        const workerTermination = terminateValidatedJobWorker(updated.cancelQueuedWorkerPid, jobId);
        if (!workerTermination.ok) {
          const failed = updateJob(cwd, jobId, {
            status: "cancel_failed",
            cancelFailedAt: new Date().toISOString(),
            cancelFailureReason: `Queued worker cancellation requires process identity validation; refusing to report cancelled: ${workerTermination.reason || "termination failed"}`,
            cancelWorkerIdentity: workerTermination.identity ?? updated.pidIdentity,
            cancelWorkerDelivered: Boolean(workerTermination.delivered)
          }, env);
          return {
            status: "cancel_failed",
            jobId,
            reason: "Queued worker cancellation failed after the queued state was closed.",
            job: failed ?? updated
          };
        }
        const annotated = updateJob(cwd, jobId, {
          cancelWorkerIdentity: workerTermination.identity ?? updated.pidIdentity,
          cancelWorkerDelivered: Boolean(workerTermination.delivered),
          cancelWorkerEscalated: Boolean(workerTermination.escalated)
        }, env);
        return { status: "cancelled", jobId, job: annotated?.status === "cancelled" ? annotated : updated };
      }
      return { status: "cancelled", jobId, job: updated };
    }
    if (updated?.status === "running") {
      return cancelJob(cwd, jobId, env);
    }
    if (updated && isTerminalJobStatus(updated.status)) {
      return { status: updated.status, jobId, job: updated };
    }
    return cancelFailure(cwd, jobId, "Queued job changed state before cancellation could be persisted.", env);
  }
  if (job.status === "running") {
    const childGroupPid = childProcessGroupPidForJob(job);
    if (!Number.isInteger(childGroupPid) && !Number.isInteger(job.workerPid)) {
      return cancelFailure(cwd, jobId, "Running job has no valid workerPid or child process group.", env);
    }
    const requested = updateJobUnlessTerminal(cwd, jobId, {
      cancelRequestedAt: new Date().toISOString(),
      phase: "cancelling"
    }, env);
    if (!requested || requested.status === "locked") {
      return {
        status: "cancel_failed",
        jobId,
        reason: requested?.reason ?? "Running job cancellation request could not be persisted; refusing to signal.",
        job: requested
      };
    }
    if (requested && isTerminalJobStatus(requested.status)) {
      return {
        status: requested.status,
        jobId,
        reason: "Job reached a terminal state before cancellation could be requested.",
        job: requested
      };
    }
    let workerTermination = Number.isInteger(requested.workerPid)
      ? terminateValidatedJobWorker(requested.workerPid, jobId)
      : { ok: true, delivered: false, reason: "worker process already absent" };
    const requestedChildGroupPid = childProcessGroupPidForJob(requested);
    let childTermination = Number.isInteger(requestedChildGroupPid)
      ? terminateValidatedProcessGroup(requestedChildGroupPid, requested.childProcessGroupIdentity)
      : { ok: true, delivered: false, reason: "no child process group recorded" };
    if (!workerTermination.ok && childTermination.ok && Number.isInteger(requested.workerPid)) {
      workerTermination = terminateValidatedJobWorker(requested.workerPid, jobId);
    }
    if (!childTermination.ok && workerTermination.ok && Number.isInteger(requestedChildGroupPid)) {
      childTermination = terminateValidatedProcessGroup(requestedChildGroupPid, requested.childProcessGroupIdentity);
    }
    if (childTermination.ok && workerTermination.ok) {
      const signalDelivered = Boolean(childTermination.delivered || workerTermination.delivered);
      if (!signalDelivered) {
        const processMayRemain = !(
          String(workerTermination.reason ?? "").includes("already absent") &&
          String(childTermination.reason ?? "").includes("no child process group")
        );
        return cancelFailure(cwd, jobId, "Running job cancellation did not deliver a signal to a validated worker or child process group.", env, { preserveActive: processMayRemain });
      }
      const updates = {
        status: "cancelled",
        cancelledAt: new Date().toISOString(),
        cancelIdentity: workerTermination.identity ?? requested.pidIdentity ?? childTermination.identity ?? requested.childProcessGroupIdentity,
        cancelChildIdentity: childTermination.identity ?? requested.childProcessGroupIdentity,
        cancelWorkerIdentity: workerTermination.identity ?? requested.pidIdentity,
        cancelChildDelivered: Boolean(childTermination.delivered),
        cancelWorkerDelivered: Boolean(workerTermination.delivered)
      };
      const updated = updateJobUnlessTerminal(cwd, jobId, updates, env);
      if (!updated || updated.status === "locked") {
        return {
          status: "cancel_failed",
          jobId,
          reason: updated?.reason ?? "Cancellation signal was delivered but the cancelled state could not be persisted.",
          job: updated
        };
      }
      if (updated && isTerminalJobStatus(updated.status) && updated.status !== "cancelled") {
        return {
          status: updated.status,
          jobId,
          reason: "Job reached a terminal state before cancellation could be persisted.",
          job: updated
        };
      }
      if (updated?.status === "cancelled" && (!updated.cancelWorkerIdentity || !updated.cancelChildIdentity)) {
        const annotated = updateJob(cwd, jobId, updates, env);
        return { status: "cancelled", jobId, job: annotated?.status === "cancelled" ? annotated : updated };
      }
      return { status: "cancelled", jobId, job: updated };
    }
    const details = [
      childTermination.ok ? "" : `child process group: ${childTermination.reason || "termination failed"}`,
      workerTermination.ok ? "" : `worker: ${workerTermination.reason || "termination failed"}`
    ].filter(Boolean).join("; ");
    return cancelFailure(cwd, jobId, `Running job cancellation requires process identity validation; refusing to signal PID: ${details}`, env, { preserveActive: true });
  }
  if (isTerminalJobStatus(job.status)) {
    return { status: job.status, jobId, job };
  }
  return {
    status: "cancel_failed",
    jobId,
    reason: "No validated running process is recorded for this job."
  };
}
