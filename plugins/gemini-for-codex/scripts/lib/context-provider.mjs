import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { canonicalWorkspaceRoot } from "./workspace.mjs";

const DEFAULT_BUDGET = 32768;
const DEFAULT_TIMEOUT_MS = 5000;
const MAX_FOCUS_BYTES = 8192;
const MAX_FIELD_BYTES = 2048;
const STDERR_LIMIT = 16384;
const TRAMPOLINE_BASENAMES = new Set([
  "sh", "bash", "zsh", "fish", "python", "python3", "node", "ruby", "perl",
  "env", "npx", "pnpm", "npm", "yarn"
]);

export class ContextProviderError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "ContextProviderError";
    this.reason = options.reason || "unsafe-config";
  }
}

export function defaultContextOptions() {
  return {
    provider: "off",
    budget: DEFAULT_BUDGET,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    strict: false
  };
}

export function parseContextOptions(args) {
  const provider = args.contextProvider ?? "off";
  const budget = args.contextBudget === undefined ? DEFAULT_BUDGET : Number(args.contextBudget);
  const timeoutMs = args.contextTimeoutMs === undefined ? DEFAULT_TIMEOUT_MS : Number(args.contextTimeoutMs);
  if (!Number.isInteger(budget) || budget < 0 || budget > 1024 * 1024) {
    throw new ContextProviderError("Invalid --context-budget; expected integer bytes from 0 to 1048576.", { reason: "invalid-response" });
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 120000) {
    throw new ContextProviderError("Invalid --context-timeout-ms; expected integer milliseconds from 100 to 120000.", { reason: "invalid-response" });
  }
  if (!/^[a-zA-Z0-9_.-]{1,64}$/.test(provider)) {
    throw new ContextProviderError("Invalid --context-provider name.", { reason: "unknown-provider" });
  }
  return { provider, budget, timeoutMs, strict: Boolean(args.contextStrict) };
}

export function contextConfigPaths(env = process.env) {
  const paths = [];
  if (env.GEMINI_FOR_CODEX_CONTEXT_CONFIG) {
    paths.push(path.resolve(env.GEMINI_FOR_CODEX_CONTEXT_CONFIG));
  }
  if (env.GEMINI_FOR_CODEX_DATA) {
    paths.push(path.join(path.resolve(env.GEMINI_FOR_CODEX_DATA), "context", "providers.json"));
  }
  paths.push(path.join(os.homedir(), ".codex", "gemini-for-codex", "context", "providers.json"));
  return paths;
}

function firstExistingConfig(env = process.env) {
  return contextConfigPaths(env).find((candidate) => fs.existsSync(candidate)) || "";
}

function readConfig(env = process.env) {
  const configPath = firstExistingConfig(env);
  if (!configPath) {
    return { configPath: "", config: null };
  }
  const realConfigPath = fs.realpathSync.native(configPath);
  try {
    const stat = fs.statSync(realConfigPath);
    if ((stat.mode & 0o022) !== 0) {
      throw new ContextProviderError("Context provider config must not be group/world writable.", { reason: "unsafe-config" });
    }
  } catch (error) {
    if (error instanceof ContextProviderError) throw error;
  }
  return {
    configPath: realConfigPath,
    config: JSON.parse(fs.readFileSync(realConfigPath, "utf8"))
  };
}

function assertRepoExternal(filePath, workspaceRoot, what) {
  const realPath = fs.realpathSync.native(filePath);
  const relative = path.relative(workspaceRoot, realPath);
  if (!relative || (!relative.startsWith("..") && !path.isAbsolute(relative))) {
    throw new ContextProviderError(`${what} must resolve outside workspaceRoot.`, { reason: "unsafe-config" });
  }
  return realPath;
}

function assertExecutable(filePath) {
  try {
    fs.accessSync(filePath, fs.constants.X_OK);
  } catch {
    throw new ContextProviderError("Context provider executable is not executable.", { reason: "unsafe-config" });
  }
}

