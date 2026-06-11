import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";
import { createGitMcpConfig } from "./mcp-config.mjs";
import { formatProgressEvent } from "./progress.mjs";

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
export const WRITE_DANGER_CANDIDATES = WRITE_DENY_TOOLS;
export const DENY_TOOLS_ENV = "CLAUDE_FOR_CODEX_DENY_TOOLS";
export const SDK_NATIVE_PARENT_TOOL = "Agent";

const SDK_PACKAGES = Object.freeze([
  "@anthropic-ai/claude-agent-sdk",
  "@anthropic-ai/claude-code"
]);
const SDK_PATH_ENV = "CLAUDE_FOR_CODEX_SDK_MODULE";
const BACKEND_ENV = "CLAUDE_FOR_CODEX_BACKEND";
const UNKNOWN_DENY_PARSE_LIMIT = 8192;
const UNKNOWN_DENY_PATTERN = /^\s*Permission deny rule "([^"\r\n]{1,128})" matches no known tool(?:\.|\s+[—-]\s+check for typos\.)?\s*$/m;
// Broad by design: ambiguous stderr that looks like model output must skip retry and fail closed.
const MODEL_OUTPUT_METADATA_PATTERN = /\b(?:input_tokens|output_tokens|total_tokens|usage|cost|model|duration_ms)\b/i;
const BENIGN_NON_MODEL_STDOUT_PATTERNS = Object.freeze([
  /^Not logged in · Please run \/login\s*$/,
  /^You've hit your session limit · resets .+\s*$/
]);

export function normalizeToolName(tool) {
  return String(tool ?? "").trim().toLowerCase();
}

export function uniqueCanonicalTools(tools) {
  const byNormalized = new Map();
  const result = [];
  for (const tool of tools || []) {
    if (typeof tool !== "string" || !tool.trim()) {
      continue;
    }
    const canonical = tool.trim();
    const normalized = normalizeToolName(canonical);
    const existing = byNormalized.get(normalized);
    if (existing && existing !== canonical) {
      throw new Error("Tool name collision after normalization: " + existing + ", " + canonical);
    }
    if (!existing) {
      byNormalized.set(normalized, canonical);
      result.push(canonical);
    }
  }
  return result;
}

