import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSyncWithRetry } from "./spawn-retry.mjs";

export const GIT_TIMEOUT_MS = 10 * 1000;

function spawnSync(command, args, options) {
  return spawnSyncWithRetry(command, args, options);
}

function runGit(args, cwd) {
  const result = spawnSync("git", args, {
    cwd,
    encoding: "utf8",
    maxBuffer: 1024 * 1024,
    timeout: GIT_TIMEOUT_MS,
    killSignal: "SIGKILL"
  });
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? ""
  };
}

export function canonicalWorkspaceRoot(cwd = process.cwd()) {
  const rootResult = runGit(["rev-parse", "--show-toplevel"], cwd);
  const candidate = rootResult.status === 0 ? rootResult.stdout.trim() : cwd;
  const resolved = path.resolve(candidate || cwd);
  try {
    return fs.realpathSync.native(resolved);
  } catch {
    return resolved;
  }
}

export function workspaceSlug(cwd = process.cwd()) {
  const workspaceRoot = canonicalWorkspaceRoot(cwd);
  const slug = (path.basename(workspaceRoot) || "workspace")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "") || "workspace";
  const hash = createHash("sha256").update(workspaceRoot).digest("hex").slice(0, 16);
  return `${slug}-${hash}`;
}
