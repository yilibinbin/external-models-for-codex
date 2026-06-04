import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";
import { createGitMcpConfig } from "./mcp-config.mjs";

export const READ_ONLY_BUILTIN_TOOLS = Object.freeze(["Read", "Grep", "Glob"]);
export const READ_ONLY_MCP_TOOLS = Object.freeze([
  "mcp__claude-for-codex-git__git_status",
  "mcp__claude-for-codex-git__git_diff",
  "mcp__claude-for-codex-git__git_diff_cached",
  "mcp__claude-for-codex-git__git_log",
  "mcp__claude-for-codex-git__git_show",
  "mcp__claude-for-codex-git__git_blame",
  "mcp__claude-for-codex-git__git_grep",
  "mcp__claude-for-codex-git__git_ls_files"
]);
export const WRITE_DENY_TOOLS = Object.freeze(["Edit", "Write", "MultiEdit", "Bash"]);

const SDK_PACKAGES = Object.freeze([
  "@anthropic-ai/claude-agent-sdk",
  "@anthropic-ai/claude-code"
]);
const SDK_PATH_ENV = "CLAUDE_FOR_CODEX_SDK_MODULE";
const BACKEND_ENV = "CLAUDE_FOR_CODEX_BACKEND";

export function resolveBackend(args = {}, env = process.env) {
  const backend = args.backend || env[BACKEND_ENV] || "cli";
  if (!["cli", "sdk"].includes(backend)) {
    throw new Error(`Invalid --backend "${backend}". Valid backends: cli, sdk.`);
  }
  return backend;
}

function safePackageVersion(packageJsonPath) {
  try {
    const parsed = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
    return typeof parsed.version === "string" && parsed.version ? parsed.version : "unknown";
  } catch {
    return "unknown";
  }
}

function sdkPackageFromPackageJson(packageJsonPath) {
  try {
    const parsed = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
    return typeof parsed.name === "string" && parsed.name ? parsed.name : "";
  } catch {
    return "";
  }
}

function packageJsonFromEntry(entryPath, packageName) {
  let current = fs.statSync(entryPath).isDirectory() ? entryPath : path.dirname(entryPath);
  while (true) {
    const packageJsonPath = path.join(current, "package.json");
    if (fs.existsSync(packageJsonPath) && sdkPackageFromPackageJson(packageJsonPath) === packageName) {
      return packageJsonPath;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return "";
    }
    current = parent;
  }
}

function moduleFileFromPackageJson(packageJsonPath) {
  try {
    const parsed = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
    const entry = typeof parsed.module === "string"
      ? parsed.module
      : typeof parsed.main === "string"
        ? parsed.main
        : "index.js";
    return path.join(path.dirname(packageJsonPath), entry);
  } catch {
    return path.join(path.dirname(packageJsonPath), "index.js");
  }
}

function npmGlobalRoot() {
  const result = spawnSync("npm", ["root", "-g"], {
    encoding: "utf8",
    timeout: 5000
  });
  return result.status === 0 ? result.stdout.trim() : "";
}

export function resolveSdkModule(env = process.env, cwd = process.cwd()) {
  if (env[SDK_PATH_ENV]) {
    const modulePath = path.resolve(env[SDK_PATH_ENV]);
    if (!fs.existsSync(modulePath)) {
      return null;
    }
    return {
      importPath: pathToFileURL(modulePath).href,
      packageJsonPath: path.join(path.dirname(modulePath), "package.json"),
      packageName: sdkPackageFromPackageJson(path.join(path.dirname(modulePath), "package.json")) || "env",
      source: "env"
    };
  }

  for (const root of [cwd, npmGlobalRoot()].filter(Boolean)) {
    for (const packageName of SDK_PACKAGES) {
      try {
        const requireFromRoot = createRequire(path.join(root, "package.json"));
        const modulePath = requireFromRoot.resolve(packageName);
        const packageJsonPath = packageJsonFromEntry(modulePath, packageName);
        return {
          importPath: pathToFileURL(modulePath).href,
          packageJsonPath,
          packageName,
          source: root === cwd ? "local" : "global"
        };
      } catch {
        // Try next package/root.
      }
    }
  }
  return null;
}

export function backendCapabilities(env = process.env, cwd = process.cwd()) {
  const resolved = resolveSdkModule(env, cwd);
  const version = resolved?.packageJsonPath ? safePackageVersion(resolved.packageJsonPath) : "";
  return {
    defaultBackend: env[BACKEND_ENV] || "cli",
    requestedBackend: env[BACKEND_ENV] || "cli",
    claudeSdk: {
      available: Boolean(resolved),
      importable: Boolean(resolved),
      version: resolved ? version : "",
      source: resolved?.source ?? "",
      packageName: resolved?.packageName ?? "",
      supportedFeatures: {
        query: Boolean(resolved),
        allowedTools: Boolean(resolved),
        disallowedTools: Boolean(resolved),
        mcpServers: Boolean(resolved),
        permissionMode: Boolean(resolved),
        abortSignal: Boolean(resolved),
        agents: Boolean(resolved),
        outputFormat: Boolean(resolved),
        includePartialMessages: Boolean(resolved)
      }
    }
  };
}

