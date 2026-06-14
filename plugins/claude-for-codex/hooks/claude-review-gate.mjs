#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { CAPACITY_BLOCKED_STATUS } from "../scripts/lib/resource-governor.mjs";

if (String(process.env.CLAUDE_FOR_CODEX_REVIEW_GATE ?? "").toLowerCase() === "off") {
  process.exit(0);
}

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const RUNTIME = path.resolve(SCRIPT_DIR, "..", "scripts", "claude-companion.mjs");

const input = process.stdin.isTTY ? "" : fs.readFileSync(0, "utf8");
const result = spawnSync(process.execPath, [RUNTIME, "review-gate"], {
  cwd: process.cwd(),
  env: process.env,
  input,
  encoding: "utf8",
  maxBuffer: 20 * 1024 * 1024,
  timeout: 15 * 60 * 1000
});

if (result.stdout) {
  process.stdout.write(result.stdout);
}
if (result.stderr) {
  process.stderr.write(result.stderr);
}
if (result.error) {
  process.stderr.write(`[claude-for-codex review-gate] wrapper failed; allowing stop: ${result.error.message}\n`);
}
if (result.status === 75 || String(result.stderr || result.stdout || "").includes(CAPACITY_BLOCKED_STATUS)) {
  process.stderr.write(`[claude-for-codex review-gate] ${CAPACITY_BLOCKED_STATUS}; allowing stop\n`);
}

process.exitCode = 0;
