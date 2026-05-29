#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import process from "node:process";
import { fileURLToPath } from "node:url";

const COMMANDS = new Map([
  ["status", ["status", "--short", "--untracked-files=all"]],
  ["diff", ["diff"]],
  ["diff-cached", ["diff", "--cached"]],
  ["ls-files", ["ls-files"]]
]);

function runGit(args) {
  const result = spawnSync("git", args, {
    cwd: process.cwd(),
    encoding: "utf8",
    maxBuffer: 5 * 1024 * 1024
  });
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message ?? result.error) : ""
  };
}

export function runReadOnlyGitCommand(name) {
  if (!COMMANDS.has(name)) {
    return {
      status: 2,
      stdout: "",
      stderr: `Unsupported read-only git command "${name}".`,
      error: ""
    };
  }
  return runGit(COMMANDS.get(name));
}

if (fileURLToPath(import.meta.url) === process.argv[1]) {
  const command = process.argv[2] || "status";
  const result = runReadOnlyGitCommand(command);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  process.exit(result.status === 0 ? 0 : 1);
}