export function sdkReadOnlyOptions(mcpConfig) {
  let config = {};
  try {
    config = JSON.parse(fs.readFileSync(mcpConfig.configPath, "utf8"));
  } catch {
    config = {};
  }
  return {
    permissionMode: "dontAsk",
    allowedTools: [...READ_ONLY_BUILTIN_TOOLS, ...READ_ONLY_MCP_TOOLS],
    disallowedTools: [...WRITE_DENY_TOOLS],
    mcpServers: config.mcpServers ?? {},
    strictMcpConfig: true
  };
}

function sdkWriteOptions() {
  return {
    permissionMode: "bypassPermissions",
    nonInteractive: true
  };
}

function sanitizeError(error) {
  return String(error?.message ?? error ?? "")
    .split(/\r?\n/)
    .slice(0, 3)
    .join("\n");
}

function metadataFromEvents(events) {
  const metadata = {
    sdkMessageCount: events.length
  };
  for (const event of events) {
    if (event && typeof event === "object") {
      if (typeof event.subtype === "string") {
        metadata.sdkResultSubtype = event.subtype;
      }
      if (typeof event.session_id === "string") {
        metadata.sdkSessionIdHash = createHash("sha256").update(event.session_id).digest("hex").slice(0, 16);
      }
      if (typeof event.total_cost_usd === "number") {
        metadata.sdkCostUsd = event.total_cost_usd;
      }
      if (event.usage && typeof event.usage === "object") {
        metadata.sdkTokenCounts = Object.fromEntries(
          Object.entries(event.usage).filter(([, value]) => typeof value === "number")
        );
      }
    }
  }
  return metadata;
}

function textFromEvent(event) {
  if (typeof event === "string") {
    return event;
  }
  if (!event || typeof event !== "object") {
    return "";
  }
  if (typeof event.result === "string") {
    return event.result;
  }
  if (typeof event.text === "string") {
    return event.text;
  }
  if (event.message?.content && Array.isArray(event.message.content)) {
    return event.message.content
      .map((part) => typeof part?.text === "string" ? part.text : "")
      .join("");
  }
  return "";
}

async function collectSdkOutput(queryResult) {
  const events = [];
  const chunks = [];
  if (queryResult && typeof queryResult[Symbol.asyncIterator] === "function") {
    for await (const event of queryResult) {
      events.push(event);
      const text = textFromEvent(event);
      if (text) {
        chunks.push(text);
      }
    }
  } else {
    events.push(queryResult);
    const text = textFromEvent(queryResult);
    if (text) {
      chunks.push(text);
    }
  }
  return {
    stdout: chunks.join(""),
    metadata: metadataFromEvents(events)
  };
}

export async function runSdkPrompt(prompt, args = {}, options = {}) {
  const resolved = resolveSdkModule(process.env, options.cwd ?? process.cwd());
  if (!resolved) {
    return {
      status: 1,
      stdout: "",
      stderr: "Claude SDK backend requested but @anthropic-ai/claude-agent-sdk or @anthropic-ai/claude-code is unavailable.",
      error: "Claude SDK unavailable",
      errorCode: "SDK_UNAVAILABLE",
      backend: "sdk",
      metadata: {}
    };
  }

  let mcpConfig = null;
  const abortController = new AbortController();
  const previousSigint = process.listeners("SIGINT");
  const previousSigterm = process.listeners("SIGTERM");
  const signalHandler = () => abortController.abort();
  process.removeAllListeners("SIGINT");
  process.removeAllListeners("SIGTERM");
  process.once("SIGINT", signalHandler);
  process.once("SIGTERM", signalHandler);

  try {
    const sdk = await import(resolved.importPath);
    const query = sdk.query ?? sdk.default?.query;
    if (typeof query !== "function") {
      throw new Error("Claude SDK does not export query().");
    }
    const sdkOptions = args.write
      ? sdkWriteOptions()
      : sdkReadOnlyOptions(mcpConfig = createGitMcpConfig(options.cwd ?? process.cwd(), process.env));
    if (!args.write && (!Array.isArray(sdkOptions.allowedTools) || !Array.isArray(sdkOptions.disallowedTools))) {
      throw new Error("Claude SDK read-only tool restrictions are unavailable.");
    }
    const queryResult = query({
      prompt,
      cwd: options.cwd ?? process.cwd(),
      model: args.model || undefined,
      effort: args.effort || undefined,
      signal: abortController.signal,
      options: sdkOptions,
      permissionMode: sdkOptions.permissionMode,
      allowedTools: sdkOptions.allowedTools,
      disallowedTools: sdkOptions.disallowedTools,
      mcpServers: sdkOptions.mcpServers,
      strictMcpConfig: sdkOptions.strictMcpConfig
    });
    const output = await collectSdkOutput(queryResult);
    return {
      status: 0,
      stdout: output.stdout,
      stderr: "",
      error: "",
      errorCode: "",
      backend: "sdk",
      metadata: output.metadata
    };
  } catch (error) {
    return {
      status: abortController.signal.aborted ? 130 : 1,
      stdout: "",
      stderr: sanitizeError(error),
      error: sanitizeError(error),
      errorCode: abortController.signal.aborted ? "SDK_ABORTED" : "SDK_ERROR",
      backend: "sdk",
      metadata: {}
    };
  } finally {
    if (mcpConfig && process.env.CLAUDE_FOR_CODEX_KEEP_MCP_CONFIG !== "1") {
      mcpConfig.cleanup();
    }
    process.removeListener("SIGINT", signalHandler);
    process.removeListener("SIGTERM", signalHandler);
    for (const listener of previousSigint) process.on("SIGINT", listener);
    for (const listener of previousSigterm) process.on("SIGTERM", listener);
  }
}
