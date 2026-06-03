#!/usr/bin/env node

import fs from "node:fs";
import process from "node:process";
import { cancelJob, listJobs } from "../scripts/lib/jobs.mjs";
import { atomicWriteJson, currentSessionFileForCwd } from "../scripts/lib/state.mjs";

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

function handleSessionStart(input) {
  const cwd = cwdFrom(input);
  atomicWriteJson(currentSessionFileForCwd(cwd), {
    sessionId: sessionIdFrom(input),
    cwd,
    hookPayloadKeys: Object.keys(input).filter((key) => !/token|secret|key|auth/i.test(key)).sort(),
    startedAt: new Date().toISOString()
  });
}

function handleSessionEnd(input) {
  const cwd = cwdFrom(input);
  const sessionId = sessionIdFrom(input);
  if (!sessionId) {
    process.stderr.write("[gemini-for-codex lifecycle] SessionEnd missing session id; leaving jobs untouched.\n");
    return;
  }
  const jobs = listJobs(cwd).jobs.filter((job) => job.status === "queued" || job.status === "running");
  for (const job of jobs) {
    if (job.sessionId === sessionId) {
      cancelJob(cwd, job.id);
    }
  }
}

try {
  const input = readHookInput();
  const eventName = process.argv[2] || input.hook_event_name || input.hookEventName || "";
  if (eventName === "SessionStart") {
    handleSessionStart(input);
  } else if (eventName === "SessionEnd") {
    handleSessionEnd(input);
  }
} catch (error) {
  process.stderr.write(`[gemini-for-codex lifecycle] ${error.message || String(error)}\n`);
}
