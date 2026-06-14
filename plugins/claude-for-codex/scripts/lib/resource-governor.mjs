import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";

export const CAPACITY_BLOCKED_STATUS = "capacity_blocked";
export const CAPACITY_BLOCKED_EXIT_CODE = 75;

const DEFAULT_TTL_MS = 900 * 1000;
const MAX_ENV_CAPACITY = 64;
const ORPHAN_SLOT_GRACE_MS = 5000;
const RECLAIM_LOCK_GRACE_MS = 5000;
const LOCK_SUBDIR = "claude-process";

let testHooks = {};

function nowMs() {
  return typeof testHooks.now === "function" ? Number(testHooks.now()) : Date.now();
}

function isoNow(now = nowMs()) {
  return new Date(now).toISOString();
}

function sha256Short(value) {
  return createHash("sha256").update(String(value ?? "")).digest("hex").slice(0, 16);
}

function defaultCapacity() {
  const parallelism = typeof os.availableParallelism === "function"
    ? os.availableParallelism()
    : Array.isArray(os.cpus()) && os.cpus().length
    ? os.cpus().length
    : 5;
  return Math.max(1, Math.min(8, parallelism || 5));
}

function directoryIdentity(stat) {
  return `${stat.dev}:${stat.ino}`;
}

function parseCapacity(value) {
  if (value === undefined || value === null || String(value).trim() === "") {
    return defaultCapacity();
  }
  if (!/^\d+$/.test(String(value).trim())) {
    return defaultCapacity();
  }
  return Math.min(Number(String(value).trim()), MAX_ENV_CAPACITY);
}

function leaseTtlMs(env = process.env) {
  const rawMs = env.CLAUDE_FOR_CODEX_RESOURCE_LEASE_TTL_MS;
  if (/^\d+$/.test(String(rawMs ?? ""))) {
    return Math.max(1000, Math.min(Number(rawMs), 24 * 60 * 60 * 1000));
  }
  const rawSeconds = env.CLAUDE_FOR_CODEX_RESOURCE_LEASE_TTL_SECONDS;
  if (/^\d+$/.test(String(rawSeconds ?? ""))) {
    return Math.max(1, Math.min(Number(rawSeconds), 24 * 60 * 60)) * 1000;
  }
  return DEFAULT_TTL_MS;
}

function effectiveLeaseTtlMs(env = process.env, minimumTtlMs = 0) {
  const floor = Number(minimumTtlMs);
  const boundedFloor = Number.isFinite(floor) && floor > 0
    ? Math.min(floor, 24 * 60 * 60 * 1000)
    : 0;
  return Math.max(leaseTtlMs(env), boundedFloor);
}

function staleExpiredLease(now, expiresAtMs) {
  return Number.isFinite(expiresAtMs) && expiresAtMs > 0 && now - expiresAtMs > ORPHAN_SLOT_GRACE_MS;
}

export function defaultResourceLockRoot(env = process.env) {
  const override = env.CLAUDE_FOR_CODEX_GLOBAL_RESOURCE_LOCK_DIR;
  if (override !== undefined && String(override).trim() !== "") {
    return path.resolve(String(override));
  }
  return path.join(os.homedir(), ".codex", "claude-for-codex", "global-resource-locks");
}

export function effectiveMaxClaudeProcesses(env = process.env) {
  return parseCapacity(env.CLAUDE_FOR_CODEX_MAX_CLAUDE_PROCESSES);
}

function resolvedLockRootClass(env = process.env) {
  const override = env.CLAUDE_FOR_CODEX_GLOBAL_RESOURCE_LOCK_DIR;
  return override !== undefined && String(override).trim() !== "" ? "env-override" : "user-codex-data";
}

function invalidStore(reason) {
  return {
    ok: false,
    reason,
    lockRootClass: "invalid",
    processRoot: "",
    root: ""
  };
}

function validateOverride(value) {
  if (value === undefined || value === null || String(value).trim() === "") {
    return null;
  }
  const raw = String(value);
  if (raw.includes("\0")) {
    return "invalid lock root override";
  }
  if (!path.isAbsolute(raw)) {
    return "lock root override must be absolute";
  }
  return null;
}

