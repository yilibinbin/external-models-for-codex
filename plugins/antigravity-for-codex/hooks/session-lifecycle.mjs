#!/usr/bin/env node

import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { stateDirForCwd } from "../scripts/lib/state.mjs";

const LOCK_WAIT_MS = 1000;
const LOCK_STALE_MS = 30_000;

function sleepMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function acquireLock(file) {
  const lockFile = `${file}.lock`;
  const deadline = Date.now() + LOCK_WAIT_MS;
  while (Date.now() <= deadline) {
    try {
      const handle = fs.openSync(lockFile, "wx");
      fs.writeFileSync(handle, JSON.stringify({ pid: process.pid, createdAt: new Date().toISOString() }));
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

function writeMarker(event) {
  const dir = stateDirForCwd(process.cwd(), process.env);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "session-lifecycle.json");
  const lock = acquireLock(file);
  if (!lock) {
    throw new Error("Lifecycle state is busy.");
  }
  try {
  let payload = { events: [] };
  try {
    payload = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    payload = { events: [] };
  }
  if (!Array.isArray(payload.events)) {
    payload.events = [];
  }
  payload.events.push({ event, at: new Date().toISOString() });
  payload.events = payload.events.slice(-20);
  const tmp = `${file}.${process.pid}.${randomBytes(4).toString("hex")}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, file);
  } finally {
    releaseLock(lock);
  }
}

try {
  const event = process.argv[2] === "end" ? "end" : "start";
  writeMarker(event);
} catch (error) {
  process.stderr.write(`[antigravity-for-codex lifecycle] ${error.message || String(error)}\n`);
}
