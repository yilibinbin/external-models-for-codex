import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { mailboxDirForCwd } from "./state.mjs";
import { sanitizeSummary } from "./sanitize.mjs";
import { canonicalWorkspaceRoot } from "./workspace.mjs";

const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const ROLE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const COMMANDS = new Set(["multi-review", "review", "adversarial-review", "plan", "rescue", "manual", "job"]);
const MODES = new Set(["plugin-managed", "native-agents", "manual", "job"]);
const STATUSES = new Set(["started", "succeeded", "failed", "cancelled", "note"]);
const SOURCES = new Set(["runtime", "hook", "manual"]);

export function assertIdentifier(value, label = "identifier") {
  if (!ID_PATTERN.test(String(value ?? ""))) {
    throw new Error(`Invalid ${label} "${value}".`);
  }
  return String(value);
}

function safeRole(value) {
  const role = String(value || "note");
  if (!ROLE_PATTERN.test(role)) {
    throw new Error(`Invalid role "${value}".`);
  }
  return role;
}

function workspaceId(cwd) {
  return createHash("sha256").update(canonicalWorkspaceRoot(cwd)).digest("hex").slice(0, 16);
}

function threadDir(cwd, threadId, env = process.env) {
  const id = assertIdentifier(threadId, "thread id");
  return path.join(mailboxDirForCwd(cwd, env), "threads", id);
}

function writeJsonNoClobber(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
  const tmp = `${filePath}.${process.pid}.${Date.now().toString(36)}.${Math.random().toString(16).slice(2)}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  try {
    fs.linkSync(tmp, filePath);
  } catch (error) {
    if (error.code === "EEXIST") {
      throw new Error(`Mailbox message already exists: ${path.basename(filePath)}`);
    }
    let fd;
    try {
      fd = fs.openSync(filePath, "wx", 0o600);
      fs.writeFileSync(fd, fs.readFileSync(tmp));
    } catch (fallbackError) {
      if (fallbackError.code === "EEXIST") {
        throw new Error(`Mailbox message already exists: ${path.basename(filePath)}`);
      }
      throw fallbackError;
    } finally {
      if (fd !== undefined) {
        fs.closeSync(fd);
      }
    }
  } finally {
    fs.rmSync(tmp, { force: true });
  }
}

export function postMailboxMessage(cwd, message, env = process.env) {
  const threadId = assertIdentifier(message.threadId || message.jobId || `thread-${Date.now().toString(36)}`, "thread id");
  const id = assertIdentifier(message.id || `msg-${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`, "message id");
  const jobId = message.jobId ? assertIdentifier(message.jobId, "job id") : "";
  const command = COMMANDS.has(message.command) ? message.command : "manual";
  const mode = MODES.has(message.mode) ? message.mode : "manual";
  const status = STATUSES.has(message.status) ? message.status : "note";
  const source = SOURCES.has(message.source) ? message.source : "manual";
  const payload = {
    version: 1,
    id,
    threadId,
    jobId,
    role: safeRole(message.role || "note"),
    mode,
    command,
    status,
    summary: sanitizeSummary(message.summary || "", { cwd, env, maxBytes: 2048 }),
    severityCounts: message.severityCounts && typeof message.severityCounts === "object"
      ? {
          high: Number(message.severityCounts.high || 0),
          medium: Number(message.severityCounts.medium || 0),
          low: Number(message.severityCounts.low || 0)
        }
      : { high: 0, medium: 0, low: 0 },
    createdAt: message.createdAt || new Date().toISOString(),
    workspaceId: workspaceId(cwd),
    source
  };
  writeJsonNoClobber(path.join(threadDir(cwd, threadId, env), `${id}.json`), payload);
  return payload;
}

export function showMailboxThread(cwd, threadOrJobId, env = process.env) {
  const id = assertIdentifier(threadOrJobId, "thread id");
  const dir = threadDir(cwd, id, env);
  const messages = [];
  const corrupt = [];
  if (fs.existsSync(dir)) {
    for (const name of fs.readdirSync(dir).filter((item) => item.endsWith(".json"))) {
      try {
        messages.push(JSON.parse(fs.readFileSync(path.join(dir, name), "utf8")));
      } catch (error) {
        corrupt.push({ id: name.slice(0, -5), error: error.message || String(error) });
      }
    }
  }
  messages.sort((left, right) => String(left.createdAt).localeCompare(String(right.createdAt)));
  return { threadId: id, messages, corrupt };
}

export function listMailboxThreads(cwd, env = process.env) {
  const root = path.join(mailboxDirForCwd(cwd, env), "threads");
  fs.mkdirSync(root, { recursive: true, mode: 0o700 });
  const threads = fs.readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && ID_PATTERN.test(entry.name))
    .map((entry) => {
      const dir = path.join(root, entry.name);
      const files = fs.readdirSync(dir)
        .filter((name) => name.endsWith(".json"));
      let lastMessageAt = "";
      for (const name of files) {
        try {
          const mtime = fs.statSync(path.join(dir, name)).mtime.toISOString();
          if (mtime > lastMessageAt) {
            lastMessageAt = mtime;
          }
        } catch {
          // Ignore entries that disappear during listing.
        }
      }
      return {
        threadId: entry.name,
        messageCount: files.length,
        lastMessageAt
      };
    })
    .sort((left, right) => String(right.lastMessageAt).localeCompare(String(left.lastMessageAt)));
  return { threads };
}

export function mailboxSummary(cwd, threadId, env = process.env, options = {}) {
  if (!threadId) {
    return { enabled: false, messageCount: 0, writeFailures: 0 };
  }
  const shown = showMailboxThread(cwd, threadId, env);
  return {
    enabled: true,
    threadIdHash: `sha256:${createHash("sha256").update(threadId).digest("hex")}`,
    messageCount: shown.messages.length,
    writeFailures: Number(options.writeFailures || 0)
  };
}
