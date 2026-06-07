import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const STATE_HOME_ENV = "ANTIGRAVITY_FOR_CODEX_STATE_HOME";
const CODEX_PLUGIN_DATA_ENV = "CODEX_PLUGIN_DATA";

function canonicalPath(value) {
  try {
    return fs.realpathSync(value);
  } catch {
    return path.resolve(value);
  }
}

export function canonicalWorkspaceRoot(cwd = process.cwd()) {
  const result = spawnSync("git", ["rev-parse", "--show-toplevel"], {
    cwd,
    encoding: "utf8",
    timeout: 30000
  });
  const root = result.status === 0 ? String(result.stdout || "").trim() : "";
  return canonicalPath(root || cwd);
}

export function stateRoot(env = process.env) {
  if (env[STATE_HOME_ENV]) {
    return env[STATE_HOME_ENV];
  }
  if (env[CODEX_PLUGIN_DATA_ENV]) {
    return path.join(env[CODEX_PLUGIN_DATA_ENV], "antigravity-for-codex");
  }
  return path.join(os.homedir(), ".codex", "antigravity-for-codex");
}

export function workspaceSlug(cwd = process.cwd()) {
  const root = canonicalWorkspaceRoot(cwd);
  const base = path.basename(root).replace(/[^a-zA-Z0-9._-]+/g, "-") || "workspace";
  const digest = createHash("sha256").update(root).digest("hex").slice(0, 16);
  return `${base}-${digest}`;
}

export function stateDirForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateRoot(env), workspaceSlug(cwd));
}
