#!/usr/bin/env node

import fs from "node:fs";
import process from "node:process";
import { isTerminalJobStatus } from "../scripts/lib/job-lifecycle.mjs";
import { listJobs } from "../scripts/lib/jobs.mjs";
import { atomicWriteJson, turnBaselineFileForCwd } from "../scripts/lib/state.mjs";
import { workingTreeFingerprint } from "../scripts/lib/worktree-fingerprint.mjs";

function readHookInput() {
  if (process.stdin.isTTY) {
    return {};
  }
  const raw = fs.readFileSync(0, "utf8").trim();
  return raw ? JSON.parse(raw) : {};
}

function notifyUnreadResults(cwd, sessionId) {
  const unread = listJobs(cwd).jobs.filter((job) =>
    isTerminalJobStatus(job.status)
      && !job.resultViewedAt
      && (!sessionId || job.sessionId === sessionId)
  );
  if (unread.length === 0) {
    return;
  }
  const summary = unread.slice(0, 3).map((job) => `${job.id} (${job.status})`).join(", ");
  process.stderr.write(`[claude-for-codex] Unread Claude job result: ${summary}. Run claude-result <job-id>.\n`);
}

try {
  const input = readHookInput();
  const cwd = input.cwd || process.cwd();
  const sessionId = input.session_id || "";
  atomicWriteJson(turnBaselineFileForCwd(cwd), {
    sessionId,
    cwd,
    promptSubmittedAt: new Date().toISOString(),
    workingTreeFingerprint: workingTreeFingerprint(cwd)
  });
  notifyUnreadResults(cwd, sessionId);
} catch (error) {
  process.stderr.write(`[claude-for-codex unread-result] ${error.message || String(error)}\n`);
}