function validateDirectory(directory, { create = false, className = "user-codex-data" } = {}) {
  try {
    if (!fs.existsSync(directory)) {
      if (!create) {
        return { ok: false, reason: "lock directory is missing" };
      }
      fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    }
    const stat = fs.lstatSync(directory);
    if (stat.isSymbolicLink()) {
      return { ok: false, reason: "lock directory must not be a symlink" };
    }
    if (!stat.isDirectory()) {
      return { ok: false, reason: "lock directory must be a directory" };
    }
    if ((stat.mode & 0o022) !== 0) {
      return { ok: false, reason: "lock directory must not be group/world writable" };
    }
    if (typeof process.getuid === "function" && Number.isInteger(stat.uid) && stat.uid !== process.getuid()) {
      return { ok: false, reason: "lock directory owner does not match current user" };
    }
    return { ok: true, lockRootClass: className };
  } catch {
    return { ok: false, reason: "lock directory could not be validated" };
  }
}

function prepareStore(env = process.env) {
  const className = resolvedLockRootClass(env);
  const overrideError = validateOverride(env.CLAUDE_FOR_CODEX_GLOBAL_RESOURCE_LOCK_DIR);
  if (overrideError) {
    return invalidStore(overrideError);
  }
  const root = defaultResourceLockRoot(env);
  const rootValidation = validateDirectory(root, { create: true, className });
  if (!rootValidation.ok) {
    return invalidStore(rootValidation.reason);
  }
  const processRoot = path.join(root, LOCK_SUBDIR);
  const processValidation = validateDirectory(processRoot, { create: true, className });
  if (!processValidation.ok) {
    return invalidStore(processValidation.reason);
  }
  return {
    ok: true,
    root,
    processRoot,
    lockRootClass: className
  };
}

function workspaceMetadata(workspace = process.cwd()) {
  if (typeof workspace !== "string" || workspace.trim() === "") {
    return { workspaceClass: "unknown", workspaceHash: "" };
  }
  const resolved = path.resolve(workspace);
  return {
    workspaceClass: path.isAbsolute(resolved) ? "path" : "unknown",
    workspaceHash: `sha256:${sha256Short(resolved)}`
  };
}

function slotPath(store, slotIndex) {
  return path.join(store.processRoot, `slot-${slotIndex}`);
}

function leasePath(slot) {
  return path.join(slot, "lease.json");
}

function claimPath(slot) {
  return path.join(slot, "claim.json");
}

function reclaimLockPath(slot) {
  return path.join(slot, "reclaim.lock");
}

function readJsonFile(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

function statMtimeMs(file) {
  try {
    return fs.statSync(file).mtimeMs;
  } catch {
    return 0;
  }
}

function readLease(slot) {
  const file = leasePath(slot);
  if (!fs.existsSync(file)) {
    return { state: "missing", identity: `missing:${statMtimeMs(slot)}` };
  }
  const parsed = readJsonFile(file);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { state: "malformed", identity: `malformed:${statMtimeMs(file)}` };
  }
  const identity = `${String(parsed.leaseId ?? "")}:${String(parsed.startedAt ?? "")}`;
  return { state: "ok", lease: parsed, identity };
}

function defaultProbePid(pid) {
  if (!Number.isInteger(pid) || pid <= 0) {
    return { state: "invalid" };
  }
  try {
    process.kill(pid, 0);
    return { state: "live" };
  } catch (error) {
    if (error?.code === "ESRCH") {
      return { state: "dead" };
    }
    if (error?.code === "EPERM") {
      return { state: "live", unknown: true };
    }
    return { state: "live", unknown: true, errorCode: error?.code ? String(error.code) : "UNKNOWN" };
  }
}

function probePid(pid) {
  if (typeof testHooks.probePid === "function") {
    const probed = testHooks.probePid(pid);
    if (typeof probed === "string") {
      return { state: probed };
    }
    if (probed && typeof probed === "object") {
      return probed;
    }
  }
  return defaultProbePid(pid);
}

