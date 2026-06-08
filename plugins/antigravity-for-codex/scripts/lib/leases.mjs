import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { stateDirForCwd } from "./state.mjs";

const ROLE_LIMIT = 80;
const DEFAULT_TTL_SECONDS = 300;
const MAX_TTL_SECONDS = 24 * 60 * 60;
const LOCK_WAIT_MS = 1000;
const LOCK_STALE_MS = 30_000;

function leasesPath(cwd = process.cwd(), env = process.env) {
  const dir = stateDirForCwd(cwd, env);
  fs.mkdirSync(dir, { recursive: true });
  return path.join(dir, "leases.json");
}

function cleanText(value, limit = ROLE_LIMIT) {
  return String(value || "")
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "")
    .slice(0, limit)
    .trim();
}

function nowMs() {
  return Date.now();
}

function iso(ms) {
  return new Date(ms).toISOString();
}

function ttlMs(value) {
  const seconds = value === undefined || value === "" ? DEFAULT_TTL_SECONDS : Number(value);
  if (!Number.isFinite(seconds) || seconds < 1 || seconds > MAX_TTL_SECONDS) {
    throw new Error(`Lease TTL must be between 1 and ${MAX_TTL_SECONDS} seconds.`);
  }
  return Math.ceil(seconds * 1000);
}

function readLeases(cwd = process.cwd(), env = process.env) {
  const file = leasesPath(cwd, env);
  try {
    const payload = JSON.parse(fs.readFileSync(file, "utf8"));
    return Array.isArray(payload.leases) ? payload.leases : [];
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

function writeLeases(cwd, env, leases) {
  const file = leasesPath(cwd, env);
  const tmp = `${file}.${process.pid}.${randomBytes(4).toString("hex")}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify({ leases }, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, file);
  return { leases };
}

function sleepMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function acquireLock(file) {
  const lockFile = `${file}.lock`;
  const deadline = Date.now() + LOCK_WAIT_MS;
  while (Date.now() <= deadline) {
    try {
      const handle = fs.openSync(lockFile, "wx");
      fs.writeFileSync(handle, JSON.stringify({ pid: process.pid, createdAt: iso(nowMs()) }));
      return { handle, lockFile };
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      try {
        const stat = fs.statSync(lockFile);
        if (Date.now() - stat.mtimeMs > LOCK_STALE_MS) {
          const staleFile = `${lockFile}.stale-${process.pid}-${randomBytes(4).toString("hex")}`;
          fs.renameSync(lockFile, staleFile);
          fs.unlinkSync(staleFile);
          continue;
        }
      } catch (statError) {
        if (statError.code !== "ENOENT") throw statError;
        continue;
      }
      if (Date.now() >= deadline) return null;
      sleepMs(25);
    }
  }
  return null;
}

function releaseLock(lock) {
  if (!lock) return;
  fs.closeSync(lock.handle);
  try {
    fs.unlinkSync(lock.lockFile);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

function withLeasesLock(cwd, env, callback) {
  const file = leasesPath(cwd, env);
  const lock = acquireLock(file);
  if (!lock) {
    throw new Error("Leases state is busy.");
  }
  try {
    return callback();
  } finally {
    releaseLock(lock);
  }
}

function activeLeases(cwd = process.cwd(), env = process.env) {
  const current = nowMs();
  return readLeases(cwd, env).filter((lease) => Date.parse(lease.expiresAt || "") > current);
}

export function claimLease({ role, ttlSeconds = DEFAULT_TTL_SECONDS, cwd = process.cwd() }, env = process.env) {
  const cleanedRole = cleanText(role);
  if (!cleanedRole) {
    throw new Error("Lease role is required.");
  }
  return withLeasesLock(cwd, env, () => {
    const created = nowMs();
    const lease = {
      id: `lease-${Date.now().toString(36)}-${randomBytes(4).toString("hex")}`,
      role: cleanedRole,
      createdAt: iso(created),
      expiresAt: iso(created + ttlMs(ttlSeconds))
    };
    const leases = activeLeases(cwd, env);
    leases.push(lease);
    writeLeases(cwd, env, leases);
    return { status: "claimed", leaseId: lease.id, lease };
  });
}

export function listLeases(cwd = process.cwd(), env = process.env) {
  return withLeasesLock(cwd, env, () => {
    const leases = activeLeases(cwd, env);
    writeLeases(cwd, env, leases);
    return { leases };
  });
}

export function releaseLease(id, cwd = process.cwd(), env = process.env) {
  const leaseId = cleanText(id, 120);
  if (!leaseId) {
    throw new Error("Lease id is required.");
  }
  return withLeasesLock(cwd, env, () => {
    const leases = activeLeases(cwd, env);
    const next = leases.filter((lease) => lease.id !== leaseId);
    writeLeases(cwd, env, next);
    return { status: next.length === leases.length ? "not_found" : "released", leaseId };
  });
}
