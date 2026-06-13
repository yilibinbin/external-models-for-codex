import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  DEFAULT_CLAUDE_MODEL,
  DEFAULT_GEMINI_MODEL,
  normalizeAgyProvider,
  parseAgyHelp,
  parseAgyModels,
  selectAgyModel,
  validateAgyModelForProvider
} from "./agy-capabilities.mjs";
import { classifyAgyOutcome, outcomeStderr } from "./agy-outcome.mjs";
import { spawnSyncWithRetry } from "./spawn-retry.mjs";

export const AGY_CLI_PATH_ENV = "AGY_CLI_PATH";
export const ANTIGRAVITY_CLI_PATH_ENV = "ANTIGRAVITY_CLI_PATH";
export const MODEL_PROVIDER_ENV = "ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER";
export const MODEL_ENV = "ANTIGRAVITY_FOR_CODEX_MODEL";
export const GEMINI_MODEL_ENV = "ANTIGRAVITY_FOR_CODEX_GEMINI_MODEL";
export const CLAUDE_MODEL_ENV = "ANTIGRAVITY_FOR_CODEX_CLAUDE_MODEL";

export { DEFAULT_CLAUDE_MODEL, DEFAULT_GEMINI_MODEL };
export const DEFAULT_TIMEOUT_MS = 15 * 60 * 1000;

const MAX_BUFFER = 20 * 1024 * 1024;
const LOG_DIAGNOSTIC_BYTES = 128 * 1024;
const WINDOWS_TREE_CLEANUP_TIMEOUT_MS = 5000;
const POSIX_SYNC_SUPERVISOR_GRACE_MS = 5000;
const FORCE_WINDOWS_TREE_CLEANUP_ENV = "ANTIGRAVITY_FOR_CODEX_TEST_FORCE_WINDOWS_TREE_CLEANUP";
const TRANSIENT_SPAWN_ERROR_CODES = new Set(["EAGAIN", "EMFILE", "ENFILE", "ENOBUFS"]);
const POSIX_SYNC_SUPERVISOR_ATTEMPTS = 4;

function spawnSync(command, args, options) {
  return spawnSyncWithRetry(command, args, options);
}

