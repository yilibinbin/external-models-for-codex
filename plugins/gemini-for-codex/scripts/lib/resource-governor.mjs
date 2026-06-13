import { randomBytes } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";

const PLUGIN_NAME = "gemini-for-codex";
const DISABLE_ENV = "GEMINI_FOR_CODEX_RESOURCE_GOVERNOR";
const LOCK_DIR_ENV = "GEMINI_FOR_CODEX_RESOURCE_LOCK_DIR";
const MODEL_LIMIT_ENV = "GEMINI_FOR_CODEX_GLOBAL_MAX_MODEL_CALLS";
const BACKGROUND_LIMIT_ENV = "GEMINI_FOR_CODEX_GLOBAL_MAX_BACKGROUND_JOBS";
const MULTI_PARALLEL_ENV = "GEMINI_FOR_CODEX_MULTI_REVIEW_MAX_PARALLEL";
const LOCK_WAIT_ENV = "GEMINI_FOR_CODEX_RESOURCE_LOCK_WAIT_MS";
const LOCK_STALE_ENV = "GEMINI_FOR_CODEX_RESOURCE_LOCK_STALE_MS";
const DEFAULT_MODEL_LIMIT = 2;
const DEFAULT_BACKGROUND_LIMIT = 2;
const DEFAULT_MULTI_PARALLEL = 2;
const DEFAULT_LOCK_WAIT_MS = 1000;
const DEFAULT_LOCK_STALE_MS = 30_000;
const DEFAULT_LEASE_TTL_MS = 30 * 60 * 1000;
const BACKGROUND_LEASE_TTL_MS = 2 * 60 * 60 * 1000;

function sleepMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function parsePositiveInteger(value, fallback, { min = 1, max = 64 } = {}) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < min) {
    return fallback;
  }
  return Math.min(Math.trunc(parsed), max);
}

function governorDisabled(env = process.env) {
  return String(env[DISABLE_ENV] || "").toLowerCase() === "off";
}

export function resourceLockRoot(env = process.env) {
  if (env[LOCK_DIR_ENV]) {
    return path.resolve(env[LOCK_DIR_ENV]);
  }
  return path.join(os.homedir(), ".codex", PLUGIN_NAME, "global-resource-locks");
}

function lockWaitMs(env = process.env) {
  return parsePositiveInteger(env[LOCK_WAIT_ENV], DEFAULT_LOCK_WAIT_MS, { min: 0, max: 60_000 });
}

function lockStaleMs(env = process.env) {
  return parsePositiveInteger(env[LOCK_STALE_ENV], DEFAULT_LOCK_STALE_MS, { min: 1000, max: 10 * 60 * 1000 });
}

export function resourceLimit(kind, env = process.env) {
  if (kind === "background-job") {
    return parsePositiveInteger(env[BACKGROUND_LIMIT_ENV], DEFAULT_BACKGROUND_LIMIT, { min: 1, max: 32 });
  }
  return parsePositiveInteger(env[MODEL_LIMIT_ENV], DEFAULT_MODEL_LIMIT, { min: 1, max: 32 });
}

export function multiReviewConcurrency(env = process.env) {
  return Math.max(1, Math.min(
    parsePositiveInteger(env[MULTI_PARALLEL_ENV], DEFAULT_MULTI_PARALLEL, { min: 1, max: 16 }),
    resourceLimit("model-call", env)
  ));
}