function classifySlot(store, slotIndex, env = process.env) {
  const slot = slotPath(store, slotIndex);
  try {
    const stat = fs.lstatSync(slot);
    if (stat.isSymbolicLink() || !stat.isDirectory()) {
      return { state: "busy", reason: "slot is not a directory", slotIndex, slot };
    }
  } catch (error) {
    if (error?.code === "ENOENT") {
      return { state: "free", slotIndex, slot };
    }
    return { state: "busy", reason: "slot could not be inspected", slotIndex, slot };
  }

  const lease = readLease(slot);
  if (lease.state === "missing") {
    const ageMs = Math.max(0, nowMs() - statMtimeMs(slot));
    return ageMs < ORPHAN_SLOT_GRACE_MS
      ? { state: "busy", reason: "orphan slot grace", slotIndex, slot }
      : { state: "stale", reason: "orphan slot", slotIndex, slot, identity: lease.identity };
  }
  if (lease.state === "malformed") {
    return { state: "stale", reason: "malformed lease", slotIndex, slot, identity: lease.identity };
  }

  const pid = Number(lease.lease.pid);
  if (!Number.isInteger(pid) || pid <= 0) {
    return { state: "stale", reason: "malformed pid", slotIndex, slot, identity: lease.identity, lease: lease.lease };
  }
  const expiresAtMs = Number(lease.lease.expiresAtMs ?? 0);
  const probed = probePid(pid);
  if (probed.state === "dead" || probed.state === "invalid") {
    return { state: "stale", reason: "dead pid", slotIndex, slot, identity: lease.identity, lease: lease.lease };
  }
  const expired = expiresAtMs <= nowMs();
  return {
    state: "active",
    slotIndex,
    slot,
    identity: lease.identity,
    lease: lease.lease,
    expired,
    staleExpired: staleExpiredLease(nowMs(), expiresAtMs),
    unknown: Boolean(probed.unknown)
  };
}

function currentLease({ slotIndex, leaseId, surface, command, workspace, ttlMs }) {
  const now = nowMs();
  return {
    version: 1,
    leaseId,
    slot: slotIndex,
    pid: process.pid,
    startedAt: isoNow(now),
    startedAtMs: now,
    refreshedAt: isoNow(now),
    refreshedAtMs: now,
    expiresAt: isoNow(now + ttlMs),
    expiresAtMs: now + ttlMs,
    ttlMs,
    surface: String(surface ?? ""),
    command: String(command ?? ""),
    ...workspaceMetadata(workspace),
    owner: "claude-for-codex"
  };
}

function slotDirectoryIdentity(slot) {
  try {
    const stat = fs.lstatSync(slot);
    if (stat.isSymbolicLink() || !stat.isDirectory()) {
      return "";
    }
    return directoryIdentity(stat);
  } catch {
    return "";
  }
}

function slotClaimLostError() {
  const error = new Error("slot claim was lost before lease write");
  error.code = "ECLAIMLOST";
  return error;
}

function writeClaim(slot, identity) {
  const fd = fs.openSync(claimPath(slot), "wx", 0o600);
  try {
    fs.writeFileSync(fd, `${JSON.stringify({ identity, pid: process.pid, startedAtMs: nowMs() })}\n`, "utf8");
  } finally {
    fs.closeSync(fd);
  }
}

function sameClaim(slot, identity) {
  return readJsonFile(claimPath(slot))?.identity === identity;
}

function removeSlotIfClaimIdentity(slot, expectedDirectoryIdentity, expectedClaimIdentity = "") {
  if (!expectedDirectoryIdentity || slotDirectoryIdentity(slot) !== expectedDirectoryIdentity) {
    return false;
  }
  if (expectedClaimIdentity && !sameClaim(slot, expectedClaimIdentity)) {
    return false;
  }
  fs.rmSync(slot, { recursive: true, force: true });
  return true;
}

function hardlinkUnsupported(error) {
  return ["EPERM", "ENOTSUP", "EOPNOTSUPP", "EMLINK"].includes(String(error?.code ?? ""));
}

function commitExclusiveLease(tmp, file) {
  try {
    fs.linkSync(tmp, file);
    fs.rmSync(tmp, { force: true });
  } catch (error) {
    if (!hardlinkUnsupported(error)) {
      throw error;
    }
    fs.renameSync(tmp, file);
  }
}