const POSIX_SYNC_SUPERVISOR = `
const { spawn } = require("node:child_process");
const fs = require("node:fs");

function write(payload) {
  process.stdout.write(JSON.stringify(payload));
}

function appendCapped(chunks, state, chunk, maxBuffer) {
  const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk));
  state.total += buffer.length;
  if (state.kept >= maxBuffer) return;
  const available = maxBuffer - state.kept;
  const next = buffer.length > available ? buffer.subarray(0, available) : buffer;
  chunks.push(next);
  state.kept += next.length;
}

let payload;
try {
  payload = JSON.parse(fs.readFileSync(0, "utf8") || "{}");
} catch (error) {
  write({ status: 1, stdout: "", stderr: "", error: error.message || String(error), errorCode: "EBADPAYLOAD" });
  process.exit(0);
}

const maxBuffer = Number.isFinite(payload.maxBuffer) && payload.maxBuffer > 0 ? payload.maxBuffer : 20 * 1024 * 1024;
const stdoutChunks = [];
const stderrChunks = [];
const stdoutState = { kept: 0, total: 0 };
const stderrState = { kept: 0, total: 0 };
let spawnError = null;
let timedOut = false;
let settled = false;
let timeoutTimer = null;
let killTimer = null;
let signalExitTimer = null;
let parentMonitor = null;

const child = spawn(String(payload.command || ""), Array.isArray(payload.args) ? payload.args.map(String) : [], {
  cwd: payload.cwd || process.cwd(),
  env: payload.env && typeof payload.env === "object" ? payload.env : process.env,
  detached: true,
  stdio: ["pipe", "pipe", "pipe"]
});

function killTree(signal) {
  if (child.pid) {
    try {
      process.kill(-child.pid, signal);
      return;
    } catch {
      // Fall through to direct child kill when process-group signaling is unavailable.
    }
  }
  try {
    child.kill(signal);
  } catch {
    // The close/error path resolves the result.
  }
}

function exitAfterSignal(signal) {
  timedOut = timedOut || signal === "SIGTERM";
  killTree("SIGTERM");
  setTimeout(() => {
    killTree("SIGKILL");
  }, 25);
  signalExitTimer = setTimeout(() => {
    process.exit(1);
  }, 1000);
}

for (const signal of ["SIGTERM", "SIGINT", "SIGHUP"]) {
  process.on(signal, () => exitAfterSignal(signal));
}

const parentPid = Number(payload.parentPid);
if (Number.isInteger(parentPid) && parentPid > 0) {
  parentMonitor = setInterval(() => {
    try {
      process.kill(parentPid, 0);
    } catch (error) {
      if (error?.code === "ESRCH") {
        exitAfterSignal("SIGTERM");
      }
    }
  }, 100);
  parentMonitor.unref?.();
}

function finish(result) {
  if (settled) return;
  settled = true;
  if (timeoutTimer) clearTimeout(timeoutTimer);
  if (killTimer) clearTimeout(killTimer);
  if (signalExitTimer) clearTimeout(signalExitTimer);
  if (parentMonitor) clearInterval(parentMonitor);
  write(result);
}

const timeout = Number.isFinite(payload.timeout) && payload.timeout > 0 ? payload.timeout : 15 * 60 * 1000;
timeoutTimer = setTimeout(() => {
  timedOut = true;
  killTree("SIGTERM");
  killTimer = setTimeout(() => {
    killTree("SIGKILL");
  }, 1000);
  killTimer.unref?.();
}, timeout);
timeoutTimer.unref?.();

child.stdout.on("data", (chunk) => appendCapped(stdoutChunks, stdoutState, chunk, maxBuffer));
child.stderr.on("data", (chunk) => appendCapped(stderrChunks, stderrState, chunk, maxBuffer));
child.on("error", (error) => {
  spawnError = error;
});
child.on("close", (code, signal) => {
  const stdout = Buffer.concat(stdoutChunks).toString("utf8");
  const stderr = Buffer.concat(stderrChunks).toString("utf8");
  if (timedOut || signal) {
    killTree("SIGKILL");
  }
  finish({
    status: code ?? (spawnError ? 1 : (signal ? 1 : 0)),
    stdout,
    stderr,
    error: spawnError ? String(spawnError.message || spawnError) : (timedOut ? "ETIMEDOUT" : ""),
    errorCode: spawnError?.code ? String(spawnError.code) : (timedOut ? "ETIMEDOUT" : ""),
    signal: signal || "",
    timedOut,
    stdoutBytes: stdoutState.total,
    stderrBytes: stderrState.total
  });
});

try {
  if (payload.inputBase64) {
    child.stdin.end(Buffer.from(String(payload.inputBase64), "base64"));
  } else {
    child.stdin.end();
  }
} catch {
  // Spawn/close errors carry the failure.
}
`;

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

function shouldUseWindowsTreeCleanup(env = process.env) {
  return process.platform === "win32" || env[FORCE_WINDOWS_TREE_CLEANUP_ENV] === "1";
}

