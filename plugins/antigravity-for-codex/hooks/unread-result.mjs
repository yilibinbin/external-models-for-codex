#!/usr/bin/env node

import process from "node:process";
import {
  listJobs,
  TERMINAL_JOB_STATUSES
} from "../scripts/lib/jobs.mjs";

try {
  const unread = listJobs(process.cwd(), process.env)
    .filter((job) => TERMINAL_JOB_STATUSES.has(job.status) && !job.viewed)
    .slice(0, 5);
  if (unread.length) {
    const ids = unread.map((job) => `${job.id}:${job.status}`).join(", ");
    process.stderr.write(`[antigravity-for-codex] unread background result(s): ${ids}. Run antigravity-companion.mjs result <job-id>.\n`);
  }
} catch (error) {
  process.stderr.write(`[antigravity-for-codex unread-result] ${error.message || String(error)}\n`);
}