function writeLease(slot, lease, { expectedDirectoryIdentity = "", expectedClaimIdentity = "", exclusive = false } = {}) {
  const file = leasePath(slot);
  const tmp = `${file}.${process.pid}.${Math.random().toString(16).slice(2)}.tmp`;
  try {
    if (typeof testHooks.beforeWriteLease === "function") {
      testHooks.beforeWriteLease({
        slotPath: slot,
        leaseId: lease.leaseId,
        identity: `${lease.leaseId}:${lease.startedAt}`,
        expectedDirectoryIdentity,
        exclusive
      });
    }
    if (expectedDirectoryIdentity && slotDirectoryIdentity(slot) !== expectedDirectoryIdentity) {
      throw slotClaimLostError();
    }
    if (expectedClaimIdentity && !sameClaim(slot, expectedClaimIdentity)) {
      throw slotClaimLostError();
    }
    fs.writeFileSync(tmp, `${JSON.stringify(lease, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
    if (expectedDirectoryIdentity && slotDirectoryIdentity(slot) !== expectedDirectoryIdentity) {
      throw slotClaimLostError();
    }
    if (expectedClaimIdentity && !sameClaim(slot, expectedClaimIdentity)) {
      throw slotClaimLostError();
    }
    if (exclusive) {
      commitExclusiveLease(tmp, file);
    } else {
      fs.renameSync(tmp, file);
    }
    if (expectedDirectoryIdentity && slotDirectoryIdentity(slot) !== expectedDirectoryIdentity) {
      throw slotClaimLostError();
    }
    if (expectedClaimIdentity && !sameClaim(slot, expectedClaimIdentity)) {
      throw slotClaimLostError();
    }
  } catch (error) {
    try {
      fs.rmSync(tmp, { force: true });
    } catch {
      // Best-effort cleanup only.
    }
    throw error;
  }
}

function sameIdentity(slot, identity) {
  return readLease(slot).identity === identity;
}

function stillMatchesStaleClassification(classification) {
  if (classification.reason === "orphan slot") {
    return readLease(classification.slot).state === "missing";
  }
  return sameIdentity(classification.slot, classification.identity);
}

function removeOwnReclaimLock(lockFile, reclaimId) {
  const current = readJsonFile(lockFile);
  if (current?.reclaimId !== reclaimId) {
    return false;
  }
  try {
    fs.rmSync(lockFile, { force: true });
    return true;
  } catch {
    return false;
  }
}

function existingReclaimLockBlocks(lockFile) {
  let stat;
  try {
    stat = fs.statSync(lockFile);
  } catch (error) {
    if (error?.code === "ENOENT") {
      return false;
    }
    return true;
  }
  if (!stat.isFile()) {
    return false;
  }
  const ageMs = Math.max(0, nowMs() - stat.mtimeMs);
  const owner = readJsonFile(lockFile);
  const pid = Number(owner?.pid);
  const probed = probePid(pid);
  if (ageMs > RECLAIM_LOCK_GRACE_MS && (probed.state === "dead" || probed.state === "invalid")) {
    try {
      fs.rmSync(lockFile, { force: true });
      return false;
    } catch {
      return true;
    }
  }
  return true;
}

function reclaimStaleSlot(classification) {
  const lockFile = reclaimLockPath(classification.slot);
  if (existingReclaimLockBlocks(lockFile)) {
    return false;
  }
  const reclaimId = randomUUID();
  let fd;
  try {
    fd = fs.openSync(lockFile, "wx", 0o600);
    fs.writeFileSync(fd, `${JSON.stringify({
      reclaimId,
      pid: process.pid,
      startedAt: isoNow(),
      startedAtMs: nowMs()
    })}\n`, "utf8");
  } catch (error) {
    if (error?.code === "EEXIST") {
      return false;
    }
    return false;
  } finally {
    if (fd !== undefined) {
      try {
        fs.closeSync(fd);
      } catch {
        // Best-effort close only.
      }
    }
  }

  try {
    if (typeof testHooks.afterClassifyStaleSlot === "function") {
      testHooks.afterClassifyStaleSlot({
        slotIndex: classification.slotIndex,
        slotPath: classification.slot,
        reclaimId,
        identity: classification.identity
      });
    }
    if (!stillMatchesStaleClassification(classification)) {
      removeOwnReclaimLock(lockFile, reclaimId);
      return false;
    }
    const tombstone = `${classification.slot}.reclaimed-${reclaimId}`;
    fs.renameSync(classification.slot, tombstone);
    fs.rmSync(tombstone, { recursive: true, force: true });
    return true;
  } catch {
    removeOwnReclaimLock(lockFile, reclaimId);
    return false;
  }
}

function startRefreshTimer(token, ttlMs) {
  const refreshEvery = Math.max(250, Math.min(60_000, Math.floor(ttlMs / 3), Math.floor(ttlMs / 2)));
  const timer = setInterval(() => {
    try {
      if (!token.refresh()) {
        clearInterval(timer);
        token._notifyLost("capacity lease refresh failed");
      }
    } catch (error) {
      clearInterval(timer);
      token._notifyLost(error);
      // A failed refresh should not keep a process alive or throw from a timer.
    }
  }, refreshEvery);
  timer.unref?.();
  return timer;
}

function createSlotToken({ store, slotIndex, lease, ttlMs, directoryIdentity: claimedDirectoryIdentity = "", claimIdentity = "" }) {
  const lostHandlers = new Set();
  const token = {
    slot: slotIndex,
    leaseId: lease.leaseId,
    lockRootClass: store.lockRootClass,
    released: false,
    lost: false,
    onLost(callback) {
      if (typeof callback !== "function") {
        return () => {};
      }
      if (this.lost) {
        callback(new Error("capacity lease was already lost"));
        return () => {};
      }
      lostHandlers.add(callback);
      return () => lostHandlers.delete(callback);
    },
    _notifyLost(reason) {
      if (this.released || this.lost) {
        return;
      }
      this.lost = true;
      const error = reason instanceof Error ? reason : new Error(String(reason || "capacity lease was lost"));
      for (const callback of [...lostHandlers]) {
        try {
          callback(error);
        } catch {
          // Lease loss notifications are best-effort.
        }
      }
      lostHandlers.clear();
    },
    refresh() {
      if (this.released) {
        return false;
      }
      const current = readLease(slotPath(store, slotIndex));
      if (current.identity !== `${lease.leaseId}:${lease.startedAt}`) {
        return false;
      }
      if (claimIdentity && !sameClaim(slotPath(store, slotIndex), claimIdentity)) {
        return false;
      }
      const nextNow = nowMs();
      const refreshed = {
        ...current.lease,
        refreshedAt: isoNow(nextNow),
        refreshedAtMs: nextNow,
        expiresAt: isoNow(nextNow + ttlMs),
        expiresAtMs: nextNow + ttlMs
      };
      writeLease(slotPath(store, slotIndex), refreshed, { expectedDirectoryIdentity: claimedDirectoryIdentity, expectedClaimIdentity: claimIdentity });
      return true;
    },
    release() {
      if (this.released) {
        return false;
      }
      this.released = true;
      if (this._refreshTimer) {
        clearInterval(this._refreshTimer);
      }
      const slot = slotPath(store, slotIndex);
      const current = readLease(slot);
      if (claimedDirectoryIdentity && slotDirectoryIdentity(slot) !== claimedDirectoryIdentity) {
        return false;
      }
      if (claimIdentity && !sameClaim(slot, claimIdentity)) {
        return false;
      }
      if (current.identity !== `${lease.leaseId}:${lease.startedAt}`) {
        return false;
      }
      try {
        fs.rmSync(slot, { recursive: true, force: true });
        return true;
      } catch {
        return false;
      }
    }
  };
  token._refreshTimer = startRefreshTimer(token, ttlMs);
  return token;
}

function tryClaimSlot(store, slotIndex, options) {
  const slot = slotPath(store, slotIndex);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      fs.mkdirSync(slot, { mode: 0o700 });
      const claimedDirectoryIdentity = slotDirectoryIdentity(slot);
      const leaseId = randomUUID();
      const lease = currentLease({
        slotIndex,
        leaseId,
        surface: options.surface,
        command: options.command,
        workspace: options.workspace,
        ttlMs: options.ttlMs
      });
      const claimIdentity = `${lease.leaseId}:${lease.startedAt}`;
      writeClaim(slot, claimIdentity);
      try {
        writeLease(slot, lease, { expectedDirectoryIdentity: claimedDirectoryIdentity, expectedClaimIdentity: claimIdentity, exclusive: true });
      } catch (error) {
        removeSlotIfClaimIdentity(slot, claimedDirectoryIdentity, claimIdentity);
        throw error;
      }
      return createSlotToken({ store, slotIndex, lease, ttlMs: options.ttlMs, directoryIdentity: claimedDirectoryIdentity, claimIdentity });
    } catch (error) {
      if (error?.code !== "EEXIST") {
        return null;
      }
      const classification = classifySlot(store, slotIndex, options.env);
      if (classification.state !== "stale" || !reclaimStaleSlot(classification)) {
        return null;
      }
    }
  }
  return null;
}

function summarizeActiveLease(classification) {
  const lease = classification.lease ?? {};
  return {
    slot: classification.slotIndex,
    pid: Number.isInteger(Number(lease.pid)) ? Number(lease.pid) : undefined,
    surface: typeof lease.surface === "string" ? lease.surface : "",
    command: typeof lease.command === "string" ? lease.command : "",
    workspaceClass: typeof lease.workspaceClass === "string" ? lease.workspaceClass : "",
    workspaceHash: typeof lease.workspaceHash === "string" ? lease.workspaceHash : "",
    expired: Boolean(classification.expired),
    unknownLiveness: Boolean(classification.unknown)
  };
}

function inspectSlots(store, max, env = process.env) {
  const activeLeases = [];
  let availableSlots = 0;
  for (let slotIndex = 0; slotIndex < max; slotIndex += 1) {
    const classification = classifySlot(store, slotIndex, env);
    if (classification.state === "free" || classification.state === "stale") {
      availableSlots += 1;
    } else if (classification.state === "active") {
      activeLeases.push(summarizeActiveLease(classification));
    }
  }
  return { activeLeases, availableSlots };
}

export function capacityBlockedResult({
  surface = "",
  command = "",
  requestedSlots = 1,
  availableSlots = 0,
  lockRootClass = "invalid",
  effectiveMax = 0,
  reason = "Claude process capacity is exhausted."
} = {}) {
  return {
    status: CAPACITY_BLOCKED_STATUS,
    capacityStatus: CAPACITY_BLOCKED_STATUS,
    surface,
    command,
    requestedSlots,
    availableSlots: Math.max(0, availableSlots),
    effectiveMax,
    lockRootClass,
    reason
  };
}

export function availableClaudeCapacity({ env = process.env } = {}) {
  return inspectResourceGovernor({ env });
}

export function inspectResourceGovernor({ env = process.env } = {}) {
  const effectiveMax = effectiveMaxClaudeProcesses(env);
  const store = prepareStore(env);
  if (!store.ok) {
    return {
      ok: false,
      enabled: true,
      effectiveMax,
      maxClaudeProcesses: effectiveMax,
      lockRootClass: "invalid",
      activeLeases: [],
      activeLeaseCount: 0,
      availableSlots: 0,
      reason: store.reason
    };
  }
  const slots = inspectSlots(store, effectiveMax, env);
  return {
    ok: true,
    enabled: true,
    effectiveMax,
    maxClaudeProcesses: effectiveMax,
    lockRootClass: store.lockRootClass,
    activeLeases: slots.activeLeases,
    activeLeaseCount: slots.activeLeases.length,
    availableSlots: effectiveMax === 0 ? 0 : slots.availableSlots
  };
}

export function acquireClaudeCapacity({
  slots = 1,
  surface = "",
  command = "",
  workspace = process.cwd(),
  env = process.env,
  minimumTtlMs = 0
} = {}) {
  const requestedSlots = Math.max(1, Number.isFinite(Number(slots)) ? Math.trunc(Number(slots)) : 1);
  const effectiveMax = effectiveMaxClaudeProcesses(env);
  const store = prepareStore(env);
  if (!store.ok) {
    return {
      ok: false,
      ...capacityBlockedResult({
        surface,
        command,
        requestedSlots,
        availableSlots: 0,
        effectiveMax,
        lockRootClass: "invalid",
        reason: store.reason
      })
    };
  }
  if (effectiveMax <= 0 || requestedSlots > effectiveMax) {
    const available = effectiveMax <= 0 ? 0 : inspectSlots(store, effectiveMax, env).availableSlots;
    return {
      ok: false,
      ...capacityBlockedResult({
        surface,
        command,
        requestedSlots,
        availableSlots: available,
        effectiveMax,
        lockRootClass: store.lockRootClass,
        reason: effectiveMax <= 0
          ? "Claude process capacity is configured to zero."
          : "Requested Claude process capacity exceeds the configured maximum."
      })
    };
  }

  const acquired = [];
  const ttlMs = effectiveLeaseTtlMs(env, minimumTtlMs);
  for (let count = 0; count < requestedSlots; count += 1) {
    let token = null;
    for (let slotIndex = 0; slotIndex < effectiveMax; slotIndex += 1) {
      if (acquired.some((entry) => entry.slot === slotIndex)) {
        continue;
      }
      token = tryClaimSlot(store, slotIndex, { surface, command, workspace, env, ttlMs });
      if (token) {
        break;
      }
    }
    if (!token) {
      for (const entry of acquired) {
        entry.release();
      }
      const current = inspectSlots(store, effectiveMax, env);
      return {
        ok: false,
        ...capacityBlockedResult({
          surface,
          command,
          requestedSlots,
          availableSlots: current.availableSlots,
          effectiveMax,
          lockRootClass: store.lockRootClass,
          reason: "Claude process capacity is exhausted."
        })
      };
    }
    acquired.push(token);
  }

  const after = inspectSlots(store, effectiveMax, env);
  return {
    ok: true,
    status: "acquired",
    capacityStatus: "acquired",
    requestedSlots,
    availableSlots: after.availableSlots,
    effectiveMax,
    lockRootClass: store.lockRootClass,
    leases: acquired,
    release() {
      let released = false;
      for (const entry of acquired) {
        released = entry.release() || released;
      }
      return released;
    },
    onLost(callback) {
      if (typeof callback !== "function") {
        return () => {};
      }
      let notified = false;
      const unsubscribers = acquired.map((entry) => entry.onLost((error) => {
        if (notified) {
          return;
        }
        notified = true;
        callback(error);
      }));
      return () => {
        for (const unsubscribe of unsubscribers) {
          unsubscribe();
        }
      };
    },
    refresh() {
      return acquired.map((entry) => entry.refresh()).every(Boolean);
    }
  };
}

export async function withClaudeCapacity(options, operation) {
  const lease = acquireClaudeCapacity(options);
  if (!lease.ok) {
    return lease;
  }
  try {
    return await operation(lease);
  } finally {
    lease.release();
  }
}

function isTestHooksAllowed() {
  return process.env.NODE_ENV === "test" || process.env.CLAUDE_FOR_CODEX_TEST_HOOKS === "1";
}

export function refreshLeaseForTest(lease) {
  if (!isTestHooksAllowed()) {
    throw new Error("resource governor test helpers are only available in tests.");
  }
  if (lease && typeof lease.refresh === "function") {
    return lease.refresh();
  }
  throw new Error("refreshLeaseForTest requires a lease object.");
}

export function setResourceGovernorTestHooksForTest(hooks = {}) {
  if (!isTestHooksAllowed()) {
    throw new Error("resource governor test hooks are only available in tests.");
  }
  if (!hooks || Object.keys(hooks).length === 0) {
    testHooks = {};
    return;
  }
  for (const [key, value] of Object.entries(hooks)) {
    if (!["probePid", "now", "afterClassifyStaleSlot", "beforeWriteLease"].includes(key) || typeof value !== "function") {
      throw new Error(`Invalid resource governor test hook: ${key}`);
    }
  }
  testHooks = { ...hooks };
}