function validateProviderConfig(providerName, provider, configPath, workspaceRoot) {
  if (!provider || typeof provider !== "object" || Array.isArray(provider)) {
    throw new ContextProviderError(`Provider ${providerName} must be an object.`, { reason: "unsafe-config" });
  }
  if (!Array.isArray(provider.command) || provider.command.length === 0 || provider.command.some((item) => typeof item !== "string")) {
    throw new ContextProviderError("Provider command must be a non-empty argv string array.", { reason: "unsafe-config" });
  }
  const executable = provider.command[0];
  if (!path.isAbsolute(executable)) {
    throw new ContextProviderError("Provider executable must be an absolute path.", { reason: "unsafe-config" });
  }
  assertRepoExternal(configPath, workspaceRoot, "Context provider config");
  const realExecutable = assertRepoExternal(executable, workspaceRoot, "Context provider executable");
  assertExecutable(realExecutable);
  const argvBase = path.basename(executable);
  const realBase = path.basename(realExecutable);
  if (TRAMPOLINE_BASENAMES.has(argvBase) || TRAMPOLINE_BASENAMES.has(realBase)) {
    throw new ContextProviderError("Context provider executable uses a disallowed trampoline basename.", { reason: "unsafe-config" });
  }
  const env = provider.env || {};
  if (!env || typeof env !== "object" || Array.isArray(env)) {
    throw new ContextProviderError("Provider env must be an object.", { reason: "unsafe-config" });
  }
  for (const [key, value] of Object.entries(env)) {
    if (!/^GEMINI_CONTEXT_PROVIDER_[A-Z0-9_]+$/.test(key) || typeof value !== "string") {
      throw new ContextProviderError(`Unsafe provider env key ${key}.`, { reason: "unsafe-config" });
    }
  }
  return {
    command: [realExecutable, ...provider.command.slice(1)],
    env,
    timeoutMs: provider.timeoutMs,
    maxOutputBytes: provider.maxOutputBytes
  };
}

export function resolveProviderSelection(options, cwd = process.cwd(), env = process.env) {
  const workspaceRoot = canonicalWorkspaceRoot(cwd);
  if (!options.provider || options.provider === "off") {
    return { status: "disabled", failureReason: "disabled", providerName: "", workspaceRoot };
  }
  const { configPath, config } = readConfig(env);
  if (!config) {
    if (options.provider === "auto") {
      return { status: "unavailable", failureReason: "disabled", providerName: "", workspaceRoot };
    }
    throw new ContextProviderError(`Unknown context provider "${options.provider}".`, { reason: "unknown-provider" });
  }
  const providers = config.providers && typeof config.providers === "object" ? config.providers : {};
  const providerName = options.provider === "auto" ? config.defaultProvider : options.provider;
  if (!providerName || !/^[a-zA-Z0-9_.-]{1,64}$/.test(providerName) || providerName === "off" || providerName === "auto" || !providers[providerName]) {
    throw new ContextProviderError(`Unknown context provider "${providerName || options.provider}".`, { reason: "unknown-provider" });
  }
  const provider = validateProviderConfig(providerName, providers[providerName], configPath, workspaceRoot);
  return { status: "selected", failureReason: "none", providerName, provider, configPath, workspaceRoot };
}

function byteTruncate(value, maxBytes) {
  const text = String(value ?? "");
  const buffer = Buffer.from(text, "utf8");
  if (buffer.length <= maxBytes) return text;
  return buffer.subarray(0, maxBytes).toString("utf8");
}

function xmlEscape(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function childEnv(providerEnv) {
  const env = {};
  for (const key of ["PATH", "LANG", "LC_ALL"]) {
    if (process.env[key]) env[key] = process.env[key];
  }
  for (const [key, value] of Object.entries(providerEnv || {})) {
    env[key] = value;
  }
  return env;
}

function changedFiles(cwd, run) {
  const status = run("git", ["status", "--short", "--untracked-files=all"], { cwd });
  if (status.status !== 0) return [];
  return status.stdout.split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.slice(3).trim())
    .filter(Boolean);
}

