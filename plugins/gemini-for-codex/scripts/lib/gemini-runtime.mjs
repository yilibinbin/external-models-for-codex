import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const GEMINI_CLI_PATH_ENV = "GEMINI_CLI_PATH";

function isExecutable(filePath) {
  try {
    fs.accessSync(filePath, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function findOnPath(commandName, env = process.env) {
  for (const entry of String(env.PATH || "").split(path.delimiter)) {
    if (!entry) continue;
    const candidate = path.join(entry, commandName);
    if (isExecutable(candidate)) return candidate;
  }
  return "";
}

export function geminiCommand(env = process.env) {
  if (env[GEMINI_CLI_PATH_ENV] && isExecutable(env[GEMINI_CLI_PATH_ENV])) {
    return env[GEMINI_CLI_PATH_ENV];
  }
  const pathCommand = findOnPath("gemini", env);
  if (pathCommand) return pathCommand;
  const homeFallback = path.join(os.homedir(), ".local", "bin", "gemini");
  if (isExecutable(homeFallback)) return homeFallback;
  return "gemini";
}

export function runGemini(args, options = {}) {
  const result = spawnSync(geminiCommand(options.env || process.env), args, {
    cwd: options.cwd || process.cwd(),
    env: options.env || process.env,
    encoding: "utf8",
    input: options.input,
    maxBuffer: 20 * 1024 * 1024,
    timeout: options.timeout || 15 * 60 * 1000
  });
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message || result.error) : "",
    errorCode: result.error?.code ? String(result.error.code) : ""
  };
}

export function parseGeminiJson(stdout) {
  let parsed;
  try {
    parsed = JSON.parse(stdout || "{}");
  } catch (error) {
    return { ok: false, response: "", error: `Invalid Gemini JSON output: ${error.message}`, stats: {} };
  }
  if (parsed.error) {
    return { ok: false, response: "", error: JSON.stringify(parsed.error), stats: parsed.stats || {} };
  }
  if (typeof parsed.response !== "string") {
    return { ok: false, response: "", error: "Gemini JSON output did not include a string response.", stats: parsed.stats || {} };
  }
  return { ok: true, response: parsed.response, error: "", stats: parsed.stats || {} };
}
