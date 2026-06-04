import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { leasesDirForCwd } from "./state.mjs";
import { canonicalWorkspaceRoot } from "./workspace.mjs";

export const MIN_LEASE_TTL_MS = 30_000;
export const MAX_LEASE_TTL_MS = 1_800_000;

const ROLE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const ID_PATTERN = /^lease-[A-Za-z0-9._-]{8,}$/;

function nowIso(nowMs = Date.now()) {
  return new Date(nowMs).toISOString();
}

function workspaceId(cwd) {
  return createHash("sha256").update(canonicalWorkspaceRoot(cwd)).digest("hex").slice(0, 16);
}

function parseTtl(ttl) {
  if (typeof ttl === "number" && Number.isFinite(ttl)) {
    return ttl;
  }
  const text = String(ttl || "600s").trim();
  const match = text.match(/^(\d+)(ms|s|m)?$/);
  if (!match) {
    throw new Error(`Invalid lease ttl "${ttl}".`);
  }
  const value = Number(match[1]);
  const unit = match[2] || "s";
  const ms = unit === "ms" ? value : unit === "m" ? value * 60_000 : value * 1000;
  if (ms < MIN_LEASE_TTL_MS || ms > MAX_LEASE_TTL_MS) {
    throw new Error(`Lease ttl must be between ${MIN_LEASE_TTL_MS}ms and ${MAX_LEASE_TTL_MS}ms.`);
  }
  return ms;
}

function ensureLeaseDirs(cwd, env = process.env) {
  const root = leasesDirForCwd(cwd, env);
  for (const child of ["active", "archive", "reap"]) {
    fs.mkdirSync(path.join(root, child), { recursive: true, mode: 0o700 });
  }
  return root;
}

function nearestExisting(candidate) {
  let current = candidate;
  while (!fs.existsSync(current)) {
    const parent = path.dirname(current);
    if (parent === current) {
      throw new Error(`Cannot prove path containment for "${candidate}".`);
    }
    current = parent;
  }
  return current;
}

export function normalizeLeasePath(cwd, inputPath) {
  if (!inputPath) {
    throw new Error("Missing lease path.");
  }
  const root = canonicalWorkspaceRoot(cwd);
  const raw = String(inputPath);
  const resolved = path.isAbsolute(raw) ? path.resolve(raw) : path.resolve(root, raw);
  const existing = nearestExisting(resolved);
  const existingReal = fs.realpathSync.native(existing);
  const suffix = path.relative(existing, resolved);
  const finalReal = suffix ? path.resolve(existingReal, suffix) : existingReal;
  if (finalReal !== root && !finalReal.startsWith(`${root}${path.sep}`)) {
    throw new Error(`Lease path is outside workspace: ${inputPath}`);
  }
  return {
    root,
    absolutePath: finalReal,
    relativePath: path.relative(root, finalReal) || ".",
    pathHash: `sha256:${createHash("sha256").update(path.relative(root, finalReal) || ".").digest("hex")}`
  };
}

function activeDir(root, pathHash) {
  return path.join(root, "active", pathHash.replace(/^sha256:/, ""));
}

function reapLockDir(root, pathHash) {
  return path.join(root, "reap", pathHash.replace(/^sha256:/, ""));
}

function leaseFile(dir) {
  return path.join(dir, "lease.json");
}

function readLeaseFromDir(dir) {
  try {
    return JSON.parse(fs.readFileSync(leaseFile(dir), "utf8"));
  } catch (error) {
    return { status: "corrupt", stateError: error.message || String(error), stateCode: error.code || "" };
  }
}

function isFreshDirectory(dir, nowMs = Date.now()) {
  try {
    return nowMs - fs.statSync(dir).mtimeMs < 5000;
  } catch {
    return false;
  }
}

