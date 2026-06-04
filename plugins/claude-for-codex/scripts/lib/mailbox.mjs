import { randomUUID, createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { mailboxDirForCwd, atomicWriteJson } from "./state.mjs";
import { sanitizeSummary } from "./sanitize.mjs";
import { canonicalWorkspaceRoot } from "./workspace.mjs";

const ID_PATTERN = /^[A-Za-z0-9._-]+$/;
const ROLE_PATTERN = /^[a-z][a-z0-9-]{0,63}$/;
const STATUSES = new Set(["running", "succeeded", "failed", "cancelled", "note"]);
const SOURCES = new Set(["runtime", "hook", "manual"]);

function validateId(id, label) {
  if (typeof id !== "string" || !ID_PATTERN.test(id)) {
    throw new Error(`Invalid ${label} "${id}".`);
  }
  return id;
}

function workspaceId(cwd) {
  return createHash("sha256").update(canonicalWorkspaceRoot(cwd)).digest("hex").slice(0, 16);
}

function threadsDir(cwd, env) {
  const dir = path.join(mailboxDirForCwd(cwd, env), "threads");
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  return dir;
}

function threadDir(cwd, threadId, env) {
  validateId(threadId, "thread id");
  const dir = path.join(threadsDir(cwd, env), threadId);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  return dir;
}

function normalizeMessage(cwd, message) {
  const threadId = validateId(message.threadId ?? message.jobId ?? `thread-${Date.now().toString(36)}`, "thread id");
  const jobId = message.jobId === undefined || message.jobId === "" ? "" : validateId(message.jobId, "job id");
  const id = validateId(message.id ?? `msg-${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`, "message id");
  const role = message.role ?? "";
  if (role && !ROLE_PATTERN.test(role)) {
    throw new Error(`Invalid role "${role}".`);
  }
  const command = message.command ?? "mailbox";
  if (!ROLE_PATTERN.test(command)) {
    throw new Error(`Invalid command "${command}".`);
  }
  const status = message.status ?? "note";
  if (!STATUSES.has(status)) {
    throw new Error(`Invalid mailbox status "${status}".`);
  }
  const source = message.source ?? "runtime";
  if (!SOURCES.has(source)) {
    throw new Error(`Invalid mailbox source "${source}".`);
  }
  return {
    version: 1,
    id,
    threadId,
    jobId,
    role,
    command,
    status,
    summary: sanitizeSummary(message.summary ?? "", { cwd }),
    severityCounts: message.severityCounts ?? { high: 0, medium: 0, low: 0 },
    createdAt: message.createdAt ?? new Date().toISOString(),
    workspaceId: workspaceId(cwd),
    source
  };
}

export function postMailboxMessage(cwd, message, env = process.env) {
  const payload = normalizeMessage(cwd, message);
  const dir = threadDir(cwd, payload.threadId, env);
  const finalPath = path.join(dir, `${payload.id}.json`);
  if (fs.existsSync(finalPath)) {
    throw new Error(`Mailbox message already exists: ${payload.id}`);
  }
  atomicWriteJson(finalPath, payload);
  return { status: "posted", message: payload };
}

export function listMailboxThreads(cwd = process.cwd(), env = process.env) {
  const dir = threadsDir(cwd, env);
  const threads = fs.readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && ID_PATTERN.test(entry.name))
    .map((entry) => {
      const messageCount = fs.readdirSync(path.join(dir, entry.name)).filter((name) => name.endsWith(".json")).length;
      return { threadId: entry.name, messageCount };
    })
    .sort((left, right) => left.threadId.localeCompare(right.threadId));
  return { mailboxDir: mailboxDirForCwd(cwd, env), threads };
}

export function showMailboxThread(cwd, threadId, env = process.env) {
  validateId(threadId, "thread id");
  const dir = path.join(threadsDir(cwd, env), threadId);
  if (!fs.existsSync(dir)) {
    return { threadId, messages: [], corrupt: [] };
  }
  const corrupt = [];
  const messages = fs.readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => {
      try {
        return JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
      } catch (error) {
        corrupt.push({ file: name, error: error.message || String(error) });
        return null;
      }
    })
    .filter(Boolean)
    .sort((left, right) => String(left.createdAt).localeCompare(String(right.createdAt)));
  return { threadId, messages, corrupt };
}
