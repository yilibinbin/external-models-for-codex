import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { canonicalWorkspaceRoot } from "./workspace.mjs";
import { stateDirForCwd } from "./state.mjs";

const VALID_MODES = new Set(["off", "auto"]);
const PROVIDER_NAME_PATTERN = /^[a-z][a-z0-9_-]{0,63}$/;
const SAFE_ENV_PATTERN = /^SEMANTIC_PROVIDER_[A-Z0-9_]+$/;
const DENIED_EXECUTABLES = new Set(["sh", "bash", "zsh", "fish", "env", "node", "python", "python3", "ruby", "perl"]);
const FAILURE_REASONS = new Set([
  "unconfigured",
  "unknown_provider",
  "unsafe_config",
  "timeout",
  "nonzero_exit",
  "invalid_json",
  "byte_limit",
  "validation_error"
]);
const DEFAULT_BUDGET_BYTES = 32768;
const DEFAULT_TIMEOUT_MS = 5000;
const MAX_BUDGET_BYTES = 128 * 1024;
const MAX_TIMEOUT_MS = 30000;

export function parseSemanticOptions(tokens, index, parsed) {
  const token = tokens[index];
  if (token === "--semantic-context") {
    parsed.semanticContext = readOptionValue(tokens, index, token);
    return index + 1;
  }
  if (token === "--semantic-budget") {
    const value = Number(readOptionValue(tokens, index, token));
    if (!Number.isInteger(value) || value <= 0 || value > MAX_BUDGET_BYTES) {
      throw new Error(`--semantic-budget must be a positive integer no greater than ${MAX_BUDGET_BYTES}.`);
    }
    parsed.semanticBudget = value;
    return index + 1;
  }
  if (token === "--semantic-timeout-ms") {
    const value = Number(readOptionValue(tokens, index, token));
    if (!Number.isInteger(value) || value <= 0 || value > MAX_TIMEOUT_MS) {
      throw new Error(`--semantic-timeout-ms must be a positive integer no greater than ${MAX_TIMEOUT_MS}.`);
    }
    parsed.semanticTimeoutMs = value;
    return index + 1;
  }
  return index;
}

export function validateSemanticArgs(args, { allowAuto = true } = {}) {
  const mode = args.semanticContext ?? "off";
  if (mode === "auto" && !allowAuto) {
    throw new Error("--semantic-context auto is not allowed for this command; choose off or an explicit provider name.");
  }
  if (!VALID_MODES.has(mode) && !PROVIDER_NAME_PATTERN.test(mode)) {
    throw new Error(`Invalid --semantic-context "${mode}". Use off, auto, or a provider name.`);
  }
}

export function semanticProviderCandidates(env = process.env) {
  const candidates = ["codegraph", "serena", "claude-context"];
  return Object.fromEntries(candidates.map((name) => [name, {
    availableOnPath: Boolean(findOnPath(name, env)),
    configured: false,
    configSafe: false,
    configPath: "",
    configError: ""
  }]));
}

export function semanticCapabilities(cwd = process.cwd(), env = process.env) {
  const candidates = semanticProviderCandidates(env);
  const configPath = semanticConfigPath(cwd, env);
  const loaded = loadProviderConfig(cwd, env);
  if (!loaded.config) {
    return {
      configPath,
      configSafe: loaded.safe,
      configError: loaded.error,
      providers: candidates
    };
  }
  for (const provider of Object.keys(loaded.config.providers ?? {})) {
    candidates[provider] = {
      ...(candidates[provider] ?? { availableOnPath: false }),
      configured: true,
      configSafe: loaded.safe,
      configPath,
      configError: loaded.error
    };
  }
  return {
    configPath,
    configSafe: loaded.safe,
    configError: loaded.error,
    defaultProvider: loaded.config.defaultProvider ?? "",
    providers: candidates
  };
}

