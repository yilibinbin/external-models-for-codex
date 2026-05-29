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

function handleSessionStart(input) {
  const cwd = input.cwd || process.cwd();
  atomicWriteJson(currentSessionFileForCwd(cwd), {
    sessionId: input.session_id || "",
    cwd,
    startedAt: new Date().toISOString()
  });
}

function handleSessionEnd(input) {
  const cwd = input.cwd || process.cwd();
  const sessionId = input.session_id || "";
  const jobs = listJobs(cwd).jobs.filter((job) => job.status === "queued" || job.status === "running");
  for (const job of jobs) {
    if (sessionId && job.sessionId !== sessionId) {
      continue;
    }
    if (!sessionId && job.sessionId) {
      continue;
    }
    cancelJob(cwd, job.id);
  }
}

try {
  const input = readHookInput();
  const eventName = process.argv[2] || input.hook_event_name || "";
  if (eventName === "SessionStart") {
    handleSessionStart(input);
  } else if (eventName === "SessionEnd") {
    handleSessionEnd(input);
  }
} catch (error) {
  process.stderr.write(`[claude-for-codex lifecycle] ${error.message || String(error)}\n`);
}
