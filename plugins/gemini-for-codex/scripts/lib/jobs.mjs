import fs from "node:fs";
import path from "node:path";
import { captureProcessIdentity, terminateValidatedJobWorker } from "./process.mjs";
import { jobsDirForCwd, stateDirForCwd } from "./state.mjs";

const JOB_ID_PATTERN = /^[A-Za-z0-9._-]+$/;
const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled", "cancel_failed"]);

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

function writeJob(cwd, job, env = process.env) {
  const file = jobFile(cwd, job.id, env);
  const tmpFile = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmpFile, `${JSON.stringify(job, null, 2)}\n`, "utf8");
  fs.renameSync(tmpFile, file);
  return job;
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
  if (!String(job.workerCommand[commandIndex - 1] ?? "").endsWith("gemini-companion.mjs")) {
    return false;
  }
  const jobIdFlagIndex = job.workerCommand.indexOf("--job-id", commandIndex + 1);
  return jobIdFlagIndex >= 0 && job.workerCommand[jobIdFlagIndex + 1] === jobId;
}

export function claimReservedJob(cwd, jobId, workerPid = process.pid, env = process.env) {
  const file = jobFile(cwd, jobId, env);
  if (!fs.existsSync(file)) {
    return { status: "not_found", jobId };
  }
  const original = fs.readFileSync(file, "utf8");
  let job;
  try {
    job = JSON.parse(original);
  } catch (error) {
    return {
      status: "not_claimed",
      jobId,
      reason: `Reserved job state is corrupt: ${error.message || String(error)}`
    };
  }
  if (job.status !== "queued") {
    return { status: "not_claimed", jobId, job };
  }
  if (!isValidReservedWorkerCommand(job, jobId)) {
    return {
      status: "not_claimed",
      jobId,
      reason: "Job is not a valid host-forwarded reservation.",
      job
    };
  }
  const running = {
    ...job,
    status: "running",
    workerPid,
    pidIdentity: captureProcessIdentity(workerPid),
    startedAt: new Date().toISOString(),
    updatedAt: new Date().toISOString()
  };
  const tmpFile = `${file}.${process.pid}.claim.tmp`;
  fs.writeFileSync(tmpFile, `${JSON.stringify(running, null, 2)}\n`, "utf8");
  if (fs.readFileSync(file, "utf8") !== original) {
    fs.rmSync(tmpFile, { force: true });
    return {
      status: "not_claimed",
      jobId,
      reason: "Job changed before it could be claimed."
    };
  }
  fs.renameSync(tmpFile, file);
  return { status: "claimed", job: running };
}

export function updateJob(cwd, jobId, updates, env = process.env) {
  const job = readJob(cwd, jobId, env);
  if (!job) {
    return null;
  }
  const updated = {
    ...job,
    ...updates,
    updatedAt: new Date().toISOString()
  };
  return writeJob(cwd, updated, env);
}

export function updateJobUnlessTerminal(cwd, jobId, updates, env = process.env) {
  const job = readJob(cwd, jobId, env);
  if (!job) {
    return null;
  }
  if (TERMINAL_STATUSES.has(job.status)) {
    return job;
  }
  const updated = {
    ...job,
    ...updates,
    updatedAt: new Date().toISOString()
  };
  return writeJob(cwd, updated, env);
}

export function markJobRunning(cwd, jobId, workerPid, env = process.env) {
  return updateJob(cwd, jobId, {
    status: "running",
    workerPid,
    pidIdentity: captureProcessIdentity(workerPid),
    startedAt: new Date().toISOString()
  }, env);
}

export function finishJob(cwd, jobId, result, env = process.env) {
  const current = readJob(cwd, jobId, env);
  if (current && TERMINAL_STATUSES.has(current.status)) {
    return current;
  }
  return updateJob(cwd, jobId, {
    status: result.status === 0 ? "succeeded" : "failed",
    exitStatus: result.status,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ?? "",
    finishedAt: new Date().toISOString()
  }, env);
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
  const file = jobFile(cwd, jobId, env);
  if (!fs.existsSync(file)) {
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    return {
      id: jobId,
      status: "corrupt",
      stateError: error.message || String(error)
    };
  }
}

export function resultForJob(cwd, jobId, env = process.env) {
  const job = readJob(cwd, jobId, env);
  if (!job) {
    return { status: "not_found", jobId };
  }
  if (job.status === "corrupt") {
    return { status: "corrupt", jobId, job };
  }
  const updated = {
    ...job,
    resultViewedAt: new Date().toISOString()
  };
  writeJob(cwd, updated, env);
  return { status: "ok", job: updated };
}

export function cancelJob(cwd, jobId, env = process.env) {
  const job = readJob(cwd, jobId, env);
  if (!job) {
    return { status: "not_found", jobId };
  }
  if (job.status === "queued") {
    const updated = {
      ...job,
      status: "cancelled",
      cancelledAt: new Date().toISOString()
    };
    writeJob(cwd, updated, env);
    return { status: "cancelled", jobId, job: updated };
  }
  if (job.status === "running" && Number.isInteger(job.workerPid)) {
    const termination = terminateValidatedJobWorker(job.workerPid, jobId);
    if (termination.ok) {
      const updated = updateJobUnlessTerminal(cwd, jobId, {
        status: "cancelled",
        cancelledAt: new Date().toISOString(),
        cancelIdentity: termination.identity
      }, env);
      return { status: "cancelled", jobId, job: updated };
    }
    const updated = updateJobUnlessTerminal(cwd, jobId, {
      status: "cancel_failed",
      cancelFailedAt: new Date().toISOString(),
      cancelFailureReason: `Running job cancellation requires process identity validation; refusing to signal PID: ${termination.reason}`
    }, env);
    return {
      status: "cancel_failed",
      jobId,
      reason: `Running job cancellation requires process identity validation; refusing to signal PID: ${termination.reason}`,
      job: updated
    };
  }
  if (job.status === "running") {
    const updated = updateJobUnlessTerminal(cwd, jobId, {
      status: "cancel_failed",
      cancelFailedAt: new Date().toISOString(),
      cancelFailureReason: "Running job has no valid workerPid."
    }, env);
    return {
      status: "cancel_failed",
      jobId,
      reason: "Running job has no valid workerPid.",
      job: updated
    };
  }
  if (TERMINAL_STATUSES.has(job.status)) {
    return { status: job.status, jobId, job };
  }
  return {
    status: "cancel_failed",
    jobId,
    reason: "No validated running process is recorded for this job."
  };
}