export function buildSemanticContext(args, context = {}) {
  validateSemanticArgs(args, { allowAuto: !context.reviewGate });
  const mode = args.semanticContext ?? "off";
  if (mode === "off") {
    return offContext();
  }

  const cwd = context.cwd ?? process.cwd();
  const workspaceRoot = canonicalWorkspaceRoot(cwd);
  const budget = args.semanticBudget ?? DEFAULT_BUDGET_BYTES;
  const loaded = loadProviderConfig(cwd, process.env);
  if (!loaded.config) {
    if (mode === "auto" && loaded.reason === "unconfigured") {
      return offContext();
    }
    if (mode !== "auto" && loaded.reason === "unconfigured") {
      throw new Error(`Unknown semantic provider "${mode}".`);
    }
    return unavailableContext({
      provider: mode === "auto" ? "" : mode,
      reason: loaded.reason ?? "unconfigured",
      warning: loaded.error
    });
  }
  if (!loaded.safe) {
    return unavailableContext({
      provider: mode === "auto" ? loaded.config.defaultProvider : mode,
      reason: "unsafe_config",
      warning: loaded.error
    });
  }

  const providerName = mode === "auto" ? loaded.config.defaultProvider : mode;
  if (!providerName || !PROVIDER_NAME_PATTERN.test(providerName)) {
    return unavailableContext({ provider: providerName, reason: "unknown_provider" });
  }
  const provider = loaded.config.providers?.[providerName];
  if (!provider) {
    if (mode === "auto") {
      return unavailableContext({ provider: providerName, reason: "unknown_provider" });
    }
    throw new Error(`Unknown semantic provider "${providerName}".`);
  }

  let normalizedProvider;
  try {
    normalizedProvider = normalizeProviderConfig(provider, providerName, workspaceRoot, process.env);
  } catch (error) {
    return unavailableContext({ provider: providerName, reason: "unsafe_config", warning: safeWarning(error) });
  }

  const started = Date.now();
  const request = {
    version: 1,
    workspaceRoot,
    scope: args.scope ?? "auto",
    paths: args.paths ?? [],
    changedFiles: context.changedFiles ?? [],
    focus: args._?.join(" ").trim() ?? "",
    maxOutputBytes: normalizedProvider.maxOutputBytes
  };
  const result = spawnSync(normalizedProvider.command[0], normalizedProvider.command.slice(1), {
    cwd: workspaceRoot,
    env: normalizedProvider.env,
    input: `${JSON.stringify(request)}\n`,
    encoding: "utf8",
    timeout: args.semanticTimeoutMs ?? normalizedProvider.timeoutMs,
    maxBuffer: normalizedProvider.maxOutputBytes,
    killSignal: "SIGKILL"
  });
  const durationMs = Math.max(0, Date.now() - started);
  if (result.error?.code === "ETIMEDOUT") {
    return unavailableContext({ provider: providerName, reason: "timeout", durationMs });
  }
  if (result.error) {
    return unavailableContext({ provider: providerName, reason: "validation_error", durationMs });
  }
  if ((result.stdout ?? "").length >= normalizedProvider.maxOutputBytes) {
    return unavailableContext({ provider: providerName, reason: "byte_limit", durationMs });
  }
  if (result.status !== 0) {
    return unavailableContext({ provider: providerName, reason: "nonzero_exit", durationMs });
  }
  let parsed;
  try {
    parsed = JSON.parse(result.stdout || "{}");
  } catch {
    return unavailableContext({ provider: providerName, reason: "invalid_json", durationMs });
  }
  try {
    const items = normalizeProviderItems(parsed.items, workspaceRoot, budget);
    const rendered = renderSemanticContext(providerName, "available", items);
    return {
      enabled: true,
      status: "available",
      provider: providerName,
      promptBlock: rendered,
      report: semanticReport({
        provider: providerName,
        status: "available",
        bytes: Buffer.byteLength(rendered, "utf8"),
        durationMs,
        failed: false
      })
    };
  } catch {
    return unavailableContext({ provider: providerName, reason: "validation_error", durationMs });
  }
}

function offContext() {
  return {
    enabled: false,
    status: "off",
    provider: "",
    promptBlock: "",
    report: semanticReport({ status: "off", failed: false })
  };
}

