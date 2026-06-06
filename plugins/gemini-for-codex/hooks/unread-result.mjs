#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import process from "node:process";
import { listJobs } from "../scripts/lib/jobs.mjs";
import { atomicWriteJson, turnBaselineFileForCwd } from "../scripts/lib/state.mjs";

function readHookInput() {
  if (process.stdin.isTTY) {
    return {};
  }
  const raw = fs.readFileSync(0, "utf8").trim();
  return raw ? JSON.parse(raw) : {};
}

function sessionIdFrom(input) {
  return input.session_id || input.sessionId || "";
}

function cwdFrom(input) {
  return input.cwd || process.cwd();
}

function git(cwd, args) {
  const result = spawnSync("git", args, {
    cwd,
    encoding: "utf8",
    maxBuffer: 10 * 1024 * 1024,
    timeout: 10000,
    killSignal: "SIGKILL"
  });
  return result.status === 0 ? result.stdout : "";
}

function workingTreeFingerprint(cwd) {
  const parts = [
    git(cwd, ["status", "--short", "--untracked-files=all"]),
    git(cwd, ["diff", "--cached"]),
    git(cwd, ["diff"])
  ];
  return createHash("sha256").update(parts.join("\n--- gemini-for-codex ---\n")).digest("hex");
}

function notifyUnreadResults(cwd, sessionId) {
  const unread = listJobs(cwd).jobs.filter((job) =>
    ["succeeded", "failed", "cancelled", "cancel_failed"].includes(job.status)
      && !job.resultViewedAt
      && (!sessionId || job.sessionId === sessionId)
  );
  if (!unread.length) {
    return;
  }
  const summary = unread.slice(0, 3).map((job) => `${job.id} (${job.status})`).join(", ");
  process.stderr.write(`[gemini-for-codex] Unread Gemini job result: ${summary}. Run gemini-result <job-id>.\n`);
}

try {
  const input = readHookInput();
  const cwd = cwdFrom(input);
  const sessionId = sessionIdFrom(input);
  atomicWriteJson(turnBaselineFileForCwd(cwd), {
    sessionId,
    cwd,
    hookPayloadKeys: Object.keys(input).filter((key) => !/token|secret|key|auth/i.test(key)).sort(),
    promptSubmittedAt: new Date().toISOString(),
    workingTreeFingerprint: workingTreeFingerprint(cwd)
  });
  notifyUnreadResults(cwd, sessionId);
} catch (error) {
  process.stderr.write(`[gemini-for-codex unread-result] ${error.message || String(error)}\n`);
}
