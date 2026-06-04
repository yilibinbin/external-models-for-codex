import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { canonicalWorkspaceRoot, workspaceSlug } from "./workspace.mjs";

const STATE_VERSION = 1;
const PLUGIN_DATA_ENV = "CLAUDE_PLUGIN_DATA";

export class StateReadError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "StateReadError";
    this.stateFile = options.stateFile;
    this.cause = options.cause;
  }
}

export function stateRoot(env = process.env) {
  return env[PLUGIN_DATA_ENV]
    ? path.join(env[PLUGIN_DATA_ENV], "state")
    : path.join(os.homedir(), ".codex", "claude-for-codex", "state");
}

export function stateDirForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateRoot(env), workspaceSlug(cwd));
}

export function stateFileForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateDirForCwd(cwd, env), "state.json");
}

export function jobsDirForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateDirForCwd(cwd, env), "jobs");
}

export function mailboxDirForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateDirForCwd(cwd, env), "mailbox");
}

export function leasesDirForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateDirForCwd(cwd, env), "leases");
}

export function currentSessionFileForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateDirForCwd(cwd, env), "current-session.json");
}

export function turnBaselineFileForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateDirForCwd(cwd, env), "turn-baseline.json");
}

export function atomicWriteJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
  const tmpFile = `${filePath}.${process.pid}.${Date.now().toString(36)}.tmp`;
  fs.writeFileSync(tmpFile, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(tmpFile, filePath);
}

export function readJson(filePath, fallback = null) {
  if (!fs.existsSync(filePath)) {
    return fallback;
  }
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

export function defaultState() {
  return {
    version: STATE_VERSION,
    config: {
      reviewGateEnabled: false,
      reviewGateMode: "multi-role"
    }
  };
}

function normalizeState(parsed) {
  return {
    ...defaultState(),
    ...parsed,
    version: STATE_VERSION,
    config: {
      ...defaultState().config,
      ...(parsed?.config ?? {})
    }
  };
}

export function loadState(cwd = process.cwd(), env = process.env) {
  const stateFile = stateFileForCwd(cwd, env);
  if (!fs.existsSync(stateFile)) {
    return defaultState();
  }
  try {
    return normalizeState(JSON.parse(fs.readFileSync(stateFile, "utf8")));
  } catch (error) {
    throw new StateReadError(`State file is corrupt: ${stateFile}`, { stateFile, cause: error });
  }
}

export function readStateReport(cwd = process.cwd(), env = process.env) {
  const stateFile = stateFileForCwd(cwd, env);
  try {
    const state = loadState(cwd, env);
    return {
      state,
      readable: true,
      error: "",
      stateFile
    };
  } catch (error) {
    if (error instanceof StateReadError) {
      return {
        state: defaultState(),
        readable: false,
        error: error.message,
        stateFile
      };
    }
    throw error;
  }
}

export function saveState(cwd, state, env = process.env) {
  const stateDir = stateDirForCwd(cwd, env);
  fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  const payload = normalizeState(state);
  const stateFile = stateFileForCwd(cwd, env);
  const tmpFile = `${stateFile}.${process.pid}.tmp`;
  fs.writeFileSync(tmpFile, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(tmpFile, stateFile);
  return payload;
}

export function setConfig(cwd, key, value, env = process.env) {
  const state = loadState(cwd, env);
  state.config = {
    ...state.config,
    [key]: value
  };
  return saveState(cwd, state, env);
}

export function getConfig(cwd = process.cwd(), env = process.env) {
  return loadState(cwd, env).config;
}

export { canonicalWorkspaceRoot };
