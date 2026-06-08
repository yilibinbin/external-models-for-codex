import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { stateDirForCwd } from "./state.mjs";

const MESSAGE_LIMIT = 16 * 1024;
const THREAD_LIMIT = 120;
const MESSAGE_HISTORY_LIMIT = 100;
const LOCK_WAIT_MS = 1000;
const LOCK_STALE_MS = 30_000;

function mailboxDir(cwd = process.cwd(), env = process.env) {
  const dir = path.join(stateDirForCwd(cwd, env), "mailbox");
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function cleanText(value, limit = MESSAGE_LIMIT) {
  return String(value || "")
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "")
    .slice(0, limit)
    .trim();
}

function safeThreadId(value) {
  const cleaned = cleanText(value, THREAD_LIMIT).replace(/[^a-zA-Z0-9._-]+/g, "-");
  if (!cleaned) {
    throw new Error("Mailbox thread is required.");
  }
  return cleaned;
}

function threadPath(thread, cwd = process.cwd(), env = process.env) {
  return path.join(mailboxDir(cwd, env), `${safeThreadId(thread)}.json`);
}

function now() {
  return new Date().toISOString();
}

function writeJsonAtomic(file, payload) {
  const tmp = `${file}.${process.pid}.${randomBytes(4).toString("hex")}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, file);
  return payload;
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
      fs.writeFileSync(handle, JSON.stringify({ pid: process.pid, createdAt: now() }));
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

function withFileLock(file, callback) {
  const lock = acquireLock(file);
  if (!lock) {
    throw new Error("Mailbox state is busy.");
  }
  try {
    return callback();
  } finally {
    releaseLock(lock);
  }
}

function readThread(thread, cwd = process.cwd(), env = process.env) {
  const id = safeThreadId(thread);
  const file = threadPath(id, cwd, env);
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") {
      return { thread: id, messages: [], updatedAt: "" };
    }
    throw error;
  }
}

export function postMailboxMessage({ thread, message, role = "codex", cwd = process.cwd() }, env = process.env) {
  const id = safeThreadId(thread);
  const body = cleanText(message);
  if (!body) {
    throw new Error("Mailbox message is required.");
  }
  const file = threadPath(id, cwd, env);
  return withFileLock(file, () => {
    const current = readThread(id, cwd, env);
    const createdAt = now();
    const entry = {
      id: `msg-${Date.now().toString(36)}-${randomBytes(4).toString("hex")}`,
      role: cleanText(role, 60) || "codex",
      message: body,
      createdAt
    };
    current.messages.push(entry);
    const overflow = Math.max(0, current.messages.length - MESSAGE_HISTORY_LIMIT);
    if (overflow > 0) {
      current.truncated = true;
      current.droppedMessages = Number(current.droppedMessages || 0) + overflow;
      current.retainedMessages = MESSAGE_HISTORY_LIMIT;
    }
    current.messages = current.messages.slice(-MESSAGE_HISTORY_LIMIT);
    current.updatedAt = createdAt;
    writeJsonAtomic(file, current);
    return { status: "posted", thread: id, message: entry };
  });
}

export function listMailboxThreads(cwd = process.cwd(), env = process.env) {
  const dir = mailboxDir(cwd, env);
  const threads = fs.readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => {
      try {
        const payload = JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
        return {
          thread: payload.thread || name.slice(0, -5),
          messageCount: Array.isArray(payload.messages) ? payload.messages.length : 0,
          truncated: Boolean(payload.truncated),
          droppedMessages: Number(payload.droppedMessages || 0),
          retainedMessages: Number(payload.retainedMessages || 0),
          updatedAt: payload.updatedAt || ""
        };
      } catch {
        return null;
      }
    })
    .filter(Boolean)
    .sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
  return { threads };
}

export function showMailboxThread(thread, cwd = process.cwd(), env = process.env) {
  return readThread(thread, cwd, env);
}