function writeLease(filePath, lease) {
  fs.writeFileSync(filePath, `${JSON.stringify(lease, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
}

function role(value) {
  const roleName = String(value || "manual");
  if (!ROLE_PATTERN.test(roleName)) {
    throw new Error(`Invalid role "${value}".`);
  }
  return roleName;
}

function validateLeaseId(id) {
  if (!ID_PATTERN.test(String(id || ""))) {
    throw new Error(`Invalid lease id "${id}".`);
  }
  return String(id);
}

function archiveActiveDir(root, sourceDir, lease) {
  const archive = path.join(root, "archive", `${lease.pathHash.replace(/^sha256:/, "")}-${lease.id}-${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`);
  fs.mkdirSync(path.dirname(archive), { recursive: true, mode: 0o700 });
  fs.renameSync(sourceDir, archive);
  return archive;
}

function pruneLeaseArchive(root, nowMs = Date.now(), maxAgeMs = 7 * 24 * 60 * 60 * 1000) {
  const archiveRoot = path.join(root, "archive");
  if (!fs.existsSync(archiveRoot)) {
    return;
  }
  for (const entry of fs.readdirSync(archiveRoot, { withFileTypes: true })) {
    if (!entry.isDirectory()) {
      continue;
    }
    const dir = path.join(archiveRoot, entry.name);
    try {
      if (nowMs - fs.statSync(dir).mtimeMs > maxAgeMs) {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    } catch {
      // Best-effort archive cleanup.
    }
  }
}

function writeLockOwner(lock) {
  writeLease(path.join(lock, "owner.json"), {
    pid: process.pid,
    createdAt: nowIso(),
    version: 1
  });
}

function lockOwnerAlive(lock) {
  let owner;
  try {
    owner = JSON.parse(fs.readFileSync(path.join(lock, "owner.json"), "utf8"));
  } catch {
    return false;
  }
  const pid = Number(owner.pid || 0);
  if (!Number.isInteger(pid) || pid <= 0) {
    return false;
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

function acquireReapLock(lock, maxAgeMs = 5 * 60 * 1000) {
  try {
    fs.mkdirSync(lock, { mode: 0o700 });
    writeLockOwner(lock);
    return { ok: true };
  } catch (error) {
    if (error.code !== "EEXIST") {
      throw error;
    }
  }
  const stale = !lockOwnerAlive(lock) && Date.now() - fs.statSync(lock).mtimeMs > maxAgeMs;
  if (!stale) {
    return { ok: false, reason: "Lease reap/release lock is active." };
  }
  fs.rmSync(lock, { recursive: true, force: true });
  try {
    fs.mkdirSync(lock, { mode: 0o700 });
    writeLockOwner(lock);
    return { ok: true };
  } catch (error) {
    if (error.code === "EEXIST") {
      return { ok: false, reason: "Lease reap/release lock is active." };
    }
    throw error;
  }
}

function withReapLock(root, pathHash, callback) {
  const lock = reapLockDir(root, pathHash);
  const acquired = acquireReapLock(lock);
  if (!acquired.ok) {
    return { status: "degraded", reason: acquired.reason };
  }
  try {
    return callback();
  } finally {
    fs.rmSync(lock, { recursive: true, force: true });
  }
}

export function clearStaleReapLocks(cwd = process.cwd(), env = process.env, maxAgeMs = 5 * 60 * 1000) {
  const root = ensureLeaseDirs(cwd, env);
  const reapRoot = path.join(root, "reap");
  let cleared = 0;
  for (const entry of fs.readdirSync(reapRoot, { withFileTypes: true })) {
    if (!entry.isDirectory()) {
      continue;
    }
    const dir = path.join(reapRoot, entry.name);
    try {
      const alive = lockOwnerAlive(dir);
      if (!alive && Date.now() - fs.statSync(dir).mtimeMs > maxAgeMs) {
        fs.rmSync(dir, { recursive: true, force: true });
        cleared += 1;
      }
    } catch {
      // Best-effort stale lock cleanup.
    }
  }
  return { cleared };
}

export function claimLease(cwd, options, env = process.env) {
  if (Number(options.depth || 0) > 3) {
    return { status: "degraded", degraded: true, reason: "Lease claim retry limit reached." };
  }
  const root = ensureLeaseDirs(cwd, env);
  const normalized = normalizeLeasePath(cwd, options.path);
  const dir = activeDir(root, normalized.pathHash);
  const lock = reapLockDir(root, normalized.pathHash);
  if (fs.existsSync(lock)) {
    return { status: "degraded", reason: "Lease reap/release lock is active.", degraded: true };
  }
  try {
    fs.mkdirSync(dir, { mode: 0o700 });
  } catch (error) {
    if (error.code !== "EEXIST") {
      throw error;
    }
    const existing = readLeaseFromDir(dir);
    if (existing.stateCode === "ENOENT" || !fs.existsSync(dir)) {
      return claimLease(cwd, { ...options, depth: Number(options.depth || 0) + 1 }, env);
    }
    if (existing.status === "corrupt") {
      if (isFreshDirectory(dir, Number(options.nowMs ?? Date.now()))) {
        return { status: "conflict", degraded: true, reason: "Lease claim is still being initialized." };
      }
      return { status: "degraded", degraded: true, reason: existing.stateError };
    }
    if (new Date(existing.expiresAt).getTime() <= Number(options.nowMs ?? Date.now())) {
      const reaped = reapExpiredLeaseForPath(cwd, options.path, { nowMs: options.nowMs ?? Date.now() }, env);
      if (reaped.status === "reaped") {
        return claimLease(cwd, { ...options, depth: Number(options.depth || 0) + 1 }, env);
      }
    }
    return { status: "conflict", lease: existing, degraded: false };
  }

  const ttlMs = parseTtl(options.ttl);
  const nowMs = Number(options.nowMs ?? Date.now());
  const lease = {
    version: 1,
    id: `lease-${Date.now().toString(36)}-${randomUUID().slice(0, 12)}`,
    path: normalized.relativePath,
    pathHash: normalized.pathHash,
    role: role(options.role),
    jobId: options.jobId ? String(options.jobId) : "",
    holder: options.holder || `pid-${process.pid}`,
    mode: ["plugin-managed", "native-agents", "manual"].includes(options.mode) ? options.mode : "manual",
    createdAt: nowIso(nowMs),
    expiresAt: nowIso(nowMs + ttlMs),
    releasedAt: null,
    status: "active",
    workspaceId: workspaceId(cwd)
  };
  try {
    writeLease(leaseFile(dir), lease);
    const verified = readLeaseFromDir(dir);
    if (verified.id !== lease.id) {
      throw new Error("Lease claim could not be verified after write.");
    }
  } catch (error) {
    fs.rmSync(dir, { recursive: true, force: true });
    throw error;
  }
  return { status: "claimed", lease, degraded: false, primitive: "mkdir" };
}

export function releaseLease(cwd, leaseId, env = process.env, options = {}) {
  const id = validateLeaseId(leaseId);
  const root = ensureLeaseDirs(cwd, env);
  for (const entry of fs.readdirSync(path.join(root, "active"), { withFileTypes: true })) {
    if (!entry.isDirectory()) {
      continue;
    }
    const dir = path.join(root, "active", entry.name);
    const lease = readLeaseFromDir(dir);
    if (lease.id !== id) {
      continue;
    }
    return withReapLock(root, lease.pathHash, () => {
      const current = readLeaseFromDir(dir);
      if (current.id !== id) {
        return { status: "not_found", leaseId: id };
      }
      if (options.holder && current.holder && current.holder !== options.holder) {
        return { status: "refused", leaseId: id, reason: "Lease holder mismatch." };
      }
      const released = {
        ...current,
        status: "released",
        releasedAt: nowIso(Number(options.nowMs ?? Date.now()))
      };
      writeLease(leaseFile(dir), released);
      archiveActiveDir(root, dir, released);
      return { status: "released", lease: released };
    });
  }
  return { status: "not_found", leaseId: id };
}

export function reapExpiredLeaseForPath(cwd, inputPath, options = {}, env = process.env) {
  const root = ensureLeaseDirs(cwd, env);
  const normalized = normalizeLeasePath(cwd, inputPath);
  const dir = activeDir(root, normalized.pathHash);
  if (!fs.existsSync(dir)) {
    return { status: "none" };
  }
  const observed = readLeaseFromDir(dir);
  if (observed.status === "corrupt" && isFreshDirectory(dir, Number(options.nowMs ?? Date.now()))) {
    return { status: "active", reason: "active lease is still being initialized" };
  }
  const observedExpired = observed.status === "corrupt" || new Date(observed.expiresAt).getTime() <= Number(options.nowMs ?? Date.now());
  if (!observedExpired) {
    return { status: "active", lease: observed };
  }
  if (typeof options.beforeArchive === "function") {
    options.beforeArchive();
  }
  return withReapLock(root, normalized.pathHash, () => {
    if (!fs.existsSync(dir)) {
      return { status: "abort", reason: "active lease disappeared" };
    }
    const current = readLeaseFromDir(dir);
    if (current.id !== observed.id || ((current.status === "corrupt") !== (observed.status === "corrupt"))) {
      return { status: "abort", reason: "active lease changed before reap" };
    }
    const currentExpired = current.status === "corrupt" || new Date(current.expiresAt).getTime() <= Number(options.nowMs ?? Date.now());
    if (!currentExpired) {
      return { status: "abort", reason: "active lease is no longer expired" };
    }
    const archived = archiveActiveDir(root, dir, { ...current, pathHash: normalized.pathHash, id: current.id || "corrupt" });
    return { status: "reaped", archived };
  });
}

export function listLeases(cwd = process.cwd(), env = process.env) {
  const root = ensureLeaseDirs(cwd, env);
  pruneLeaseArchive(root);
  clearStaleReapLocks(cwd, env);
  const active = [];
  const corrupt = [];
  for (const entry of fs.readdirSync(path.join(root, "active"), { withFileTypes: true })) {
    if (!entry.isDirectory()) {
      continue;
    }
    const lease = readLeaseFromDir(path.join(root, "active", entry.name));
    if (lease.status === "corrupt") {
      corrupt.push({ key: entry.name, error: lease.stateError });
    } else {
      active.push(lease);
    }
  }
  active.sort((left, right) => String(left.path).localeCompare(String(right.path)));
  return { active, corrupt, degraded: false, primitive: "mkdir" };
}

export function leaseSummary(results = []) {
  return {
    enabled: results.length > 0,
    claimed: results.filter((item) => item.status === "claimed").length,
    conflicts: results.filter((item) => item.status === "conflict").length,
    degraded: results.some((item) => item.degraded || item.status === "degraded")
  };
}