export function cleanupWindowsDescendantsByParentPid(parentPid, env = process.env) {
  const numericPid = Number(parentPid);
  if (!Number.isInteger(numericPid) || numericPid <= 0 || !shouldUseWindowsTreeCleanup(env)) {
    return { ok: true, skipped: true };
  }
  const script = [
    "$ErrorActionPreference = 'SilentlyContinue'",
    "$seen = @{}",
    "function Stop-Children([int]$parent) {",
    "  Get-CimInstance Win32_Process -Filter \"ParentProcessId = $parent\" | ForEach-Object {",
    "    $childPid = [int]$_.ProcessId",
    "    if (-not $seen.ContainsKey($childPid)) {",
    "      $seen[$childPid] = $true",
    "      Stop-Children $childPid",
    "      Stop-Process -Id $childPid -Force -ErrorAction SilentlyContinue",
    "    }",
    "  }",
    "}",
    `Stop-Children ${numericPid}`
  ].join("; ");
  const result = spawnSync("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
    encoding: "utf8",
    env,
    timeout: WINDOWS_TREE_CLEANUP_TIMEOUT_MS,
    killSignal: "SIGKILL",
    maxBuffer: MAX_BUFFER
  });
  return {
    ok: !result.error && !result.signal && result.status === 0,
    status: result.status ?? 1,
    error: result.error ? String(result.error.message || result.error) : "",
    stderr: String(result.stderr || ""),
    stdout: String(result.stdout || "")
  };
}

function cleanupWindowsProcessTree(parentPid, env = process.env) {
  const numericPid = Number(parentPid);
  if (!Number.isInteger(numericPid) || numericPid <= 0 || !shouldUseWindowsTreeCleanup(env)) {
    return;
  }
  try {
    spawnSync("taskkill.exe", ["/PID", String(numericPid), "/T", "/F"], {
      encoding: "utf8",
      env,
      timeout: WINDOWS_TREE_CLEANUP_TIMEOUT_MS,
      killSignal: "SIGKILL",
      maxBuffer: MAX_BUFFER
    });
  } catch {
    // Parent-PID descendant cleanup below does not require taskkill to succeed.
  }
  cleanupWindowsDescendantsByParentPid(numericPid, env);
}

function inputBase64(input) {
  if (input === undefined || input === null) return "";
  return Buffer.isBuffer(input)
    ? input.toString("base64")
    : Buffer.from(String(input), "utf8").toString("base64");
}

function sleepMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function isTransientRunCommandFailure(result) {
  return TRANSIENT_SPAWN_ERROR_CODES.has(String(result?.errorCode || ""));
}

function runCommandWithPosixSupervisorOnce(command, args, options = {}, env = process.env) {
  const timeout = options.timeout || DEFAULT_TIMEOUT_MS;
  const result = spawnSync(process.execPath, ["-e", POSIX_SYNC_SUPERVISOR], {
    cwd: options.cwd || process.cwd(),
    env,
    encoding: "utf8",
    input: JSON.stringify({
      command,
      args,
      cwd: options.cwd || process.cwd(),
      env,
      parentPid: process.pid,
      inputBase64: inputBase64(options.input),
      maxBuffer: MAX_BUFFER,
      timeout
    }),
    maxBuffer: MAX_BUFFER,
    timeout: timeout + POSIX_SYNC_SUPERVISOR_GRACE_MS,
    killSignal: "SIGKILL",
    detached: true
  });
  if (result.status === 0 && result.stdout) {
    try {
      const payload = JSON.parse(result.stdout);
      return {
        status: payload.status ?? 1,
        stdout: payload.stdout ?? "",
        stderr: payload.stderr ?? "",
        error: payload.error ?? "",
        errorCode: payload.errorCode ?? "",
        signal: payload.signal ?? "",
        timedOut: Boolean(payload.timedOut)
      };
    } catch {
      // Fall through to the wrapper-failure result.
    }
  }
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message || result.error) : "",
    errorCode: result.error?.code ? String(result.error.code) : "",
    signal: result.signal || "",
    timedOut: result.error?.code === "ETIMEDOUT"
  };
}

function runCommandWithPosixSupervisor(command, args, options = {}, env = process.env) {
  let result = null;
  for (let attempt = 1; attempt <= POSIX_SYNC_SUPERVISOR_ATTEMPTS; attempt += 1) {
    result = runCommandWithPosixSupervisorOnce(command, args, options, env);
    if (!isTransientRunCommandFailure(result) || attempt >= POSIX_SYNC_SUPERVISOR_ATTEMPTS) {
      return result;
    }
    sleepMs(50 * (2 ** (attempt - 1)));
  }
  return result;
}