function processAlive(pid) {
  const numericPid = Number(pid);
  if (!Number.isInteger(numericPid) || numericPid <= 0) {
    return false;
  }
  try {
    process.kill(numericPid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

function ensureRoot(env = process.env) {
  const root = resourceLockRoot(env);
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  return root;
}

function leasePath(root, leaseId) {
  if (!/^[A-Za-z0-9._-]+$/.test(String(leaseId || ""))) {
    throw new Error("Invalid resource lease id.");
  }
  return path.join(root, `${leaseId}.json`);
}

function readLease(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

function shouldReapLease(lease, now = Date.now()) {
  if (!lease || lease.plugin !== PLUGIN_NAME) {
    return true;
  }
  const expiresAt = Date.parse(String(lease.expiresAt || ""));
  if (Number.isFinite(expiresAt) && expiresAt <= now) {
    return true;
  }
  if (lease.transferable) {
    return false;
  }
  return !processAlive(lease.pid);
}

function reapStaleLeases(root, now = Date.now()) {
  let names = [];
  try {
    names = fs.readdirSync(root);
  } catch {
    return;
  }
  for (const name of names) {
    if (!name.endsWith(".json")) {
      continue;
    }
    const file = path.join(root, name);
    const lease = readLease(file);
    if (shouldReapLease(lease, now)) {
      try {
        fs.rmSync(file, { force: true });
      } catch {
        // Best-effort cleanup only.
      }
    }
  }
}

function activeLeases(root, kind) {
  reapStaleLeases(root);
  let leases = [];
  try {
    leases = fs.readdirSync(root)
      .filter((name) => name.endsWith(".json"))
      .map((name) => readLease(path.join(root, name)))
      .filter((lease) => lease?.plugin === PLUGIN_NAME && lease.kind === kind && !shouldReapLease(lease));
  } catch {
    leases = [];
  }
  return leases;
}

function acquireMutex(root, env = process.env) {
  const file = path.join(root, ".governor.lock");
  const deadline = Date.now() + lockWaitMs(env);
  while (Date.now() <= deadline) {
    try {
      const handle = fs.openSync(file, "wx", 0o600);
      fs.writeFileSync(handle, JSON.stringify({ pid: process.pid, createdAt: new Date().toISOString() }));
      return { handle, file };
    } catch (error) {
      if (error.code !== "EEXIST") {
        throw error;
      }
      try {
        const stat = fs.statSync(file);
        const lock = readLease(file);
        if ((Date.now() - stat.mtimeMs) > lockStaleMs(env) || !processAlive(lock?.pid)) {
          fs.rmSync(file, { force: true });
          continue;
        }
      } catch (statError) {
        if (statError.code !== "ENOENT") {
          throw statError;
        }
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

function releaseMutex(lock) {
  if (!lock) {
    return;
  }
  try {
    fs.closeSync(lock.handle);
  } catch {
    // Ignore close failures; unlink below is authoritative for future waiters.
  }
  try {
    fs.rmSync(lock.file, { force: true });
  } catch {
    // Best-effort cleanup only.
  }
}

export function acquireResourceLease(kind, options = {}) {
  const env = options.env || process.env;
  if (governorDisabled(env)) {
    return { ok: true, disabled: true, release() {} };
  }
  const root = ensureRoot(env);
  const limit = Number.isInteger(options.limit) ? options.limit : resourceLimit(kind, env);
  const mutex = acquireMutex(root, env);
  if (!mutex) {
    return {
      ok: false,
      reason: "resource governor lock is busy",
      kind,
      active: activeLeases(root, kind).length,
      limit,
      root
    };
  }
  try {
    const active = activeLeases(root, kind);
    if (active.length >= limit) {
      return {
        ok: false,
        reason: `global ${kind} capacity exhausted`,
        kind,
        active: active.length,
        limit,
        root
      };
    }
    const now = Date.now();
    const ttlMs = Number.isFinite(options.ttlMs) && options.ttlMs > 0
      ? options.ttlMs
      : (kind === "background-job" ? BACKGROUND_LEASE_TTL_MS : DEFAULT_LEASE_TTL_MS);
    const id = `${kind}-${now.toString(36)}-${process.pid}-${randomBytes(6).toString("hex")}`;
    const file = leasePath(root, id);
    const lease = {
      id,
      plugin: PLUGIN_NAME,
      kind,
      command: String(options.command || ""),
      cwdHash: String(options.cwdHash || ""),
      pid: process.pid,
      ppid: process.ppid,
      transferable: Boolean(options.transferable),
      createdAt: new Date(now).toISOString(),
      expiresAt: new Date(now + ttlMs).toISOString()
    };
    fs.writeFileSync(file, `${JSON.stringify(lease, null, 2)}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
    return {
      ok: true,
      lease,
      root,
      release() {
        releaseResourceLease(id, env);
      }
    };
  } finally {
    releaseMutex(mutex);
  }
}

export function releaseResourceLease(leaseId, env = process.env) {
  if (!leaseId || governorDisabled(env)) {
    return;
  }
  try {
    fs.rmSync(leasePath(resourceLockRoot(env), leaseId), { force: true });
  } catch {
    // Best-effort cleanup only.
  }
}

export function transferResourceLease(leaseId, pid, env = process.env) {
  if (!leaseId || governorDisabled(env)) {
    return false;
  }
  try {
    const root = ensureRoot(env);
    const file = leasePath(root, leaseId);
    const lease = readLease(file);
    if (!lease || lease.plugin !== PLUGIN_NAME) {
      return false;
    }
    const updated = {
      ...lease,
      pid,
      ppid: process.pid,
      transferable: false,
      transferredAt: new Date().toISOString()
    };
    const tmp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, `${JSON.stringify(updated, null, 2)}\n`, "utf8");
    fs.renameSync(tmp, file);
    return true;
  } catch {
    return false;
  }
}

export function ensureResourceLease(leaseId, kind, options = {}) {
  const env = options.env || process.env;
  if (governorDisabled(env)) {
    return { ok: true, disabled: true, release() {} };
  }
  if (leaseId) {
    const root = ensureRoot(env);
    const lease = readLease(leasePath(root, leaseId));
    if (lease?.kind === kind && lease.plugin === PLUGIN_NAME && !shouldReapLease(lease)) {
      return {
        ok: true,
        lease,
        root,
        release() {
          releaseResourceLease(leaseId, env);
        }
      };
    }
  }
  return acquireResourceLease(kind, options);
}

export function capacityBlockedMessage(plugin, lease) {
  return `capacity_blocked: ${plugin} ${lease.kind} capacity is full (${lease.active}/${lease.limit}) at ${lease.root}.`;
}

export function capacityBlockedResult(plugin, lease) {
  return {
    status: 75,
    stdout: "",
    stderr: `${capacityBlockedMessage(plugin, lease)}\n`,
    error: capacityBlockedMessage(plugin, lease),
    errorCode: "ECAPACITY"
  };
}

export function withResourceLeaseSync(kind, options, callback) {
  const lease = acquireResourceLease(kind, options);
  if (!lease.ok) {
    return capacityBlockedResult(PLUGIN_NAME, lease);
  }
  try {
    return callback(lease);
  } finally {
    lease.release();
  }
}

export async function withResourceLeaseAsync(kind, options, callback) {
  const lease = acquireResourceLease(kind, options);
  if (!lease.ok) {
    return capacityBlockedResult(PLUGIN_NAME, lease);
  }
  try {
    return await callback(lease);
  } finally {
    lease.release();
  }
}