function unavailableContext({ provider = "", reason = "unconfigured", durationMs = 0, warning = "" }) {
  const safeReason = FAILURE_REASONS.has(reason) ? reason : "validation_error";
  return {
    enabled: true,
    status: "unavailable",
    provider,
    promptBlock: renderUnavailable(provider, safeReason),
    warning,
    report: semanticReport({
      provider,
      status: "unavailable",
      durationMs,
      failureReason: safeReason,
      failed: true
    })
  };
}

function semanticReport({ provider = "", status = "off", bytes = 0, durationMs = 0, failureReason = "", failed = false }) {
  return {
    semanticProvider: provider,
    semanticStatus: status,
    semanticBytes: bytes,
    semanticDurationMs: durationMs,
    semanticFailureReason: failureReason,
    semanticFailed: Boolean(failed)
  };
}

function renderUnavailable(provider, reason) {
  return [
    `<semantic_context provider="${xmlEscape(provider)}" status="unavailable" reason="${xmlEscape(reason)}">`,
    "Semantic context was requested but unavailable. Treat this review as degraded.",
    "</semantic_context>"
  ].join("\n");
}

function renderSemanticContext(provider, status, items) {
  const lines = [
    `<semantic_context provider="${xmlEscape(provider)}" status="${xmlEscape(status)}">`,
    "Semantic context is advisory. Do not cite it alone for a finding; tie findings to changed files or git context."
  ];
  for (const item of items) {
    lines.push([
      `<item path="${xmlEscape(item.path)}"${item.symbol ? ` symbol="${xmlEscape(item.symbol)}"` : ""}>`,
      xmlEscape(item.summary),
      item.reason ? `<reason>${xmlEscape(item.reason)}</reason>` : "",
      "</item>"
    ].filter(Boolean).join("\n"));
  }
  lines.push("</semantic_context>");
  return lines.join("\n");
}

function normalizeProviderItems(items, workspaceRoot, budget) {
  const normalized = [];
  let bytes = 0;
  for (const item of Array.isArray(items) ? items : []) {
    const relativePath = normalizeWorkspacePath(item?.path, workspaceRoot);
    const summary = truncateText(item?.summary, 1000);
    if (!summary) {
      continue;
    }
    const entry = {
      path: relativePath,
      symbol: truncateText(item?.symbol, 120),
      summary,
      reason: truncateText(item?.reason, 240)
    };
    bytes += Buffer.byteLength(JSON.stringify(entry), "utf8");
    if (bytes > budget) {
      break;
    }
    normalized.push(entry);
  }
  return normalized;
}

function normalizeWorkspacePath(rawPath, workspaceRoot) {
  if (typeof rawPath !== "string" || !rawPath.trim()) {
    throw new Error("semantic item path is required");
  }
  const absolute = path.isAbsolute(rawPath)
    ? path.resolve(rawPath)
    : path.resolve(workspaceRoot, rawPath);
  const rootReal = safeRealpath(workspaceRoot);
  const targetReal = safeRealpath(absolute);
  if (!isInside(rootReal, targetReal)) {
    throw new Error("semantic item path escapes workspace");
  }
  return path.relative(rootReal, targetReal) || ".";
}

function normalizeProviderConfig(provider, providerName, workspaceRoot, env) {
  if (!provider || typeof provider !== "object") {
    throw new Error(`Provider ${providerName} must be an object.`);
  }
  if (!Array.isArray(provider.command) || !provider.command.every((part) => typeof part === "string" && part)) {
    throw new Error(`Provider ${providerName} command must be an argv array.`);
  }
  const executable = resolveExecutable(provider.command[0], env);
  const basename = path.basename(executable);
  if (DENIED_EXECUTABLES.has(basename)) {
    throw new Error(`Provider ${providerName} command uses denied executable ${basename}.`);
  }
  if (isInside(safeRealpath(workspaceRoot), safeRealpath(executable))) {
    throw new Error(`Provider ${providerName} executable must not live inside the workspace.`);
  }
  return {
    command: [executable, ...provider.command.slice(1)],
    env: providerEnv(provider.env, env),
    timeoutMs: boundedNumber(provider.timeoutMs, DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS),
    maxOutputBytes: boundedNumber(provider.maxOutputBytes, DEFAULT_BUDGET_BYTES, MAX_BUDGET_BYTES)
  };
}

