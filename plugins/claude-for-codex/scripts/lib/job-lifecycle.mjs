import crypto from "node:crypto";

export const TERMINAL_JOB_STATUSES = Object.freeze(["succeeded", "failed", "cancelled", "cancel_failed"]);
export const TERMINAL_JOB_STATUS_SET = new Set(TERMINAL_JOB_STATUSES);
export const JOB_HEARTBEAT_INTERVAL_MS = 15_000;
export const JOB_SUSPECT_AFTER_MS = 3 * 60 * 1000;
export const JOB_LOST_AFTER_MS = 10 * 60 * 1000;
export const JOB_QUEUED_LOST_AFTER_MS = 60 * 1000;
export const DEFAULT_BACKGROUND_WAIT_MS = 45_000;
export const MAX_BACKGROUND_WAIT_MS = 5 * 60 * 1000;
export const HARD_JOB_TIMEOUT_MS = 30 * 60 * 1000;
export const DEFAULT_MAX_ACTIVE_JOBS = 3;
export const MAX_STORED_OUTPUT_BYTES = 1024 * 1024;

export function isTerminalJobStatus(status) {
  return TERMINAL_JOB_STATUS_SET.has(String(status ?? ""));
}

function parseTime(value) {
  const parsed = Date.parse(String(value ?? ""));
  return Number.isFinite(parsed) ? parsed : null;
}

export function parsePositiveInteger(value, fallback, { min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < min) {
    return fallback;
  }
  return Math.min(Math.trunc(parsed), max);
}

export function queuedLostAfterMs(env = process.env) {
  return parsePositiveInteger(env.CLAUDE_FOR_CODEX_QUEUED_LOST_AFTER_MS, JOB_QUEUED_LOST_AFTER_MS, {
    min: 100,
    max: 10 * 60 * 1000
  });
}

export function deriveJobIdempotencyKey({ command, args = [], cwd = "", workspaceFingerprint = "", executionControls = {} }) {
  const hash = crypto.createHash("sha256");
  hash.update(JSON.stringify({
    command: String(command ?? ""),
    args: Array.isArray(args) ? args.map(String) : [],
    cwd: String(cwd ?? ""),
    workspaceFingerprint: String(workspaceFingerprint ?? ""),
    executionControls: executionControls && typeof executionControls === "object"
      ? Object.fromEntries(Object.entries(executionControls).sort(([left], [right]) => left.localeCompare(right)))
      : {}
  }));
  return `sha256:${hash.digest("hex")}`;
}

export function classifyJobLiveness(job, options = {}) {
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const status = String(job?.status ?? "unknown");
  if (isTerminalJobStatus(status)) {
    return { state: "terminal", status, staleForMs: 0 };
  }
  if (status === "queued") {
    const created = parseTime(job.createdAt);
    const staleForMs = created ? Math.max(0, now - created) : null;
    const queuedLostMs = Number.isFinite(options.queuedLostAfterMs)
      ? options.queuedLostAfterMs
      : queuedLostAfterMs(options.env ?? process.env);
    const missingDirectWorker = !Number.isInteger(job.workerPid) && job.reservationMode !== "host-forwarded" && job.submissionState === "starting";
    const abandonedReservation = !Number.isInteger(job.workerPid) && job.reservationMode === "host-forwarded";
    if (staleForMs !== null && staleForMs >= queuedLostMs && (Number.isInteger(job.workerPid) || missingDirectWorker || abandonedReservation)) {
      return { state: "lost", status, staleForMs, queued: true };
    }
    return { state: "queued", status, staleForMs: staleForMs ?? 0 };
  }
  if (status !== "running") {
    return { state: "unknown", status, staleForMs: 0 };
  }
  const signalTimes = [parseTime(job.lastProgressAt), parseTime(job.lastHeartbeatAt), parseTime(job.startedAt)]
    .filter((value) => Number.isFinite(value));
  const lastSignal = signalTimes.length ? Math.max(...signalTimes) : null;
  if (!lastSignal) {
    return { state: "suspect", status, staleForMs: null };
  }
  const staleForMs = Math.max(0, now - lastSignal);
  if (staleForMs >= JOB_LOST_AFTER_MS) {
    return { state: "lost", status, staleForMs };
  }
  if (staleForMs >= JOB_SUSPECT_AFTER_MS) {
    return { state: "suspect", status, staleForMs };
  }
  return { state: "healthy", status, staleForMs };
}
