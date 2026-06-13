import { spawnSync as nodeSpawnSync } from "node:child_process";

const TRANSIENT_SPAWN_ERROR_CODES = new Set(["EAGAIN", "EMFILE", "ENFILE", "ENOBUFS"]);
const DEFAULT_ATTEMPTS = 4;
const DEFAULT_BASE_DELAY_MS = 50;
const MAX_ATTEMPTS = 10;
const MAX_BASE_DELAY_MS = 1000;

function sleepMs(ms) {
  if (typeof Atomics !== "undefined"
    && typeof SharedArrayBuffer !== "undefined"
    && typeof Atomics.wait === "function") {
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
    return;
  }
  const deadline = Date.now() + Math.max(0, ms);
  while (Date.now() < deadline) {
    // Synchronous fallback for JS runtimes without Atomics.wait.
  }
}

function boundedInteger(value, fallback, min, max) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, Math.trunc(numeric)));
}

export function isTransientSpawnError(resultOrError) {
  const code = resultOrError?.error?.code ?? resultOrError?.code;
  return TRANSIENT_SPAWN_ERROR_CODES.has(String(code || ""));
}

export function spawnSyncWithRetry(command, args = [], options = {}, retryOptions = {}) {
  const spawnImpl = retryOptions.spawnSyncImpl || nodeSpawnSync;
  const attempts = boundedInteger(retryOptions.attempts, DEFAULT_ATTEMPTS, 1, MAX_ATTEMPTS);
  const baseDelayMs = boundedInteger(retryOptions.baseDelayMs, DEFAULT_BASE_DELAY_MS, 0, MAX_BASE_DELAY_MS);
  let lastResult = null;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      lastResult = spawnImpl(command, args, options);
    } catch (error) {
      if (!isTransientSpawnError(error)) {
        throw error;
      }
      lastResult = {
        status: null,
        signal: null,
        stdout: "",
        stderr: "",
        error
      };
    }

    if (!isTransientSpawnError(lastResult) || attempt >= attempts) {
      return lastResult;
    }

    const delay = baseDelayMs * (2 ** (attempt - 1));
    if (delay > 0) {
      sleepMs(delay);
    }
  }

  return lastResult;
}
