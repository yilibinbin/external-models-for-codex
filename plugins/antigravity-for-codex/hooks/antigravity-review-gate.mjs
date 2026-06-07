#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

if (String(process.env.ANTIGRAVITY_FOR_CODEX_REVIEW_GATE ?? "").toLowerCase() === "off") {
  process.exit(0);
}

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const RUNTIME = path.resolve(SCRIPT_DIR, "..", "scripts", "antigravity-companion.mjs");
const WRAPPER_TIMEOUT_MS = 870 * 1000;

try {
  let input = "";
  if (!process.stdin.isTTY) {
    try {
      input = fs.readFileSync(0, "utf8");
    } catch (error) {
      process.stderr.write(`[antigravity-for-codex review-gate] failed to read stdin; allowing stop: ${error.message}\n`);
    }
  }

  const result = spawnSync(process.execPath, [RUNTIME, "review-gate"], {
    cwd: process.cwd(),
    env: process.env,
    input,
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
    timeout: WRAPPER_TIMEOUT_MS
  });

  if (result.stdout) {
    process.stdout.write(result.stdout);
  }
  if (result.stderr) {
    process.stderr.write(result.stderr);
  }
  if (result.error) {
    process.stderr.write(`[antigravity-for-codex review-gate] wrapper failed; allowing stop: ${result.error.message}\n`);
  }
} catch (error) {
  process.stderr.write(`[antigravity-for-codex review-gate] wrapper error; allowing stop: ${error.message || String(error)}\n`);
}

process.exitCode = 0;
