import { randomUUID, createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { leasesDirForCwd, atomicWriteJson } from "./state.mjs";
import { canonicalWorkspaceRoot } from "./workspace.mjs";

const ID_PATTERN = /^[A-Za-z0-9._-]+$/;
const ROLE_PATTERN = /^[a-z][a-z0-9-]{0,63}$/;
const MIN_TTL_MS = 30 * 1000;
const MAX_TTL_MS = 30 * 60 * 1000;

function validateId(id, label) {
  if (typeof id !== "string" || !ID_PATTERN.test(id)) {
    throw new Error(`Invalid ${label} "${id}".`);
  }
  return id;
}

function safeRealpath(candidate) {
  try {
    return fs.realpathSync.native(candidate);
  } catch {
    return path.resolve(candidate);
  }
}

function isInside(root, candidate) {
  return candidate === root || candidate.startsWith(`${root}${path.sep}`);
}

function nearestExistingAncestor(candidate) {
  let current = candidate;
  while (!fs.existsSync(current)) {
    const parent = path.dirname(current);
    if (parent === current) {
      return current;
    }
    current = parent;
  }
  return current;
}

function normalizeWorkspacePath(cwd, rawPath) {
  if (typeof rawPath !== "string" || !rawPath.trim()) {
    throw new Error("Lease path is required.");
  }
  const root = safeRealpath(canonicalWorkspaceRoot(cwd));
  const resolved = path.isAbsolute(rawPath)
    ? path.resolve(rawPath)
    : path.resolve(root, rawPath);
  const existing = nearestExistingAncestor(resolved);
  const existingReal = safeRealpath(existing);
  if (!isInside(root, existingReal)) {
    throw new Error("Lease path must resolve inside the workspace.");
  }
  const suffix = path.relative(existing, resolved);
  const targetReal = suffix ? path.resolve(existingReal, suffix) : existingReal;
  if (!isInside(root, targetReal)) {
    throw new Error("Lease path must resolve inside the workspace.");
  }
  return path.relative(root, targetReal) || ".";
}

function parseTtl(ttl) {
  if (typeof ttl === "number") {
    return ttl;
  }
  const match = String(ttl ?? "").trim().match(/^(\d+)(ms|s|m)?$/);
  if (!match) {
    throw new Error("Lease ttl must be a duration such as 60s or 10m.");
  }
  const value = Number(match[1]);
  const unit = match[2] ?? "ms";
  const ms = unit === "m" ? value * 60 * 1000 : unit === "s" ? value * 1000 : value;
  if (ms < MIN_TTL_MS || ms > MAX_TTL_MS) {
    throw new Error("Lease ttl must be between 30s and 30m.");
  }
  return ms;
}

function workspaceId(cwd) {
  return createHash("sha256").update(canonicalWorkspaceRoot(cwd)).digest("hex").slice(0, 16);
}

function ensureDirs(cwd, env) {
  const root = leasesDirForCwd(cwd, env);
  const active = path.join(root, "active");
  const archive = path.join(root, "archive");
  fs.mkdirSync(active, { recursive: true, mode: 0o700 });
  fs.mkdirSync(archive, { recursive: true, mode: 0o700 });
  return { root, active, archive };
}

function leaseKey(cwd, relativePath) {
  return createHash("sha256").update(`${workspaceId(cwd)}:${relativePath}`).digest("hex");
}

function activeLeasePath(cwd, relativePath, env) {
  return path.join(ensureDirs(cwd, env).active, `${leaseKey(cwd, relativePath)}.json`);
}

function archivePath(cwd, lease, env, label) {
  return path.join(ensureDirs(cwd, env).archive, `${lease.id}.${label}.${Date.now().toString(36)}.json`);
}

function readLeaseFile(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function expired(lease, now = Date.now()) {
  return Date.parse(lease.expiresAt) <= now;
}

function reapActiveFile(cwd, file, selected, env, label = "expired") {
  let current;
  try {
    current = readLeaseFile(file);
  } catch {
    current = null;
  }
  if (selected?.id && current?.id !== selected.id) {
    return false;
  }
  if (current && label === "expired" && !expired(current)) {
    return false;
  }
  const target = archivePath(cwd, current ?? { id: `corrupt-${randomUUID().slice(0, 8)}` }, env, label);
  try {
    fs.renameSync(file, target);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

export function claimLease(cwd, options, env = process.env) {
  const relativePath = normalizeWorkspacePath(cwd, options.path);
  const role = options.role ?? "manual";
  if (!ROLE_PATTERN.test(role)) {
    throw new Error(`Invalid lease role "${role}".`);
  }
  const jobId = options.jobId ? validateId(options.jobId, "job id") : "";
  const ttlMs = parseTtl(options.ttl ?? "60s");
  const now = new Date();
  const lease = {
    version: 1,
    id: `lease-${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`,
    path: relativePath,
    pathHash: `sha256:${leaseKey(cwd, relativePath)}`,
    role,
    jobId,
    holder: jobId || `pid-${process.pid}`,
    createdAt: now.toISOString(),
    expiresAt: new Date(now.getTime() + ttlMs).toISOString(),
    releasedAt: null,
    status: "active",
    workspaceId: workspaceId(cwd)
  };
  const dirs = ensureDirs(cwd, env);
  const active = activeLeasePath(cwd, relativePath, env);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const tmp = path.join(dirs.active, `${lease.id}.${process.pid}.${attempt}.tmp`);
    fs.writeFileSync(tmp, `${JSON.stringify(lease, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
    try {
      fs.linkSync(tmp, active);
      fs.unlinkSync(tmp);
      return { status: "claimed", lease };
    } catch (error) {
      fs.rmSync(tmp, { force: true });
      if (error.code !== "EEXIST") {
        throw error;
      }
    }
    let existing;
    try {
      existing = readLeaseFile(active);
    } catch {
      if (reapActiveFile(cwd, active, null, env, "corrupt")) {
        continue;
      }
      return { status: "degraded", reason: "Unable to inspect corrupt active lease." };
    }
    if (expired(existing)) {
      reapActiveFile(cwd, active, existing, env, "expired");
      continue;
    }
    return { status: "conflict", lease: existing };
  }
  return { status: "conflict", lease: fs.existsSync(active) ? readLeaseFile(active) : null };
}

export function listLeases(cwd = process.cwd(), env = process.env) {
  const dirs = ensureDirs(cwd, env);
  const leases = [];
  const corrupt = [];
  for (const fileName of fs.readdirSync(dirs.active).filter((name) => name.endsWith(".json"))) {
    const file = path.join(dirs.active, fileName);
    try {
      const lease = readLeaseFile(file);
      if (expired(lease)) {
        reapActiveFile(cwd, file, lease, env, "expired");
      } else {
        leases.push(lease);
      }
    } catch (error) {
      corrupt.push({ file: fileName, error: error.message || String(error) });
    }
  }
  return { leasesDir: leasesDirForCwd(cwd, env), leases, corrupt };
}

export function releaseLease(cwd, leaseId, env = process.env, options = {}) {
  validateId(leaseId, "lease id");
  const dirs = ensureDirs(cwd, env);
  for (const fileName of fs.readdirSync(dirs.active).filter((name) => name.endsWith(".json"))) {
    const file = path.join(dirs.active, fileName);
    let lease;
    try {
      lease = readLeaseFile(file);
    } catch {
      continue;
    }
    if (lease.id !== leaseId) {
      continue;
    }
    if (!options.manual && options.holder && lease.holder !== options.holder) {
      return { status: "not_released", reason: "holder mismatch", leaseId };
    }
    const current = readLeaseFile(file);
    if (current.id !== leaseId) {
      return { status: "not_released", reason: "lease changed before release", leaseId };
    }
    const released = {
      ...current,
      status: "released",
      releasedAt: new Date().toISOString()
    };
    const target = archivePath(cwd, released, env, "released");
    atomicWriteJson(target, released);
    fs.unlinkSync(file);
    return { status: "released", lease: released };
  }
  return { status: "not_found", leaseId };
}