function expandExecutableCandidates(pattern) {
  const parts = path.resolve(pattern).split(path.sep);
  const results = [];

  function visit(index, current) {
    if (index >= parts.length) {
      results.push(current || path.sep);
      return;
    }
    const part = parts[index];
    if (!part) {
      visit(index + 1, path.sep);
      return;
    }
    if (part !== "*") {
      visit(index + 1, path.join(current || path.sep, part));
      return;
    }
    const dir = current || path.sep;
    let entries = [];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (entry.isDirectory()) {
        visit(index + 1, path.join(dir, entry.name));
      }
    }
  }

  visit(0, "");
  return results;
}

function candidateCommands(env = process.env) {
  const names = process.platform === "win32" ? ["agy.cmd", "agy.exe", "agy"] : ["agy"];
  const home = env.HOME || os.homedir();
  const candidates = [];
  const add = (candidate) => {
    if (candidate) candidates.push(candidate);
  };
  const addBin = (dir) => {
    for (const name of names) {
      add(dir ? path.join(dir, name) : "");
    }
  };

  addBin(path.join(home, ".local", "bin"));
  addBin(path.join(home, "bin"));
  addBin(path.join(home, ".npm-global", "bin"));
  addBin(path.join(home, ".volta", "bin"));
  addBin(path.join(home, ".asdf", "shims"));
  addBin(path.join(home, ".bun", "bin"));
  addBin(path.join(home, ".deno", "bin"));
  addBin(env.PNPM_HOME);
  addBin(env.NPM_CONFIG_PREFIX ? path.join(env.NPM_CONFIG_PREFIX, "bin") : "");
  addBin(env.npm_config_prefix ? path.join(env.npm_config_prefix, "bin") : "");
  addBin(env.HOMEBREW_PREFIX ? path.join(env.HOMEBREW_PREFIX, "bin") : "");

  for (const pattern of [
    path.join(home, ".nvm", "versions", "node", "*", "bin", "agy"),
    path.join(home, ".fnm", "node-versions", "*", "installation", "bin", "agy"),
    path.join(home, ".asdf", "installs", "nodejs", "*", "bin", "agy")
  ]) {
    candidates.push(...expandExecutableCandidates(pattern));
  }

  for (const dir of ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]) {
    addBin(dir);
  }
  return [...new Set(candidates)];
}

export function agyCommand(env = process.env) {
  for (const envName of [AGY_CLI_PATH_ENV, ANTIGRAVITY_CLI_PATH_ENV]) {
    if (env[envName] && isExecutable(env[envName])) return env[envName];
  }
  const fromPath = findOnPath("agy", env);
  if (fromPath) return fromPath;
  // TEST-ONLY: lets fake-CLI tests prove missing-candidate behavior without
  // accidentally discovering a real user-home Antigravity install.
  if (env.ANTIGRAVITY_FOR_CODEX_TEST_DISABLE_CANDIDATE_DISCOVERY === "1") return "agy";
  for (const candidate of candidateCommands(env)) {
    if (isExecutable(candidate)) return candidate;
  }
  return "agy";
}

export function runCommand(command, args, options = {}) {
  const env = options.env || process.env;
  if (process.platform !== "win32" && !shouldUseWindowsTreeCleanup(env)) {
    return runCommandWithPosixSupervisor(command, args, options, env);
  }
  const result = spawnSync(command, args, {
    cwd: options.cwd || process.cwd(),
    env,
    encoding: "utf8",
    input: options.input,
    maxBuffer: MAX_BUFFER,
    timeout: options.timeout || DEFAULT_TIMEOUT_MS,
    killSignal: options.killSignal || "SIGKILL"
  });
  if (result.error?.code === "ETIMEDOUT" || result.signal) {
    cleanupWindowsProcessTree(result.pid, env);
  }
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message || result.error) : "",
    errorCode: result.error?.code ? String(result.error.code) : ""
  };
}

