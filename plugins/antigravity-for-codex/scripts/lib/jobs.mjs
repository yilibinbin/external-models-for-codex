import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { stateDirForCwd } from "./state.mjs";
import { terminateValidatedJobWorker } from "./process.mjs";

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
    stdout: "",
    stderr: "",
    error: ""
  };
  return writeJob(job, cwd, env);
}

export function reserveJob({ command, args = [], cwd = process.cwd() }, env = process.env) {
  if (!RESERVABLE_COMMANDS.has(command)) {
    throw new Error(`Command "${command}" cannot be reserved.`);
  }
  const job = createJob({ command, args, cwd }, env);
  job.status = "reserved";
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
    job.updatedAt = now();
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
    job.startedAt = job.startedAt || now();
    job.updatedAt = now();
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

    const termination = terminateValidatedJobWorker(refreshed.worker?.pid, refreshed.worker?.identity);
    if (termination.status === "failed") {
      refreshed.status = "cancel_failed";
      refreshed.error = capText(termination.error || "Failed to cancel job.");
    } else {
      refreshed.status = "cancelled";
    }
    refreshed.cancel = termination;
    refreshed.endedAt = now();
    refreshed.updatedAt = refreshed.endedAt;
    return writeJob(refreshed, cwd, env);
  });
}