export function configuredWriteDenyTools(env = process.env, candidates = WRITE_DANGER_CANDIDATES) {
  const canonicalCandidates = uniqueCanonicalTools(candidates);
  const byNormalized = new Map(canonicalCandidates.map((tool) => [normalizeToolName(tool), tool]));
  const configured = env?.[DENY_TOOLS_ENV];
  if (typeof configured !== "string" || configured.trim() === "") {
    return [...canonicalCandidates];
  }
  const requested = configured.split(",").map((tool) => normalizeToolName(tool)).filter(Boolean);
  const result = [];
  const seen = new Set();
  for (const normalized of requested) {
    const canonical = byNormalized.get(normalized);
    if (!canonical || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    result.push(canonical);
  }
  if (result.length === 0) {
    throw new Error(
      `${DENY_TOOLS_ENV} did not match any supported write-deny tools. ` +
      `Use a comma-separated subset of: ${canonicalCandidates.join(", ")}.`
    );
  }
  return result;
}

export function formatDenyToolsForCli(tools) {
  return uniqueCanonicalTools(tools).join(",");
}

export function buildDenyToolsAfterOmission(currentTools, candidate) {
  const omitted = normalizeToolName(candidate);
  return uniqueCanonicalTools(currentTools).filter((tool) => normalizeToolName(tool) !== omitted);
}

export function nonModelStdoutDiagnostic(stdout = "") {
  const trimmedStdout = String(stdout ?? "").trim();
  return BENIGN_NON_MODEL_STDOUT_PATTERNS.some((pattern) => pattern.test(trimmedStdout))
    ? trimmedStdout
    : "";
}

export function parseUnknownDenyToolFailure({ stdout = "", stderr = "" } = {}, candidates = WRITE_DANGER_CANDIDATES) {
  const stdoutText = String(stdout ?? "");
  const stderrText = String(stderr ?? "");
  const trimmedStdout = stdoutText.trim();
  const trimmedStderr = stderrText.trim();
  const stdoutOnlyUnknownDeny = trimmedStdout && !trimmedStderr && UNKNOWN_DENY_PATTERN.test(trimmedStdout);
  const stderrUnknownDeny = trimmedStderr && UNKNOWN_DENY_PATTERN.test(trimmedStderr);
  if (trimmedStdout && !stdoutOnlyUnknownDeny && !stderrUnknownDeny && !nonModelStdoutDiagnostic(trimmedStdout)) {
    return null;
  }
  const diagnosticText = stdoutOnlyUnknownDeny ? stdoutText : stderrText;
  const boundedStderr = diagnosticText.slice(0, UNKNOWN_DENY_PARSE_LIMIT + 1);
  if (boundedStderr.length > UNKNOWN_DENY_PARSE_LIMIT) {
    return null;
  }
  if (MODEL_OUTPUT_METADATA_PATTERN.test(boundedStderr)) {
    return null;
  }
  const match = UNKNOWN_DENY_PATTERN.exec(boundedStderr);
  if (!match) {
    return null;
  }
  const byNormalized = new Map(uniqueCanonicalTools(candidates).map((tool) => [normalizeToolName(tool), tool]));
  return byNormalized.get(normalizeToolName(match[1])) ?? null;
}

export function denyToolsDiagnosticEnv(tools) {
  return DENY_TOOLS_ENV + "=" + formatDenyToolsForCli(tools);
}

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

function sdkSearchRoots(cwd = process.cwd()) {
  const roots = [{
    root: cwd,
    requireFile: path.join(cwd, "package.json"),
    source: "local"
  }];
  const globalRoot = npmGlobalRoot();
  if (globalRoot) {
    roots.push({
      root: globalRoot,
      requireFile: path.join(globalRoot, ".claude-for-codex-global-resolver.js"),
      source: "global"
    });
  }
  return roots;
}

function packagePathInNodeModules(nodeModulesRoot, packageName) {
  return path.join(nodeModulesRoot, ...packageName.split("/"));
}

function resolveSdkFromSearchRoot(searchRoot, packageName) {
  if (searchRoot.source === "global") {
    const packageRoot = packagePathInNodeModules(searchRoot.root, packageName);
    const packageJsonPath = path.join(packageRoot, "package.json");
    if (!fs.existsSync(packageJsonPath)) {
      return null;
    }
    const requireFromPackage = createRequire(packageJsonPath);
    const modulePath = requireFromPackage.resolve(packageName);
    return {
      modulePath,
      packageJsonPath: packageJsonFromEntry(modulePath, packageName) || packageJsonPath
    };
  }

  const requireFromRoot = createRequire(searchRoot.requireFile);
  const modulePath = requireFromRoot.resolve(packageName);
  return {
    modulePath,
    packageJsonPath: packageJsonFromEntry(modulePath, packageName)
  };
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

  for (const root of sdkSearchRoots(cwd)) {
    for (const packageName of SDK_PACKAGES) {
      try {
        const resolved = resolveSdkFromSearchRoot(root, packageName);
        if (!resolved) {
          continue;
        }
        return {
          importPath: pathToFileURL(resolved.modulePath).href,
          packageJsonPath: resolved.packageJsonPath,
          packageName,
          source: root.source
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

export function sdkReadOnlyOptions(mcpConfig, options = {}) {
  let config = {};
  try {
    config = JSON.parse(fs.readFileSync(mcpConfig.configPath, "utf8"));
  } catch {
    config = {};
  }
  const disallowedTools = options.disallowedTools ?? configuredWriteDenyTools(process.env);
  const formattedDenyTools = formatDenyToolsForCli(disallowedTools);
  if (!formattedDenyTools) {
    throw new Error("Claude SDK read-only review requires at least one disallowed write tool.");
  }
  return {
    permissionMode: "dontAsk",
    allowedTools: [...READ_ONLY_BUILTIN_TOOLS, ...READ_ONLY_MCP_TOOLS],
    disallowedTools: formattedDenyTools.split(","),
    mcpServers: config.mcpServers ?? {},
    strictMcpConfig: true,
    settingSources: [],
    skills: [],
    hooks: {},
    plugins: [],
    persistSession: false,
    env: {
      ...process.env,
      CLAUDE_FOR_CODEX_ISOLATED_REVIEW: "1"
    }
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

function fullErrorMessage(error) {
  return String(error?.message ?? error ?? "");
}

function compactStopDetails(details) {
  if (!details || typeof details !== "object" || Array.isArray(details)) {
    return undefined;
  }
  const output = {};
  for (const key of ["category", "reason", "code"]) {
    if (typeof details[key] === "string" || typeof details[key] === "number" || typeof details[key] === "boolean") {
      output[key] = details[key];
    }
  }
  return Object.keys(output).length ? output : undefined;
}

function compactUsage(usage) {
  if (!usage || typeof usage !== "object" || Array.isArray(usage)) {
    return undefined;
  }
  const output = {};
  const numericCounts = Object.fromEntries(
    Object.entries(usage).filter(([, value]) => typeof value === "number")
  );
  Object.assign(output, numericCounts);
  if (Array.isArray(usage.iterations)) {
    output.iterations = usage.iterations.map((entry) => ({
      type: typeof entry?.type === "string" ? entry.type : undefined,
      model: typeof entry?.model === "string" ? entry.model : undefined
    })).filter((entry) => entry.type || entry.model);
  }
  return Object.keys(output).length ? output : undefined;
}

function compactSdkEvent(event) {
  if (!event || typeof event !== "object") {
    return typeof event === "string" ? { type: "text" } : { type: typeof event };
  }
  const compact = {
    type: typeof event.type === "string" ? event.type : undefined,
    subtype: typeof event.subtype === "string" ? event.subtype : undefined,
    stop_reason: typeof event.stop_reason === "string" ? event.stop_reason : undefined,
    stop_details: compactStopDetails(event.stop_details),
    usage: compactUsage(event.usage)
  };
  return Object.fromEntries(Object.entries(compact).filter(([, value]) => value !== undefined));
}

function metadataFromEvents(events) {
  const metadata = {
    sdkMessageCount: events.length,
    sdkEvents: events.map(compactSdkEvent)
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
      if (Object.prototype.hasOwnProperty.call(event, "structured_output")) {
        metadata.structuredOutput = event.structured_output;
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

function sanitizedSdkProgressLine(event) {
  const eventType = event && typeof event === "object" && typeof event.type === "string"
    ? event.type
    : typeof event;
  const parts = [`[claude-for-codex sdk progress] ${eventType}`];
  if (event && typeof event === "object" && typeof event.total_cost_usd === "number") {
    parts.push(`cost_usd=${event.total_cost_usd}`);
  }
  return parts.join(" ");
}

function maybeWriteSdkProgress(event, options) {
  if (!options.streamProgress) {
    return;
  }
  process.stderr.write(`${sanitizedSdkProgressLine(event)}\n`);
  const eventType = event && typeof event === "object" && typeof event.type === "string"
    ? event.type
    : typeof event;
  const role = event && typeof event === "object"
    ? (event.agent_name ?? event.agentName ?? event.subagent_name ?? event.subagentName ?? "")
    : "";
  process.stderr.write(formatProgressEvent({
    phase: `sdk-${eventType}`,
    message: `${eventType} event received`,
    role
  }, { cwd: options.cwd ?? process.cwd() }));
}

function shouldCollectSdkText(event, options) {
  if (!options.streamProgress || !event || typeof event !== "object") {
    return true;
  }
  return event.type === "result";
}

async function collectSdkOutput(queryResult, options = {}) {
  const events = [];
  const chunks = [];
  if (queryResult && typeof queryResult[Symbol.asyncIterator] === "function") {
    for await (const event of queryResult) {
      events.push(event);
      maybeWriteSdkProgress(event, options);
      const text = shouldCollectSdkText(event, options) ? textFromEvent(event) : "";
      if (text) {
        chunks.push(text);
      }
    }
  } else {
    events.push(queryResult);
    maybeWriteSdkProgress(queryResult, options);
    const text = shouldCollectSdkText(queryResult, options) ? textFromEvent(queryResult) : "";
    if (text) {
      chunks.push(text);
    }
  }
  return {
    stdout: chunks.join(""),
    metadata: metadataFromEvents(events)
  };
}

function sdkErrorResult(error, abortController) {
  return {
    status: abortController.signal.aborted ? 130 : 1,
    stdout: "",
    stderr: sanitizeError(error),
    error: sanitizeError(error),
    errorCode: abortController.signal.aborted ? "SDK_ABORTED" : "SDK_ERROR",
    backend: "sdk",
    metadata: {}
  };
}

function applySdkReviewOptions(sdkOptions, args = {}, options = {}) {
  if (args.nativeAgents) {
    sdkOptions.agents = Object.fromEntries(
      Object.entries(args.nativeAgents).map(([name, agent]) => [
        name,
        {
          ...agent,
          disallowedTools: Array.isArray(agent?.disallowedTools)
            ? [...sdkOptions.disallowedTools, ...agent.disallowedTools.filter((tool) => normalizeToolName(tool) === normalizeToolName(SDK_NATIVE_PARENT_TOOL))]
            : [...sdkOptions.disallowedTools]
        }
      ])
    );
    if (!sdkOptions.allowedTools.includes(SDK_NATIVE_PARENT_TOOL)) {
      sdkOptions.allowedTools = [...sdkOptions.allowedTools, SDK_NATIVE_PARENT_TOOL];
    }
  }
  if (args.nativeStructured && options.outputSchema) {
    sdkOptions.outputFormat = {
      type: "json_schema",
      schema: options.outputSchema
    };
  }
  if (args.outputFormat !== undefined || options.outputFormat !== undefined) {
    sdkOptions.outputFormat = args.outputFormat ?? options.outputFormat;
  }
  if (args.streamProgress || options.streamProgress) {
    sdkOptions.includePartialMessages = true;
  }
  if (args.includePartialMessages !== undefined || options.includePartialMessages !== undefined) {
    sdkOptions.includePartialMessages = args.includePartialMessages ?? options.includePartialMessages;
  }
  return sdkOptions;
}

async function runSdkQueryOnce({ query, prompt, args, options, abortSignal, disallowedTools }) {
  let mcpConfig = null;
  try {
    const sdkOptions = args.write
      ? sdkWriteOptions()
      : sdkReadOnlyOptions(mcpConfig = createGitMcpConfig(options.cwd ?? process.cwd(), process.env), { disallowedTools });
    if (!args.write && (!Array.isArray(sdkOptions.allowedTools) || !Array.isArray(sdkOptions.disallowedTools))) {
      throw new Error("Claude SDK read-only tool restrictions are unavailable.");
    }
    applySdkReviewOptions(sdkOptions, args, options);
    const queryResult = query({
      prompt,
      cwd: options.cwd ?? process.cwd(),
      model: args.model || undefined,
      effort: args.effort || undefined,
      signal: abortSignal,
      options: sdkOptions,
      permissionMode: sdkOptions.permissionMode,
      allowedTools: sdkOptions.allowedTools,
      disallowedTools: sdkOptions.disallowedTools,
      mcpServers: sdkOptions.mcpServers,
      strictMcpConfig: sdkOptions.strictMcpConfig,
      settingSources: sdkOptions.settingSources,
      skills: sdkOptions.skills,
      hooks: sdkOptions.hooks,
      plugins: sdkOptions.plugins,
      persistSession: sdkOptions.persistSession,
      env: sdkOptions.env,
      agents: sdkOptions.agents,
      outputFormat: sdkOptions.outputFormat,
      includePartialMessages: sdkOptions.includePartialMessages
    });
    const output = await collectSdkOutput(queryResult, {
      streamProgress: Boolean(args.streamProgress || options.streamProgress),
      cwd: options.cwd ?? process.cwd()
    });
    return {
      status: 0,
      stdout: output.stdout,
      stderr: "",
      error: "",
      errorCode: "",
      backend: "sdk",
      metadata: output.metadata
    };
  } finally {
    if (mcpConfig && process.env.CLAUDE_FOR_CODEX_KEEP_MCP_CONFIG !== "1") {
      mcpConfig.cleanup();
    }
  }
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
    if (args.write) {
      return await runSdkQueryOnce({ query, prompt, args, options, abortSignal: abortController.signal });
    }

    let denyTools = configuredWriteDenyTools(process.env);
    const omitted = new Set();
    while (true) {
      try {
        return await runSdkQueryOnce({ query, prompt, args, options, abortSignal: abortController.signal, disallowedTools: denyTools });
      } catch (error) {
        const candidate = parseUnknownDenyToolFailure({ stdout: "", stderr: fullErrorMessage(error) }, denyTools);
        if (!candidate || omitted.has(candidate)) {
          return sdkErrorResult(error, abortController);
        }
        const remainingTools = buildDenyToolsAfterOmission(denyTools, candidate);
        if (remainingTools.length === denyTools.length || remainingTools.length === 0) {
          return sdkErrorResult(error, abortController);
        }
        omitted.add(candidate);
        denyTools = remainingTools;
      }
    }
  } catch (error) {
    return sdkErrorResult(error, abortController);
  } finally {
    process.removeListener("SIGINT", signalHandler);
    process.removeListener("SIGTERM", signalHandler);
    for (const listener of previousSigint) process.on("SIGINT", listener);
    for (const listener of previousSigterm) process.on("SIGTERM", listener);
  }
}

export async function runSdkNativeReview(prompt, args = {}, options = {}) {
  const resolved = resolveSdkModule(process.env, options.cwd ?? process.cwd());
  if (!resolved) {
    return {
      status: 1,
      stdout: "",
      stderr: "Claude SDK native subagents requested but the Claude Agent SDK is unavailable.",
      error: "Claude SDK unavailable",
      errorCode: "SDK_UNAVAILABLE",
      backend: "sdk",
      metadata: {}
    };
  }
  return runSdkPrompt(prompt, {
    ...args,
    nativeAgents: options.agents,
    requireAgentTool: true
  }, options);
}