export function normalizedModelProvider(value = process.env[MODEL_PROVIDER_ENV]) {
  return normalizeAgyProvider(value || "gemini");
}

export function validateModelForProvider(model, provider) {
  // Model safety policy, including GPT/OpenAI rejection via /\b(gpt|openai)\b/i, lives in agy-capabilities.mjs.
  return validateAgyModelForProvider(model, provider);
}

export function selectedModel(env = process.env, options = {}) {
  const selected = selectAgyModel({
    provider: options.modelProvider || env[MODEL_PROVIDER_ENV],
    explicitModel: options.model,
    env,
    models: options.models || null
  });
  return { modelProvider: selected.modelProvider, model: selected.model };
}

function createAgyLogFile() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "antigravity-for-codex-"));
  return path.join(dir, "agy.log");
}

function cleanupAgyLogFile(logFile) {
  if (!logFile) return;
  try {
    fs.rmSync(path.dirname(logFile), { recursive: true, force: true });
  } catch {
    // Best-effort cleanup only; diagnostics must not mask the model result.
  }
}

function tailText(text, maxBytes = LOG_DIAGNOSTIC_BYTES) {
  const buffer = Buffer.from(String(text || ""), "utf8");
  if (buffer.length <= maxBytes) return buffer.toString("utf8");
  return buffer.subarray(buffer.length - maxBytes).toString("utf8");
}

function collapseRepeatedDiagnostic(text) {
  const diagnostic = String(text || "").trim();
  const markers = ["RESOURCE_EXHAUSTED", "UNAUTHENTICATED", "PERMISSION_DENIED"];
  for (const marker of markers) {
    const first = diagnostic.indexOf(marker);
    const second = first >= 0 ? diagnostic.indexOf(marker, first + marker.length) : -1;
    if (first < 0 || second < 0) continue;

    const chunks = [];
    let offset = first;
    while (offset >= 0) {
      const next = diagnostic.indexOf(marker, offset + marker.length);
      const chunk = diagnostic.slice(offset, next >= 0 ? next : undefined)
        .replace(/^[\s:;.-]+|[\s:;.-]+$/g, "");
      if (chunk && !chunks.includes(chunk)) {
        chunks.push(chunk);
      }
      offset = next;
    }
    if (chunks.length === 1) return chunks[0];
    if (chunks.length > 1) return chunks.join(" | ");
  }
  return diagnostic;
}

function escapedRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function redactFileSystemPaths(text) {
  let redacted = String(text || "");
  const home = process.env.HOME || "";
  if (home) {
    const homePattern = escapedRegExp(home).replace(/\\\//g, "[/\\\\]+");
    redacted = redacted.replace(new RegExp(homePattern, "g"), "[redacted-path]");
  }
  return redacted
    .replace(/\\\\[A-Za-z0-9._$ -]+(?:\\{1,2}[^\r\n"'`<>|]+)+/g, "[redacted-path]")
    .replace(/(^|[\s"'(=])[A-Za-z]:(?:\\{1,2}|\/)[^\r\n"'`<>|]+/g, "$1[redacted-path]")
    .replace(/(^|[\s"'(=])~(?:\/|\\)[^\r\n"'`<>|]*/g, "$1[redacted-path]")
    .replace(/(^|[\s"'(=])\/(?:Users|home|private\/var\/folders|root|Volumes|workspace|workspaces|mnt)[^\r\n"'`<>|]*/g, "$1[redacted-path]");
}

function sanitizeDiagnostic(text) {
  const redacted = redactFileSystemPaths(String(text || "")
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[redacted-email]"))
    .replace(/\s+/g, " ")
    .trim();
  return collapseRepeatedDiagnostic(redacted);
}

export function antigravityLogDiagnostic(logText) {
  const text = tailText(logText);
  if (!text) return "";
  const resourceMatches = [...text.matchAll(/RESOURCE_EXHAUSTED[^\r\n]*/g)].map((match) => match[0]);
  if (resourceMatches.length) {
    return sanitizeDiagnostic(resourceMatches[resourceMatches.length - 1]);
  }
  const executorMatches = [...text.matchAll(/agent executor error:\s*([^\r\n]+)/g)].map((match) => match[1]);
  if (executorMatches.length) {
    return sanitizeDiagnostic(executorMatches[executorMatches.length - 1]);
  }
  if (/You are not logged into Antigravity/i.test(text) && !/authenticated successfully/i.test(text)) {
    return "Antigravity authentication failed: You are not logged into Antigravity.";
  }
  const deniedMatches = [...text.matchAll(/(?:UNAUTHENTICATED|PERMISSION_DENIED|permission denied)[^\r\n]*/gi)].map((match) => match[0]);
  if (deniedMatches.length) {
    return sanitizeDiagnostic(deniedMatches[deniedMatches.length - 1]);
  }
  return "";
}

function readAgyLogDiagnostic(logFile) {
  if (!logFile) return "";
  try {
    return antigravityLogDiagnostic(fs.readFileSync(logFile, "utf8"));
  } catch {
    return "";
  }
}

export function antigravityPreflight(env = process.env, options = {}) {
  const command = agyCommand(env);
  const commandOptions = { env };
  if (Number.isFinite(options.timeout) && options.timeout > 0) {
    commandOptions.timeout = options.timeout;
  }
  const result = runCommand(command, ["--help"], commandOptions);
  const help = result.stdout || result.stderr || "";
  const capabilities = parseAgyHelp(help);
  const catalog = options.models
    ? { available: true, status: 0, models: options.models, error: "" }
    : (capabilities.modelsCommand
      ? antigravityModelCatalog(env, { timeout: options.modelCatalogTimeout })
      : { available: false, status: 1, models: parseAgyModels(""), error: "agy models command not reported" });
  let selected = { modelProvider: "", model: "" };
  let modelError = "";
  try {
    selected = selectedModel(env, { ...options, models: catalog.models });
  } catch (error) {
    modelError = error.message || String(error);
  }
  const requiredFlags = ["--prompt", "--model", "--print-timeout"];
  const missing = requiredFlags.filter((flag) => !help.includes(flag));
  return {
    backend: "antigravity",
    command,
    checked: result.status === 0,
    ok: result.status === 0 && missing.length === 0 && !modelError,
    missing,
    requiredFlags,
    capabilities,
    modelProvider: selected.modelProvider,
    model: selected.model,
    modelCatalog: {
      available: catalog.available,
      status: catalog.status,
      models: catalog.models,
      count: catalog.models?.all?.length || 0,
      selectedModelListed: Boolean(catalog.models?.all?.includes(selected.model)),
      error: catalog.error
    },
    modelPolicyError: Boolean(modelError),
    error: modelError || (result.status === 0 ? "" : (result.stderr || result.error || "agy --help failed").trim())
  };
}

export function antigravityModelCatalog(env = process.env, options = {}) {
  const command = agyCommand(env);
  const result = runCommand(command, ["models"], { env, timeout: options.timeout || 10 * 1000 });
  const models = result.status === 0 ? parseAgyModels(result.stdout) : parseAgyModels("");
  return {
    available: result.status === 0,
    status: result.status,
    models,
    error: result.status === 0 ? "" : (result.stderr || result.error || "").trim()
  };
}

export function antigravityModelDiagnostics(env = process.env, options = {}) {
  const preflight = antigravityPreflight(env, options);
  const catalog = preflight.modelCatalog || { available: false, status: 1, models: parseAgyModels(""), error: "agy models command not reported" };
  return {
    ok: true,
    provider: {
      modelProvider: preflight.modelProvider || "",
      model: preflight.model || ""
    },
    modelCatalog: {
      available: catalog.available,
      selectedModelListed: Boolean(catalog.models?.all?.includes(preflight.model)),
      count: catalog.models?.all?.length || 0,
      error: catalog.error
    }
  };
}

function timeoutMsToAgyDuration(timeoutMs) {
  const seconds = Math.max(1, Math.ceil((timeoutMs || DEFAULT_TIMEOUT_MS) / 1000));
  return `${seconds}s`;
}

export function antigravityPrintArgs(prompt, options = {}, env = process.env) {
  const preflight = options.preflight?.ok ? options.preflight : antigravityPreflight(env, options);
  if (!preflight.ok) {
    throw new Error(preflight.error || `Antigravity CLI is unavailable; missing ${preflight.missing.join(", ")}.`);
  }
  const shouldCaptureLog = preflight.capabilities.logFile && env.ANTIGRAVITY_FOR_CODEX_DISABLE_LOG_CAPTURE !== "1";
  const args = [
    "--model",
    preflight.model,
    "--print-timeout",
    options.printTimeout || timeoutMsToAgyDuration(options.timeout)
  ];
  if (env.ANTIGRAVITY_FOR_CODEX_SANDBOX === "on" && preflight.capabilities.sandbox) {
    args.push("--sandbox");
  }
  if (options.includeDirectories?.length) {
    if (!preflight.capabilities.addDir) {
      throw new Error("Antigravity CLI does not report --add-dir support.");
    }
    for (const includeDirectory of options.includeDirectories) {
      args.push("--add-dir", includeDirectory);
    }
  }
  const logFile = shouldCaptureLog ? createAgyLogFile() : "";
  if (logFile) {
    args.push("--log-file", logFile);
  }
  // Current agy treats --prompt as the noninteractive print prompt; combining
  // --print with --prompt can ignore the supplied prompt and produce a greeting.
  args.push("--prompt", String(prompt));
  if (args.includes("--dangerously-skip-permissions")) {
    throw new Error("Internal error: unsafe Antigravity permission flag is forbidden.");
  }
  return { command: preflight.command, args, preflight, logFile };
}

export function antigravityPrint(prompt, options = {}, env = process.env) {
  let invocation;
  try {
    invocation = antigravityPrintArgs(prompt, options, env);
  } catch (error) {
    const normalized = { status: 2, stdout: "", stderr: error.message || String(error), error: "", errorCode: "" };
    return { ...normalized, outcome: classifyAgyOutcome(normalized) };
  }
  const result = runCommand(invocation.command, invocation.args, {
    cwd: options.cwd,
    env,
    timeout: options.timeout
  });
  const output = String(result.stdout || "").trim();
  if (result.status === 0 && !output) {
    const logDiagnostic = readAgyLogDiagnostic(invocation.logFile);
    const outcome = classifyAgyOutcome(result, { logDiagnostic });
    const stderr = outcomeStderr(result, { logDiagnostic });
    cleanupAgyLogFile(invocation.logFile);
    return {
      ...result,
      status: 1,
      stderr,
      errorCode: "EEMPTYOUTPUT",
      outcome,
      provider: invocation.preflight
    };
  }
  cleanupAgyLogFile(invocation.logFile);
  const normalized = { ...result, stdout: output, provider: invocation.preflight };
  return { ...normalized, outcome: classifyAgyOutcome(normalized) };
}

export function antigravityPrintAsync(prompt, options = {}, env = process.env) {
  let invocation;
  try {
    invocation = antigravityPrintArgs(prompt, options, env);
  } catch (error) {
    const normalized = {
      status: 2,
      stdout: "",
      stderr: error.message || String(error),
      error: "",
      errorCode: ""
    };
    return Promise.resolve({ ...normalized, outcome: classifyAgyOutcome(normalized) });
  }
  return new Promise((resolve) => {
    const detached = process.platform !== "win32";
    const child = spawn(invocation.command, invocation.args, {
      cwd: options.cwd || process.cwd(),
      env,
      detached,
      stdio: ["pipe", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;
    let timeoutTimer;
    let killTimer;
    let forceResolveTimer;

    const clearTimers = () => {
      clearTimeout(timeoutTimer);
      clearTimeout(killTimer);
      clearTimeout(forceResolveTimer);
    };

    const killChild = (signal) => {
      cleanupWindowsProcessTree(child.pid, env);
      if (detached && child.pid) {
        try {
          process.kill(-child.pid, signal);
          return;
        } catch {
          // Fall through to direct child kill for platforms/runtimes without process-group support.
        }
      }
      try {
        child.kill(signal);
      } catch {
        // The close/error handlers, or the forced timeout resolver, will finish the result.
      }
    };

    const settle = (payload) => {
      if (settled) return;
      settled = true;
      clearTimers();
      resolve(payload);
    };

    const timeoutMs = options.timeout || DEFAULT_TIMEOUT_MS;
    const killGraceMs = options.timeoutKillGraceMs ?? 1000;
    const forceResolveGraceMs = options.timeoutForceResolveGraceMs ?? 250;

    timeoutTimer = setTimeout(() => {
      if (settled) return;
      timedOut = true;
      killChild("SIGTERM");
      killTimer = setTimeout(() => {
        if (settled) return;
        killChild("SIGKILL");
        forceResolveTimer = setTimeout(() => {
          const output = String(stdout || "").trim();
          const normalized = {
            status: 1,
            stdout: output,
            stderr: `${stderr}${stderr ? "\n" : ""}${readAgyLogDiagnostic(invocation.logFile) || "terminated by SIGKILL after timeout"}`,
            error: "ETIMEDOUT",
            errorCode: "ETIMEDOUT",
            timedOut: true,
            provider: invocation.preflight
          };
          settle({ ...normalized, outcome: classifyAgyOutcome(normalized) });
          cleanupAgyLogFile(invocation.logFile);
        }, forceResolveGraceMs);
      }, killGraceMs);
    }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.stdin.end();
    child.on("error", (error) => {
      const normalized = {
        status: 1,
        stdout,
        stderr,
        error: timedOut ? "ETIMEDOUT" : String(error.message || error),
        errorCode: timedOut ? "ETIMEDOUT" : String(error.code || ""),
        timedOut,
        provider: invocation.preflight
      };
      settle({ ...normalized, outcome: classifyAgyOutcome(normalized) });
      cleanupAgyLogFile(invocation.logFile);
    });
    child.on("close", (status, signal) => {
      const output = String(stdout || "").trim();
      const emptyOutput = !timedOut && status === 0 && !output;
      const logDiagnostic = (timedOut || emptyOutput) ? readAgyLogDiagnostic(invocation.logFile) : "";
      const timedOutText = signal
        ? `${stderr}${stderr ? "\n" : ""}terminated by ${signal} after timeout`
        : `${stderr}${stderr ? "\n" : ""}Antigravity timeout.`;
      const timedOutStderr = outcomeStderr({ status: 1, stdout: output, stderr: timedOutText, errorCode: "ETIMEDOUT" }, { logDiagnostic });
      const normalized = {
        status: timedOut || emptyOutput ? 1 : status ?? 1,
        stdout: output,
        stderr: timedOut
          ? timedOutStderr
          : (emptyOutput ? outcomeStderr({ status, stdout: output, stderr, errorCode: "" }, { logDiagnostic }) : (signal ? `${stderr}${stderr ? "\n" : ""}terminated by ${signal}` : stderr)),
        error: timedOut ? "ETIMEDOUT" : "",
        errorCode: timedOut ? "ETIMEDOUT" : (emptyOutput ? "EEMPTYOUTPUT" : ""),
        timedOut,
        provider: invocation.preflight
      };
      const outcome = timedOut
        ? classifyAgyOutcome(normalized, { logDiagnostic })
        : (emptyOutput
          ? classifyAgyOutcome({ status, stdout: output, stderr, errorCode: "" }, { logDiagnostic })
          : classifyAgyOutcome(normalized));
      settle({ ...normalized, outcome });
      cleanupAgyLogFile(invocation.logFile);
    });
  });
}