export function buildProviderRequest(args, cwd, workspaceRoot, run) {
  const paths = args.paths?.length ? args.paths : args.path ? [args.path] : [];
  return {
    version: 1,
    workspaceRoot,
    scope: args.scope || "auto",
    changedFiles: changedFiles(cwd, run),
    paths: paths.map((item) => path.relative(workspaceRoot, path.resolve(cwd, item))).filter((item) => item && !item.startsWith("..")),
    focus: byteTruncate(args._?.join(" ").trim() || "", MAX_FOCUS_BYTES),
    maxOutputBytes: args.contextBudget ?? DEFAULT_BUDGET
  };
}

async function runProviderProcess(provider, request, timeoutMs, maxOutputBytes) {
  return new Promise((resolve) => {
    const started = Date.now();
    const child = spawn(provider.command[0], provider.command.slice(1), {
      env: childEnv(provider.env),
      stdio: ["pipe", "pipe", "pipe"]
    });
    let stdout = Buffer.alloc(0);
    let stderr = Buffer.alloc(0);
    let killedForBudget = false;
    let settled = false;
    const timer = setTimeout(() => {
      try { child.kill("SIGTERM"); } catch {}
      setTimeout(() => {
        try { child.kill("SIGKILL"); } catch {}
      }, 500).unref?.();
    }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      stdout = Buffer.concat([stdout, Buffer.from(chunk)]);
      if (stdout.length > maxOutputBytes) {
        killedForBudget = true;
        try { child.kill("SIGTERM"); } catch {}
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr = Buffer.concat([stderr, Buffer.from(chunk)]);
      if (stderr.length > STDERR_LIMIT) {
        stderr = stderr.subarray(0, STDERR_LIMIT);
      }
    });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ ok: false, reason: "nonzero", stdout: "", stderrBytes: stderr.length, durationMs: Date.now() - started, error: error.message });
    });
    child.on("close", (status, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (killedForBudget) {
        resolve({ ok: false, reason: "budget-exceeded", stdout: stdout.subarray(0, maxOutputBytes).toString("utf8"), stderrBytes: stderr.length, durationMs: Date.now() - started });
      } else if (signal) {
        resolve({ ok: false, reason: "timeout", stdout: stdout.toString("utf8"), stderrBytes: stderr.length, durationMs: Date.now() - started });
      } else if (status !== 0) {
        resolve({ ok: false, reason: "nonzero", stdout: stdout.toString("utf8"), stderrBytes: stderr.length, durationMs: Date.now() - started });
      } else {
        resolve({ ok: true, reason: "none", stdout: stdout.toString("utf8"), stderrBytes: stderr.length, durationMs: Date.now() - started });
      }
    });
    child.stdin.end(`${JSON.stringify(request)}\n`);
  });
}

function normalizePath(providerPath, workspaceRoot) {
  const absolute = path.isAbsolute(providerPath)
    ? path.resolve(providerPath)
    : path.resolve(workspaceRoot, providerPath);
  let realPath;
  try {
    realPath = fs.realpathSync.native(absolute);
  } catch {
    realPath = path.resolve(absolute);
  }
  const relative = path.relative(workspaceRoot, realPath);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    return "";
  }
  return relative;
}

