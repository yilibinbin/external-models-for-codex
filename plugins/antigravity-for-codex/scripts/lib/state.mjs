import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { gitSignalTimeoutMs } from "./job-lifecycle.mjs";

const STATE_HOME_ENV = "ANTIGRAVITY_FOR_CODEX_STATE_HOME";
const CODEX_PLUGIN_DATA_ENV = "CODEX_PLUGIN_DATA";
const GIT_REPOSITORY_ENV_KEYS = new Set([
  "GIT_ALTERNATE_OBJECT_DIRECTORIES",
  "GIT_CEILING_DIRECTORIES",
  "GIT_COMMON_DIR",
  "GIT_CONFIG",
  "GIT_CONFIG_COUNT",
  "GIT_CONFIG_GLOBAL",
  "GIT_CONFIG_NOSYSTEM",
  "GIT_CONFIG_PARAMETERS",
  "GIT_CONFIG_SYSTEM",
  "GIT_DIR",
  "GIT_DISCOVERY_ACROSS_FILESYSTEM",
  "GIT_INDEX_FILE",
  "GIT_NAMESPACE",
  "GIT_OBJECT_DIRECTORY",
  "GIT_WORK_TREE"
]);

function canonicalPath(value) {
  try {
    return fs.realpathSync(value);
  } catch {
    return path.resolve(value);
  }
}

function sanitizedGitEnv(sourceEnv = process.env) {
  const env = { ...sourceEnv };
  for (const key of Object.keys(env)) {
    if (GIT_REPOSITORY_ENV_KEYS.has(key) || /^GIT_CONFIG_(KEY|VALUE)_\d+$/.test(key)) {
      delete env[key];
    }
  }
  env.NO_COLOR = "1";
  return env;
}

function hardenedGitArgs(args) {
  return [
    "-c", "color.ui=false",
    "-c", "color.status=false",
    "-c", "color.diff=false",
    "-c", "core.fsmonitor=false",
    ...args
  ];
}

export function canonicalWorkspaceRoot(cwd = process.cwd(), env = process.env) {
  const result = spawnSync("git", hardenedGitArgs(["rev-parse", "--show-toplevel"]), {
    cwd,
    env: sanitizedGitEnv(env),
    encoding: "utf8",
    timeout: gitSignalTimeoutMs(env),
    killSignal: "SIGKILL",
    maxBuffer: 1024 * 1024
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

export function workspaceSlug(cwd = process.cwd(), env = process.env) {
  const root = canonicalWorkspaceRoot(cwd, env);
  const base = path.basename(root).replace(/[^a-zA-Z0-9._-]+/g, "-") || "workspace";
  const digest = createHash("sha256").update(root).digest("hex").slice(0, 16);
  return `${base}-${digest}`;
}

export function stateDirForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateRoot(env), workspaceSlug(cwd, env));
}