function providerEnv(configEnv = {}, parentEnv = process.env) {
  const safe = {};
  for (const key of ["PATH", "LANG", "LC_ALL"]) {
    if (typeof parentEnv[key] === "string") {
      safe[key] = parentEnv[key];
    }
  }
  if (configEnv && typeof configEnv === "object" && !Array.isArray(configEnv)) {
    for (const [key, value] of Object.entries(configEnv)) {
      if (!SAFE_ENV_PATTERN.test(key)) {
        throw new Error(`Unsafe semantic provider env key ${key}.`);
      }
      safe[key] = String(value);
    }
  }
  return safe;
}

function resolveExecutable(command, env) {
  if (path.isAbsolute(command)) {
    if (isExecutable(command)) {
      return command;
    }
    throw new Error(`Semantic provider executable is not executable: ${command}`);
  }
  const resolved = findOnPath(command, env);
  if (!resolved) {
    throw new Error(`Semantic provider executable not found on PATH: ${command}`);
  }
  return resolved;
}

function semanticConfigPath(cwd, env) {
  if (env.CLAUDE_FOR_CODEX_SEMANTIC_CONFIG) {
    return env.CLAUDE_FOR_CODEX_SEMANTIC_CONFIG;
  }
  if (env.CLAUDE_PLUGIN_DATA) {
    return path.join(env.CLAUDE_PLUGIN_DATA, "semantic", "providers.json");
  }
  return path.join(os.homedir(), ".codex", "claude-for-codex", "semantic", "providers.json");
}

function loadProviderConfig(cwd, env) {
  const configPath = semanticConfigPath(cwd, env);
  if (!fs.existsSync(configPath)) {
    return { config: null, safe: true, error: "", reason: "unconfigured" };
  }
  const workspaceRoot = canonicalWorkspaceRoot(cwd);
  try {
    const configReal = safeRealpath(configPath);
    if (isInside(safeRealpath(workspaceRoot), configReal)) {
      return { config: null, safe: false, error: "semantic config must not live inside workspace", reason: "unsafe_config" };
    }
    validateConfigPermissions(configPath);
    const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    return { config, safe: true, error: "", reason: "" };
  } catch (error) {
    return { config: null, safe: false, error: safeWarning(error), reason: "unsafe_config" };
  }
}

function validateConfigPermissions(configPath) {
  if (process.platform === "win32") {
    return;
  }
  const stat = fs.statSync(configPath);
  if ((stat.mode & 0o022) !== 0) {
    throw new Error("semantic config must not be group-writable or world-writable");
  }
  if (typeof process.getuid === "function" && stat.uid !== process.getuid()) {
    throw new Error("semantic config must be owned by the current user");
  }
}

function boundedNumber(value, fallback, max) {
  if (value === undefined) {
    return fallback;
  }
  const number = Number(value);
  if (!Number.isInteger(number) || number <= 0 || number > max) {
    throw new Error(`Semantic provider numeric option must be between 1 and ${max}.`);
  }
  return number;
}

function findOnPath(commandName, env = process.env) {
  const searchPath = env.PATH || "";
  for (const entry of searchPath.split(path.delimiter)) {
    if (!entry) {
      continue;
    }
    const candidate = path.join(entry, commandName);
    if (isExecutable(candidate)) {
      return candidate;
    }
  }
  return "";
}

function isExecutable(filePath) {
  try {
    fs.accessSync(filePath, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function safeRealpath(value) {
  return fs.realpathSync(value);
}

function isInside(root, target) {
  const relative = path.relative(root, target);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function safeWarning(error) {
  return error?.message ? String(error.message) : String(error);
}

function readOptionValue(tokens, index, optionName) {
  const value = tokens[index + 1];
  if (value === undefined || value === "" || value.startsWith("--")) {
    throw new Error(`Missing value for ${optionName}.`);
  }
  return value;
}

function truncateText(value, max) {
  if (typeof value !== "string") {
    return "";
  }
  return value.length > max ? value.slice(0, max) : value;
}

function xmlEscape(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;");
}