function renderContext(providerName, parsed, workspaceRoot, budget) {
  const warnings = Array.isArray(parsed.warnings) ? parsed.warnings : [];
  const items = Array.isArray(parsed.items) ? parsed.items : [];
  const lines = [`<gemini_context provider="${xmlEscape(providerName)}" status="available">`];
  let degraded = false;
  for (const item of items) {
    const normalizedPath = normalizePath(String(item?.path ?? ""), workspaceRoot);
    if (!normalizedPath) {
      warnings.push("dropped provider item with unsafe path");
      degraded = true;
      continue;
    }
    const block = [
      `<item path="${xmlEscape(normalizedPath)}">`,
      item?.symbol ? `<symbol>${xmlEscape(byteTruncate(item.symbol, MAX_FIELD_BYTES))}</symbol>` : "",
      item?.summary ? `<summary>${xmlEscape(byteTruncate(item.summary, MAX_FIELD_BYTES))}</summary>` : "",
      item?.reason ? `<reason>${xmlEscape(byteTruncate(item.reason, MAX_FIELD_BYTES))}</reason>` : "",
      "</item>"
    ].filter(Boolean);
    const candidate = `${lines.concat(block, ["</gemini_context>"]).join("\n")}\n`;
    if (Buffer.byteLength(candidate, "utf8") > budget) {
      degraded = true;
      break;
    }
    lines.push(...block);
  }
  for (const warning of warnings.slice(0, 5)) {
    const block = `<warning>${xmlEscape(byteTruncate(warning, 512))}</warning>`;
    const candidate = `${lines.concat([block, "</gemini_context>"]).join("\n")}\n`;
    if (Buffer.byteLength(candidate, "utf8") > budget) {
      degraded = true;
      break;
    }
    lines.push(block);
  }
  lines.push("</gemini_context>");
  return { block: lines.join("\n"), bytes: Buffer.byteLength(lines.join("\n"), "utf8"), degraded };
}

export async function resolveContext(args, options) {
  const contextOptions = parseContextOptions(args);
  const cwd = options.cwd || process.cwd();
  const selection = resolveProviderSelection(contextOptions, cwd, options.env || process.env);
  if (selection.status === "disabled" || selection.status === "unavailable") {
    return {
      block: selection.status === "unavailable" ? unavailableContextBlock(selection.providerName, selection.failureReason) : "",
      metadata: {
        contextProvider: "",
        contextStatus: selection.status,
        contextBytes: 0,
        contextDurationMs: 0,
        contextFailureReason: selection.failureReason,
        contextDegraded: false
      }
    };
  }
  const request = buildProviderRequest(args, cwd, selection.workspaceRoot, options.run);
  const timeoutMs = Number(selection.provider.timeoutMs || contextOptions.timeoutMs);
  const maxOutputBytes = Number(selection.provider.maxOutputBytes || contextOptions.budget);
  const result = await runProviderProcess(selection.provider, request, timeoutMs, maxOutputBytes);
  if (!result.ok) {
    return {
      block: unavailableContextBlock(selection.providerName, result.reason),
      metadata: {
        contextProvider: selection.providerName,
        contextStatus: "unavailable",
        contextBytes: 0,
        contextDurationMs: result.durationMs,
        contextFailureReason: result.reason,
        contextDegraded: true
      }
    };
  }
  let parsed;
  try {
    parsed = JSON.parse(result.stdout || "{}");
  } catch {
    return {
      block: unavailableContextBlock(selection.providerName, "malformed-json"),
      metadata: {
        contextProvider: selection.providerName,
        contextStatus: "unavailable",
        contextBytes: 0,
        contextDurationMs: result.durationMs,
        contextFailureReason: "malformed-json",
        contextDegraded: true
      }
    };
  }
  if (parsed.provider !== selection.providerName) {
    return {
      block: unavailableContextBlock(selection.providerName, "invalid-response"),
      metadata: {
        contextProvider: selection.providerName,
        contextStatus: "unavailable",
        contextBytes: 0,
        contextDurationMs: result.durationMs,
        contextFailureReason: "invalid-response",
        contextDegraded: true
      }
    };
  }
  const rendered = renderContext(selection.providerName, parsed, selection.workspaceRoot, contextOptions.budget);
  return {
    block: rendered.block,
    metadata: {
      contextProvider: selection.providerName,
      contextStatus: "available",
      contextBytes: rendered.bytes,
      contextDurationMs: result.durationMs,
      contextFailureReason: rendered.degraded ? "budget-exceeded" : "none",
      contextDegraded: rendered.degraded
    }
  };
}

export function unavailableContextBlock(providerName, reason) {
  return [
    `<gemini_context provider="${xmlEscape(providerName)}" status="unavailable" reason="${xmlEscape(reason)}">`,
    "Requested Gemini context was unavailable. Treat any context-dependent conclusion as degraded.",
    "</gemini_context>"
  ].join("\n");
}
