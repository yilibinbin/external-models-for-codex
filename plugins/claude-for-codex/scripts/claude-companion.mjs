#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import {
  cancelJob,
  claimReservedJob,
  createJob,
  finishJob,
  listJobs,
  markJobRunning,
  readJob,
  reserveJob,
  resultForJob,
  updateJob
} from "./lib/jobs.mjs";
import { createGitMcpConfig } from "./lib/mcp-config.mjs";
import { renderPromptTemplate } from "./lib/prompt-template.mjs";
import { latestReport, listReports, reportFromResult, safeWriteReport } from "./lib/reports.mjs";
import { runReleaseCheck } from "./lib/release-check.mjs";
import {
  aggregateRoleReviewOutputs,
  normalizeAdversarialOutput,
  normalizeReviewOutput
} from "./lib/render-review.mjs";
import {
  buildSemanticContext,
  parseSemanticOptions,
  semanticCapabilities,
  validateSemanticArgs
} from "./lib/semantic-context.mjs";
import {
  StateReadError,
  canonicalWorkspaceRoot,
  currentSessionFileForCwd,
  getConfig,
  readJson,
  readStateReport,
  setConfig,
  stateFileForCwd,
  turnBaselineFileForCwd
} from "./lib/state.mjs";
import { extractJsonObject, validateAdversarialJson } from "./lib/structured-output.mjs";

const VALID_COMMANDS = new Set(["setup", "capabilities", "review", "adversarial-review", "multi-review", "plan", "status", "review-gate", "jobs", "result", "cancel", "rescue", "report", "release-check", "__run-job", "reserve-job", "run-reserved-job"]);
const BACKGROUND_CAPABLE_COMMANDS = new Set(["review", "adversarial-review", "multi-review", "rescue"]);
const VALID_SCOPES = new Set(["auto", "working-tree", "branch"]);
const VALID_REVIEW_GATE_MODES = new Set(["multi-role"]);
const CLAUDE_CODE_PATH_ENV = "CLAUDE_CODE_PATH";
const REVIEW_GATE_ENV = "CLAUDE_FOR_CODEX_REVIEW_GATE";
const REVIEW_GATE_TIMEOUT_MS = 15 * 60 * 1000;
const REVIEW_GATE_ROLE_TIMEOUT_MS = 2 * 60 * 1000;
const ADVERSARIAL_LENSES = Object.freeze({
  skeptic: {
    label: "Skeptic",
    directive: [
      "Challenge correctness and completeness.",
      "Ask what inputs, states, or sequences will break this.",
      "Find unhandled error paths, race conditions, ordering dependencies, and assumptions that are not proven.",
      "Map findings to: prove-it-works, fix-root-causes, serialize-shared-state-mutations."
    ].join(" ")
  },
  architect: {
    label: "Architect",
    directive: [
      "Challenge structural fitness.",
      "Ask whether the design serves the stated goal or an assumed goal.",
      "Find coupling points, boundary violations, responsibility leaks, and assumptions about scale, concurrency, or ordering.",
      "Map findings to: boundary-discipline, foundational-thinking, redesign-from-first-principles."
    ].join(" ")
  },
  minimalist: {
    label: "Minimalist",
    directive: [
      "Challenge necessity and complexity.",
      "Ask what can be deleted without losing the stated goal.",
      "Find speculative abstractions, configuration without a concrete second use case, and thoroughness that does not improve the outcome.",
      "Map findings to: subtract-before-you-add, outcome-oriented-execution, cost-aware-delegation."
    ].join(" ")
  }
});
const DEFAULT_ADVERSARIAL_LENSES = Object.freeze(["skeptic", "architect", "minimalist"]);
const REVIEW_ROLES = Object.freeze({
  correctness: {
    directive: "Find bugs, regressions, edge cases, and behavioral contract breaks."
  },
  security: {
    directive: "Review read-only safety, secrets exposure, injection risks, and unsafe command or path handling."
  },
  tests: {
    directive: "Find missing, brittle, or overfit tests and release validation gaps."
  },
  release: {
    directive: "Review install, marketplace, versioning, documentation, and upgrade risks."
  },
  adversarial: {
    directive: "Challenge assumptions, simpler alternatives, hidden costs, and failure modes."
  },
  skeptic: {
    directive: ADVERSARIAL_LENSES.skeptic.directive
  },
  architect: {
    directive: ADVERSARIAL_LENSES.architect.directive
  },
  minimalist: {
    directive: ADVERSARIAL_LENSES.minimalist.directive
  }
});
const DEFAULT_MULTI_REVIEW_ROLES = Object.freeze([
  "correctness",
  "security",
  "tests",
  "release",
  "adversarial"
]);
const READ_ONLY_BUILTIN_TOOLS = Object.freeze([
  "Read",
  "Grep",
  "Glob"
]);
const READ_ONLY_MCP_TOOLS = Object.freeze([
  "mcp__claude-for-codex-git__git_status",
  "mcp__claude-for-codex-git__git_diff",
  "mcp__claude-for-codex-git__git_diff_cached",
  "mcp__claude-for-codex-git__git_log",
  "mcp__claude-for-codex-git__git_show",
  "mcp__claude-for-codex-git__git_blame",
  "mcp__claude-for-codex-git__git_grep",
  "mcp__claude-for-codex-git__git_ls_files"
]);

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? process.cwd(),
    env: process.env,
    encoding: "utf8",
    input: options.input,
    maxBuffer: 20 * 1024 * 1024,
    timeout: options.timeout
  });

  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message ?? result.error) : "",
    errorCode: result.error?.code ? String(result.error.code) : ""
  };
}

function git(args) {
  return run("git", args);
}

function isExecutable(filePath) {
  try {
    fs.accessSync(filePath, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function findOnPath(commandName) {
  const searchPath = process.env.PATH || "";
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

function claudeCommand() {
  const configuredPath = process.env[CLAUDE_CODE_PATH_ENV];
  if (configuredPath && isExecutable(configuredPath)) {
    return configuredPath;
  }
  const pathCommand = findOnPath("claude");
  if (pathCommand) {
    return pathCommand;
  }
  const homeFallback = path.join(os.homedir(), ".local", "bin", "claude");
  if (isExecutable(homeFallback)) {
    return homeFallback;
  }
  return "claude";
}

function runClaude(args, options = {}) {
  return run(claudeCommand(), args, options);
}

function hasBinary(name) {
  return run(name, ["--version"]).status === 0;
}

function hasClaude() {
  return runClaude(["--version"]).status === 0;
}

function claudeVersion() {
  const result = runClaude(["--version"], { timeout: 5000 });
  return {
    available: result.status === 0,
    version: result.status === 0 ? result.stdout.trim() : "",
    error: result.status === 0 ? "" : (result.stderr || result.error || "").trim()
  };
}

function commandVersion(name) {
  const result = run(name, ["--version"], { timeout: 5000 });
  return {
    available: result.status === 0,
    version: result.status === 0 ? result.stdout.trim().split(/\r?\n/)[0] : "",
    error: result.status === 0 ? "" : (result.stderr || result.error || "").trim()
  };
}

function claudeHelpText() {
  const result = runClaude(["--help"], { timeout: 5000 });
  return result.status === 0 ? result.stdout : "";
}

function flagSupport(helpText, flags) {
  return Object.fromEntries(flags.map((flag) => [flag, helpText.includes(flag)]));
}

function semanticProviderCandidates() {
  return semanticCapabilities(process.cwd(), process.env).providers;
}

function sdkAvailability() {
  const result = spawnSync(process.execPath, [
    "--input-type=module",
    "-e",
    "try { const pkg = await import('@anthropic-ai/claude-code'); console.log(pkg?.default?.version ?? 'available'); } catch (error) { process.exit(1); }"
  ], {
    cwd: process.cwd(),
    env: process.env,
    encoding: "utf8",
    timeout: 5000
  });
  return {
    available: result.status === 0,
    version: result.status === 0 ? result.stdout.trim() : "",
    error: result.status === 0 ? "" : (result.stderr || result.error?.message || "").trim()
  };
}

function buildCapabilitiesReport() {
  const helpText = claudeHelpText();
  return {
    node: process.version,
    claude: {
      command: claudeCommand(),
      ...claudeVersion(),
      flags: flagSupport(helpText, [
        "--print",
        "--permission-mode",
        "--tools",
        "--allowedTools",
        "--disallowedTools",
        "--mcp-config",
        "--strict-mcp-config",
        "--model",
        "--effort",
        "--output-format"
      ])
    },
    claudeSdk: sdkAvailability(),
    git: commandVersion("git"),
    githubCli: commandVersion("gh"),
    hooks: hookDiagnostics(),
    mcp: mcpDiagnostics(),
    semanticProviders: semanticProviderCandidates(),
    semanticContext: semanticCapabilities(process.cwd(), process.env)
  };
}

function hasHead() {
  return git(["rev-parse", "--verify", "HEAD"]).status === 0;
}

function hasBaseRef(base) {
  return git(["rev-parse", "--verify", `${base}^{commit}`]).status === 0;
}

function parseArgs(argv) {
  const tokens = normalizeArgv(argv);
  const parsed = { _: [], paths: [] };
  for (let index = 0; index < tokens.length; index += 1) {
    const arg = tokens[index];
    if (arg === "--base") {
      parsed.base = readOptionValue(tokens, index, arg);
      index += 1;
    } else if (arg === "--scope") {
      parsed.scope = readOptionValue(tokens, index, arg);
      index += 1;
    } else if (arg === "--path" || arg === "--paths") {
      const path = readOptionValue(tokens, index, arg);
      parsed.paths.push(path);
      parsed.path = path;
      index += 1;
    } else if (arg === "--model") {
      parsed.model = readOptionValue(tokens, index, arg);
      index += 1;
    } else if (arg === "--effort") {
      parsed.effort = readOptionValue(tokens, index, arg);
      index += 1;
    } else if (arg === "--roles") {
      const roles = readOptionValue(tokens, index, arg)
        .split(",")
        .map((role) => role.trim());
      if (roles.some((role) => !role)) {
        throw new Error("Missing role in --roles.");
      }
      parsed.roles = [...(parsed.roles ?? []), ...roles];
      index += 1;
    } else if (arg === "--role") {
      const role = readOptionValue(tokens, index, arg).trim();
      if (!role) {
        throw new Error("Missing value for --role.");
      }
      parsed.roles = [...(parsed.roles ?? []), role];
      index += 1;
    } else if (arg === "--adversarial-lenses") {
      const lenses = readOptionValue(tokens, index, arg)
        .split(",")
        .map((lens) => lens.trim());
      if (lenses.some((lens) => !lens)) {
        throw new Error("Missing lens in --adversarial-lenses.");
      }
      parsed.adversarialLenses = [...(parsed.adversarialLenses ?? []), ...lenses];
      index += 1;
    } else if (arg === "--adversarial-lens") {
      const lens = readOptionValue(tokens, index, arg).trim();
      if (!lens) {
        throw new Error("Missing value for --adversarial-lens.");
      }
      parsed.adversarialLenses = [...(parsed.adversarialLenses ?? []), lens];
      index += 1;
    } else if (arg === "--background") {
      parsed.background = true;
    } else if (arg === "--wait") {
      parsed.wait = true;
    } else if (arg === "--json" || arg === "--json-output") {
      parsed.jsonOutput = true;
    } else if (arg === "--write") {
      parsed.write = true;
    } else if (arg === "--parallel") {
      parsed.parallel = true;
    } else if (arg === "--sequential") {
      parsed.sequential = true;
    } else {
      const semanticIndex = parseSemanticOptions(tokens, index, parsed);
      if (semanticIndex !== index) {
        index = semanticIndex;
        continue;
      }
      parsed._.push(arg);
    }
  }
  if (parsed.parallel && parsed.sequential) {
    throw new Error("Choose either --parallel or --sequential.");
  }
  return parsed;
}

function resolveReviewRoles(args) {
  if (args.roles === undefined) {
    return [];
  }
  if (!args.roles.length) {
    throw new Error("Missing value for --roles.");
  }

  const validRoles = Object.keys(REVIEW_ROLES).sort();
  const seenRoles = new Set();
  for (const role of args.roles) {
    if (!Object.hasOwn(REVIEW_ROLES, role)) {
      throw new Error(`Unknown review role "${role}". Valid roles: ${validRoles.join(", ")}.`);
    }
    if (seenRoles.has(role)) {
      throw new Error(`Duplicate review role "${role}".`);
    }
    seenRoles.add(role);
  }
  return args.roles.map((name) => ({
    name,
    directive: REVIEW_ROLES[name].directive
  }));
}

function resolveAdversarialLenses(args) {
  const requested = args.adversarialLenses === undefined
    ? DEFAULT_ADVERSARIAL_LENSES
    : args.adversarialLenses;
  if (!requested.length) {
    throw new Error("Missing value for --adversarial-lenses.");
  }

  const validLenses = Object.keys(ADVERSARIAL_LENSES).sort();
  const seenLenses = new Set();
  for (const lens of requested) {
    if (!Object.hasOwn(ADVERSARIAL_LENSES, lens)) {
      throw new Error(`Unknown adversarial lens "${lens}". Valid lenses: ${validLenses.join(", ")}.`);
    }
    if (seenLenses.has(lens)) {
      throw new Error(`Duplicate adversarial lens "${lens}".`);
    }
    seenLenses.add(lens);
  }
  return requested.map((name) => ({
    name,
    ...ADVERSARIAL_LENSES[name]
  }));
}

function readOptionValue(tokens, index, optionName) {
  const value = tokens[index + 1];
  if (value === undefined || value === "" || value.startsWith("--")) {
    throw new Error(`Missing value for ${optionName}.`);
  }
  return value;
}

function normalizeArgv(argv) {
  if (argv.length !== 1 || !argv[0]?.trim()) {
    return argv;
  }
  return splitArgumentString(argv[0]);
}

function splitArgumentString(value) {
  const tokens = [];
  let current = "";
  let quote = null;
  let escaping = false;
  let tokenStarted = false;

  function pushToken() {
    if (tokenStarted) {
      tokens.push(current);
      current = "";
      tokenStarted = false;
    }
  }

  for (const char of value) {
    if (escaping) {
      current += char;
      escaping = false;
      tokenStarted = true;
      continue;
    }
    if (char === "\\") {
      escaping = true;
      tokenStarted = true;
      continue;
    }
    if (quote) {
      if (char === quote) {
        quote = null;
      } else {
        current += char;
      }
      tokenStarted = true;
      continue;
    }
    if (char === "\"" || char === "'") {
      quote = char;
      tokenStarted = true;
      continue;
    }
    if (/\s/.test(char)) {
      pushToken();
      continue;
    }
    current += char;
    tokenStarted = true;
  }

  if (escaping) {
    current += "\\";
  }
  if (quote) {
    throw new Error("Unmatched quote in arguments.");
  }
  pushToken();
  return tokens;
}

function formatCommandResult(label, result) {
  const output = result.stdout.trim();
  const stderr = result.stderr.trim() || result.error.trim();
  return [
    `${label}:`,
    output || "(empty)",
    result.status === 0 || !stderr ? "" : `stderr: ${stderr}`
  ].filter(Boolean).join("\n");
}

function changedFilesFromStatus(status) {
  const files = status.stdout
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.slice(3).trim())
    .filter(Boolean);

  return {
    status: status.status,
    stdout: files.join("\n"),
    stderr: status.stderr,
    error: status.error
  };
}

function safeResult(stdout) {
  return {
    status: 0,
    stdout,
    stderr: "",
    error: ""
  };
}

function collectGitContext(options) {
  const scope = options.scope ?? "auto";
  const base = options.base;
  const paths = options.paths?.length ? options.paths : options.path ? [options.path] : [];
  const pathLabel = paths.join(" ");
  const pathArgs = paths.length ? ["--", ...paths] : [];
  const headExists = hasHead();
  const baseExists = Boolean(base) && headExists && hasBaseRef(base);
  const baseEffective = !base
    ? ""
    : !headExists
      ? "unavailable (HEAD missing)"
      : baseExists
        ? base
        : "unavailable (base ref missing)";
  const baseIssue = !base
    ? ""
    : !headExists
      ? "base ignored because HEAD is unavailable"
      : !baseExists
        ? "base ignored because requested base ref is unavailable"
        : "";
  const includeWorkingTree = scope === "auto" || scope === "working-tree";
  const includeBaseBranch = (scope === "auto" || scope === "branch") && Boolean(base);
  const includeHeadNameOnly = scope === "auto" && !base;

  const status = includeWorkingTree ? git(["status", "--short", "--untracked-files=all", ...pathArgs]) : null;
  const stagedStat = includeWorkingTree ? git(["diff", "--cached", "--stat", ...pathArgs]) : null;
  const stagedDiff = includeWorkingTree ? git(["diff", "--cached", ...pathArgs]) : null;
  const unstagedStat = includeWorkingTree ? git(["diff", "--stat", ...pathArgs]) : null;
  const unstagedDiff = includeWorkingTree ? git(["diff", ...pathArgs]) : null;
  const branchStat = includeBaseBranch
    ? baseExists
      ? git(["diff", "--stat", `${base}...HEAD`, ...pathArgs])
      : safeResult(`(${baseIssue}; branch diff skipped)`)
    : null;
  const branchNameOnly = includeBaseBranch
    ? baseExists
      ? git(["diff", "--name-only", `${base}...HEAD`, ...pathArgs])
      : includeWorkingTree && status
        ? changedFilesFromStatus(status)
        : safeResult(`(${baseIssue}; branch name-only skipped)`)
    : includeHeadNameOnly && headExists
      ? git(["diff", "--name-only", "HEAD", ...pathArgs])
      : includeHeadNameOnly && status
        ? changedFilesFromStatus(status)
        : null;

  return [
    "<git_context>",
    `cwd: ${process.cwd()}`,
    `scope: ${scope}`,
    `base requested: ${base ?? ""}`,
    `base effective: ${baseEffective}`,
    baseIssue,
    `paths: ${pathLabel}`,
    "",
    status
      ? formatCommandResult(`git status --short --untracked-files=all${paths.length ? ` -- ${pathLabel}` : ""}`, status)
      : "",
    "",
    stagedStat
      ? formatCommandResult(`git diff --cached --stat${paths.length ? ` -- ${pathLabel}` : ""}`, stagedStat)
      : "",
    "",
    stagedDiff
      ? formatCommandResult(`git diff --cached${paths.length ? ` -- ${pathLabel}` : ""}`, stagedDiff)
      : "",
    "",
    unstagedStat
      ? formatCommandResult(`git diff --stat${paths.length ? ` -- ${pathLabel}` : ""}`, unstagedStat)
      : "",
    "",
    unstagedDiff
      ? formatCommandResult(`git diff${paths.length ? ` -- ${pathLabel}` : ""}`, unstagedDiff)
      : "",
    "",
    branchStat
      ? formatCommandResult(
          baseExists
            ? `git diff --stat ${base}...HEAD${paths.length ? ` -- ${pathLabel}` : ""}`
            : "branch diff skipped",
          branchStat
        )
      : scope === "auto"
        ? "branch diff:\n(empty)"
        : "",
    "",
    branchNameOnly
      ? base
      ? baseExists
        ? formatCommandResult(`git diff --name-only ${base}...HEAD${paths.length ? ` -- ${pathLabel}` : ""}`, branchNameOnly)
        : formatCommandResult("changed files from git status fallback", branchNameOnly)
      : includeHeadNameOnly && headExists
        ? formatCommandResult(`git diff --name-only HEAD${paths.length ? ` -- ${pathLabel}` : ""}`, branchNameOnly)
        : formatCommandResult("changed files from git status fallback", branchNameOnly)
      : "",
    "</git_context>"
  ].filter((line) => line !== "").join("\n");
}

function buildClaudePrintInvocation(prompt, options) {
  const args = [
    "--print",
    "--permission-mode",
    options.write ? "bypassPermissions" : "dontAsk",
  ];
  let mcpConfig = null;

  if (!options.write) {
    mcpConfig = createGitMcpConfig(process.cwd(), process.env);
    args.push(
      "--tools",
      READ_ONLY_BUILTIN_TOOLS.join(","),
      "--allowedTools",
      READ_ONLY_MCP_TOOLS.join(","),
      "--disallowedTools",
      "Edit,Write,MultiEdit,Bash",
      "--mcp-config",
      mcpConfig.configPath,
      "--strict-mcp-config"
    );
  }

  args.push(
    "--output-format",
    "text"
  );

  if (options.model) {
    args.push("--model", options.model);
  }
  if (options.effort) {
    args.push("--effort", options.effort);
  }

  args.push(prompt);
  return { args, mcpConfig };
}

function claudePrint(prompt, options) {
  const { args, mcpConfig } = buildClaudePrintInvocation(prompt, options);
  try {
    return runClaude(args, { timeout: options.timeout });
  } finally {
    if (mcpConfig && process.env.CLAUDE_FOR_CODEX_KEEP_MCP_CONFIG !== "1") {
      mcpConfig.cleanup();
    }
  }
}

function runClaudeAsync(args, options = {}) {
  return new Promise((resolve) => {
    const child = spawn(claudeCommand(), args, {
      cwd: options.cwd ?? process.cwd(),
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"]
    });
    const stdoutChunks = [];
    const stderrChunks = [];
    let settled = false;
    const timeout = options.timeout
      ? setTimeout(() => {
          try {
            child.kill("SIGTERM");
          } catch {
            // Process may already have exited.
          }
        }, options.timeout)
      : null;

    child.stdout?.on("data", (chunk) => stdoutChunks.push(Buffer.from(chunk)));
    child.stderr?.on("data", (chunk) => stderrChunks.push(Buffer.from(chunk)));
    child.once("error", (error) => {
      if (settled) {
        return;
      }
      settled = true;
      if (timeout) {
        clearTimeout(timeout);
      }
      resolve({
        status: 1,
        stdout: Buffer.concat(stdoutChunks).toString("utf8"),
        stderr: Buffer.concat(stderrChunks).toString("utf8"),
        error: error.message || String(error),
        errorCode: error.code ? String(error.code) : ""
      });
    });
    child.once("close", (status, signal) => {
      if (settled) {
        return;
      }
      settled = true;
      if (timeout) {
        clearTimeout(timeout);
      }
      resolve({
        status: status ?? (signal ? 1 : 0),
        stdout: Buffer.concat(stdoutChunks).toString("utf8"),
        stderr: Buffer.concat(stderrChunks).toString("utf8"),
        error: "",
        errorCode: ""
      });
    });
  });
}

async function claudePrintAsync(prompt, options) {
  const { args, mcpConfig } = buildClaudePrintInvocation(prompt, options);
  try {
    return await runClaudeAsync(args, { timeout: options.timeout });
  } finally {
    if (mcpConfig && process.env.CLAUDE_FOR_CODEX_KEEP_MCP_CONFIG !== "1") {
      mcpConfig.cleanup();
    }
  }
}

function stripBackgroundArgs(rawArgs) {
  return normalizeArgv(rawArgs).filter((arg) => arg !== "--background" && arg !== "--wait");
}

function parseJobIdArg(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "--job-id") {
      const value = tokens[index + 1];
      if (!value || value.startsWith("--")) {
        throw new Error("Missing --job-id value.");
      }
      return value;
    }
  }
  const positional = tokens.find((token) => token !== "--json" && token !== "--json-output");
  if (!positional) {
    throw new Error("Missing --job-id value.");
  }
  return positional;
}

function terminalJobStatus(status) {
  return ["succeeded", "failed", "cancelled", "cancel_failed"].includes(status);
}

function startBackgroundJob(command, rawArgs) {
  if (!BACKGROUND_CAPABLE_COMMANDS.has(command)) {
    console.error(`${command} does not support --background.`);
    process.exit(2);
  }
  const cwd = process.cwd();
  const foregroundArgs = stripBackgroundArgs(rawArgs);
  const session = readJson(currentSessionFileForCwd(cwd), {});
  const job = createJob(cwd, {
    command,
    args: foregroundArgs,
    cwd,
    sessionId: session?.sessionId || ""
  });
  const child = spawn(process.execPath, [process.argv[1], "__run-job", job.id], {
    cwd,
    env: process.env,
    detached: true,
    stdio: "ignore"
  });
  child.unref();
  updateJob(cwd, job.id, {
    workerPid: child.pid
  });
  return readJob(cwd, job.id) ?? job;
}

function waitForJob(jobId) {
  const started = Date.now();
  const timeoutMs = 30 * 60 * 1000;
  while (Date.now() - started < timeoutMs) {
    const job = readJob(process.cwd(), jobId);
    if (job && terminalJobStatus(job.status)) {
      return job;
    }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 100);
  }
  return readJob(process.cwd(), jobId) ?? { id: jobId, status: "unknown" };
}

function maybeStartBackground(command, rawArgs) {
  let parsed;
  try {
    parsed = parseArgs(rawArgs);
  } catch {
    return false;
  }
  if (!parsed.background) {
    return false;
  }
  const job = startBackgroundJob(command, rawArgs);
  if (parsed.wait) {
    const completed = waitForJob(job.id);
    process.stdout.write(`${JSON.stringify({ status: completed.status, job: completed }, null, 2)}\n`);
    process.exit(completed.status === "succeeded" ? 0 : 1);
  }
  process.stdout.write(`${JSON.stringify({ status: "started", job }, null, 2)}\n`);
  process.exit(0);
}

function handleReserveJob(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const command = tokens[0];
  if (!command) {
    throw new Error("Missing command to reserve.");
  }
  if (!BACKGROUND_CAPABLE_COMMANDS.has(command)) {
    throw new Error(`Command "${command}" cannot be reserved as a background job.`);
  }
  const commandArgs = stripBackgroundArgs(tokens.slice(1));
  const workerCommand = [
    process.argv0 || process.execPath,
    process.argv[1],
    "run-reserved-job"
  ];
  const job = reserveJob(process.cwd(), {
    command,
    args: commandArgs,
    cwd: process.cwd(),
    focus: commandArgs.join(" ")
  }, workerCommand);
  workerCommand.push("--job-id", job.id);
  const updated = updateJob(process.cwd(), job.id, { workerCommand }) ?? job;

  return {
    status: "reserved",
    job: {
      id: updated.id,
      status: updated.status,
      command: updated.command,
      args: updated.args ?? []
    },
    workerCommand,
    forwardingInstructions: "Dispatch exactly one forwarding subagent. The child must run workerCommand once, must not inspect or reinterpret the repository, and must return only the command result."
  };
}

function hasReviewableGitChanges(cwd = process.cwd()) {
  if (run("git", ["rev-parse", "--is-inside-work-tree"], { cwd }).status !== 0) {
    return { reviewable: false, reason: "not a git repository" };
  }
  const status = run("git", ["status", "--short", "--untracked-files=all"], { cwd });
  if (status.status !== 0) {
    return { reviewable: false, reason: "git status failed" };
  }
  return {
    reviewable: Boolean(status.stdout.trim()),
    reason: status.stdout.trim() ? "git working tree has changes" : "no git changes"
  };
}

function workingTreeFingerprint(cwd = process.cwd()) {
  const parts = [
    run("git", ["status", "--short", "--untracked-files=all"], { cwd }).stdout,
    run("git", ["diff", "--cached"], { cwd }).stdout,
    run("git", ["diff"], { cwd }).stdout
  ];
  return createHash("sha256").update(parts.join("\n--- claude-for-codex ---\n")).digest("hex");
}

function adversarialLensSection(lenses) {
  return [
    "<adversarial_lenses>",
    ...lenses.map((lens) => [
      `<lens name="${lens.name}" label="${lens.label}">`,
      lens.directive,
      "</lens>"
    ].join("\n")),
    "</adversarial_lenses>"
  ].join("\n");
}

function adversarialVerdictContract() {
  return [
    "<output_contract>",
    "## Intent",
    "State what the author is trying to achieve. Challenge whether the work achieves that intent well, not whether the intent itself is desirable.",
    "",
    "## Verdict: PASS | CONTESTED | REJECT",
    "Use PASS when there are no high-severity findings.",
    "Use CONTESTED when high-severity findings exist but the evidence or lens agreement is mixed.",
    "Use REJECT when high-severity findings have strong evidence or consensus across lenses.",
    "",
    "## Findings",
    "Number findings by severity from high to low. For each finding include:",
    "- **[severity]** file:line - description, evidence, and impact",
    "- Lens: skeptic | architect | minimalist",
    "- Principle: mapped principle name",
    "- Recommendation: concrete action",
    "",
    "## What Went Well",
    "List one to three things that appear sound, or say none observed.",
    "",
    "## Lead Judgment",
    "For each finding, state accept or reject with a one-line rationale. Reject false positives, overreach, and style-only objections.",
    "</output_contract>"
  ].join("\n");
}

function adversarialJsonContract() {
  return [
    "<output_contract>",
    "Return exactly one JSON object and no Markdown.",
    "Schema:",
    "{",
    '  "verdict": "PASS | CONTESTED | REJECT",',
    '  "summary": "short intent-aware judgment",',
    '  "findings": [',
    '    {"severity": "high|medium|low", "file": "path", "line": 1, "description": "issue", "evidence": "changed-file evidence", "recommendation": "action"}',
    "  ],",
    '  "next_steps": ["concrete next step"]',
    "}",
    "Use an empty findings array when there are no findings.",
    "</output_contract>"
  ].join("\n");
}

function reviewMarkdownContract() {
  return [
    "<output_contract>",
    "## Findings",
    "- [Severity] file:line - issue, evidence, impact, suggested direction",
    "## Open Questions",
    "## Residual Risk",
    "</output_contract>"
  ].join("\n");
}

function reviewJsonContract() {
  return [
    "<output_contract>",
    "Return exactly one JSON object and no Markdown.",
    "Schema:",
    "{",
    '  "verdict": "approve | needs-attention",',
    '  "summary": "short review judgment",',
    '  "findings": [',
    '    {"severity": "critical|high|medium|low", "title": "issue title", "body": "issue, evidence, and impact", "file": "path", "line_start": 1, "line_end": 1, "confidence": 0.8, "recommendation": "concrete action"}',
    "  ],",
    '  "next_steps": ["concrete next step"]',
    "}",
    "Use verdict approve only when there are no material findings.",
    "Use an empty findings array when there are no findings.",
    "</output_contract>"
  ].join("\n");
}

function semanticPromptBlock(args) {
  return args.semantic?.promptBlock ? `\n${args.semantic.promptBlock}` : "";
}

function reviewPrompt(kind, args) {
  const focus = args._.join(" ").trim();
  const gitContext = collectGitContext(args);
  const adversarial = kind === "adversarial-review";
  const reviewRoles = args.reviewRoles?.length
    ? args.reviewRoles.map((role) => role.name).join(", ")
    : "";

  if (adversarial) {
    const adversarialLenses = args.resolvedAdversarialLenses ?? resolveAdversarialLenses(args);
    return renderPromptTemplate(pluginRoot(), "adversarial-review", {
      GIT_CONTEXT: gitContext,
      SEMANTIC_CONTEXT_BLOCK: semanticPromptBlock(args),
      ADVERSARIAL_LENSES: adversarialLensSection(adversarialLenses),
      FOCUS_BLOCK: focus ? `<focus>${focus}</focus>` : "",
      OUTPUT_CONTRACT: args.jsonOutput ? adversarialJsonContract() : adversarialVerdictContract()
    });
  }

  return renderPromptTemplate(pluginRoot(), "review", {
    GIT_CONTEXT: gitContext,
    SEMANTIC_CONTEXT_BLOCK: semanticPromptBlock(args),
    REVIEW_ROLES_BLOCK: reviewRoles ? `<review_roles>${reviewRoles}</review_roles>` : "",
    FOCUS_BLOCK: focus ? `<focus>${focus}</focus>` : "",
    OUTPUT_CONTRACT: args.jsonOutput ? reviewJsonContract() : reviewMarkdownContract()
  });
}

function multiReviewRolePrompt(role, args, gitContext) {
  const focus = args._.join(" ").trim();

  return renderPromptTemplate(pluginRoot(), "multi-review-role", {
    ROLE_NAME: role.name,
    ROLE_DIRECTIVE: role.directive,
    GIT_CONTEXT: gitContext,
    SEMANTIC_CONTEXT_BLOCK: semanticPromptBlock(args),
    FOCUS_BLOCK: focus ? `<focus>${focus}</focus>` : "",
    OUTPUT_CONTRACT: args.jsonOutput ? reviewJsonContract() : reviewMarkdownContract()
  });
}

function reviewGateRolePrompt(role, args, gitContext) {
  return renderPromptTemplate(pluginRoot(), "review-gate-role", {
    ROLE_NAME: role.name,
    ROLE_DIRECTIVE: role.directive,
    GIT_CONTEXT: gitContext,
    SEMANTIC_CONTEXT_BLOCK: semanticPromptBlock(args)
  });
}

function planPrompt(args) {
  const focus = args._.join(" ").trim();
  const gitContext = collectGitContext(args);

  return renderPromptTemplate(pluginRoot(), "plan", {
    GIT_CONTEXT: gitContext,
    PLANNING_REQUEST_BLOCK: focus ? `<planning_request>${focus}</planning_request>` : ""
  });
}

function rescuePrompt(args) {
  const focus = args._.join(" ").trim();
  const gitContext = collectGitContext(args);

  return renderPromptTemplate(pluginRoot(), "rescue", {
    GIT_CONTEXT: gitContext,
    EDIT_RULE: args.write ? "- You may edit files because the user explicitly requested rescue --write." : "- Do not edit files.",
    REPORT_RULE: args.write ? "- Keep changes narrowly scoped and report every modified file." : "- Do not suggest that you are currently applying fixes.",
    RESCUE_REQUEST_BLOCK: focus ? `<rescue_request>${focus}</rescue_request>` : ""
  });
}

function setupOptions(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const options = { enable: false, disable: false, mode: undefined };
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "--enable-review-gate") {
      options.enable = true;
    } else if (token === "--disable-review-gate") {
      options.disable = true;
    } else if (token === "--review-gate-mode") {
      options.mode = readOptionValue(tokens, index, token);
      index += 1;
    } else {
      throw new Error(`Unknown setup option "${token}".`);
    }
  }
  if (options.enable && options.disable) {
    throw new Error("Choose either --enable-review-gate or --disable-review-gate.");
  }
  if (options.mode !== undefined && !VALID_REVIEW_GATE_MODES.has(options.mode)) {
    throw new Error(`Invalid --review-gate-mode "${options.mode}". Valid modes: multi-role.`);
  }
  return options;
}

function pluginRoot() {
  return path.dirname(path.dirname(fileURLToPath(import.meta.url)));
}

function hookEventsFromManifest(manifest) {
  const hooks = manifest && typeof manifest === "object" && manifest.hooks && typeof manifest.hooks === "object"
    ? manifest.hooks
    : manifest;
  if (!hooks || typeof hooks !== "object" || Array.isArray(hooks)) {
    return [];
  }
  return Object.keys(hooks);
}

function hookTrustedInCodexConfig(configText) {
  if (!configText) {
    return false;
  }
  let currentTrustedHookTable = false;
  return configText.split(/\r?\n/).some((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      return false;
    }
    const tableMatch = trimmed.match(/^\[(.+)\]$/);
    if (tableMatch) {
      currentTrustedHookTable = hookTrustKeyMatches(tableMatch[1]);
      return false;
    }
    if (!trimmed.includes("=")) {
      return false;
    }
    const [rawKey, ...valueParts] = trimmed.split("=");
    const key = rawKey.trim().replace(/^["']|["']$/g, "");
    const value = valueParts.join("=").trim().replace(/^["']|["']$/g, "");
    if (currentTrustedHookTable && key === "trusted_hash" && value.startsWith("sha256:")) {
      return true;
    }
    return hookTrustKeyMatches(key) && value === "trusted";
  });
}

function hookTrustKeyMatches(key) {
  return key.includes("claude-for-codex")
    && key.includes("hooks/hooks.json")
    && key.includes(":stop:");
}

function hookDiagnostics() {
  const root = pluginRoot();
  const manifestPath = path.join(root, "hooks", "hooks.json");
  const manifestExists = fs.existsSync(manifestPath);
  let events = [];
  let manifestError = "";
  if (manifestExists) {
    try {
      const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
      events = hookEventsFromManifest(manifest);
    } catch (error) {
      manifestError = error?.message ? String(error.message) : String(error);
    }
  }

  const configPath = path.join(os.homedir(), ".codex", "config.toml");
  const codexConfigChecked = fs.existsSync(configPath);
  let configText = "";
  let codexConfigError = "";
  if (codexConfigChecked) {
    try {
      configText = fs.readFileSync(configPath, "utf8");
    } catch (error) {
      codexConfigError = error?.message ? String(error.message) : String(error);
    }
  }

  return {
    manifest: "hooks/hooks.json",
    manifestPath,
    manifestExists,
    manifestError,
    events,
    codexConfigPath: configPath,
    codexConfigChecked,
    codexConfigError,
    trustedInCodexConfig: hookTrustedInCodexConfig(configText)
  };
}

function mcpDiagnostics() {
  const gitServerPath = path.join(pluginRoot(), "scripts", "lib", "mcp-git.mjs");
  return {
    gitServerPath,
    gitServerExists: fs.existsSync(gitServerPath),
    strictConfigSupported: true,
    claudeFlags: ["--mcp-config", "--strict-mcp-config", "--allowedTools"]
  };
}

function buildSetupReport(actionsTaken = []) {
  const cwd = process.cwd();
  const stateReport = readStateReport(cwd);
  const config = stateReport.state.config;
  return {
    node: process.version,
    claudeAvailable: hasClaude(),
    claudeCommand: claudeCommand(),
    gitAvailable: hasBinary("git"),
    cwd,
    reviewGate: {
      enabled: Boolean(config.reviewGateEnabled),
      mode: config.reviewGateMode,
      stateFile: stateFileForCwd(cwd),
      stateReadable: stateReport.readable,
      stateError: stateReport.error,
      bypassEnv: REVIEW_GATE_ENV
    },
    jobCommands: ["jobs", "result", "cancel", "rescue"],
    hooks: hookDiagnostics(),
    mcp: mcpDiagnostics(),
    capabilities: buildCapabilitiesReport(),
    actionsTaken
  };
}

function printSetup(rawArgs) {
  let options;
  try {
    options = setupOptions(rawArgs);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  const actionsTaken = [];
  const cwd = process.cwd();
  if (options.mode) {
    setConfig(cwd, "reviewGateMode", options.mode);
    actionsTaken.push(`Set review gate mode to ${options.mode}.`);
  }
  if (options.enable) {
    setConfig(cwd, "reviewGateEnabled", true);
    actionsTaken.push(`Enabled Claude review gate for ${canonicalWorkspaceRoot(cwd)}.`);
  } else if (options.disable) {
    setConfig(cwd, "reviewGateEnabled", false);
    actionsTaken.push(`Disabled Claude review gate for ${canonicalWorkspaceRoot(cwd)}.`);
  }
  const report = {
    ...buildSetupReport(actionsTaken)
  };

  console.log(JSON.stringify(report, null, 2));
  process.exit(report.claudeAvailable && report.gitAvailable && report.reviewGate.stateReadable ? 0 : 1);
}

function printStatus() {
  const result = runClaude(["agents", "--json", "--cwd", process.cwd()]);
  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.error || "claude agents --json failed\n");
    process.exit(result.status);
  }
  let agents = result.stdout;
  try {
    agents = JSON.parse(result.stdout);
  } catch {
    // Keep raw output if Claude changes the shape.
  }
  process.stdout.write(`${JSON.stringify({
    claudeAgents: agents,
    reviewGate: buildSetupReport().reviewGate
  }, null, 2)}\n`);
}

function printCapabilities() {
  process.stdout.write(`${JSON.stringify(buildCapabilitiesReport(), null, 2)}\n`);
}

function recordCommandReport(command, args, result, startedAt, parsed, roleResults = []) {
  const endedAt = new Date().toISOString();
  const report = reportFromResult({
    command,
    args,
    result,
    startedAt,
    endedAt,
    parsed,
    roleResults
  });
  safeWriteReport(process.cwd(), report);
  return endedAt;
}

function attachSemanticContext(args, options = {}) {
  if (options.allowed === false && args.semanticContext && args.semanticContext !== "off") {
    throw new Error(`--semantic-context is not supported for ${options.command}.`);
  }
  validateSemanticArgs(args, { allowAuto: !options.reviewGate });
  args.semantic = buildSemanticContext(args, {
    cwd: process.cwd(),
    reviewGate: Boolean(options.reviewGate)
  });
  if (args.semantic?.status === "unavailable" && args.semantic.warning) {
    process.stderr.write(`[claude-for-codex semantic] unavailable: ${args.semantic.report.semanticFailureReason}\n`);
  }
  return args.semantic;
}

function printReport(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const latest = tokens.includes("--latest") || tokens.length === 0;
  if (tokens.some((token) => !["--latest", "--json"].includes(token))) {
    console.error("Usage: claude-companion.mjs report [--latest] [--json]");
    process.exit(2);
  }
  const payload = latest ? latestReport(process.cwd()) : listReports(process.cwd());
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
}

function runReleaseCheckCommand(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const options = {
    remoteInstall: false,
    requireRemoteInstall: false,
    timeoutMs: 30000
  };
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "--remote-install") {
      options.remoteInstall = true;
    } else if (token === "--require-remote-install") {
      options.remoteInstall = true;
      options.requireRemoteInstall = true;
    } else if (token === "--timeout-ms") {
      options.timeoutMs = Number(readOptionValue(tokens, index, token));
      index += 1;
      if (!Number.isFinite(options.timeoutMs) || options.timeoutMs <= 0) {
        console.error("--timeout-ms must be a positive number.");
        process.exit(2);
      }
    } else if (token === "--json") {
      // JSON is the only output format for now.
    } else {
      console.error(`Unknown release-check option "${token}".`);
      process.exit(2);
    }
  }
  const payload = runReleaseCheck(path.resolve(pluginRoot(), "..", ".."), options);
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(payload.ok ? 0 : 1);
}

function parseGateVerdict(rawOutput) {
  const text = String(rawOutput ?? "").trim();
  if (!text) {
    return { kind: "invalid", reason: "empty Claude gate output" };
  }
  const firstLine = text.split(/\r?\n/, 1)[0].trim();
  if (firstLine.startsWith("ALLOW:")) {
    return { kind: "allow", reason: firstLine.slice("ALLOW:".length).trim() || "allowed" };
  }
  if (firstLine.startsWith("BLOCK:")) {
    return { kind: "block", reason: firstLine.slice("BLOCK:".length).trim() || "blocked", output: text };
  }
  return { kind: "invalid", reason: `unexpected first line: ${firstLine}` };
}

function warnGate(message) {
  process.stderr.write(`[claude-for-codex review-gate] ${message}\n`);
}

function readStdinJsonForGate() {
  if (process.stdin.isTTY) {
    return {};
  }
  const rawInput = fs.readFileSync(0, "utf8").trim();
  if (!rawInput) {
    return {};
  }
  return JSON.parse(rawInput);
}

function runReviewGate(rawArgs) {
  const startedAt = new Date().toISOString();
  if (String(process.env[REVIEW_GATE_ENV] ?? "").toLowerCase() === "off") {
    return;
  }

  let args;
  try {
    args = parseArgs(rawArgs);
  } catch (error) {
    warnGate(`argument parse failed; allowing stop: ${error.message || String(error)}`);
    return;
  }

  let input = {};
  try {
    input = readStdinJsonForGate();
  } catch (error) {
    warnGate(`invalid hook input; allowing stop: ${error.message || String(error)}`);
    return;
  }

  if (input.stop_hook_active) {
    return;
  }

  const cwd = input.cwd || process.cwd();
  try {
    process.chdir(cwd);
  } catch (error) {
    warnGate(`unable to enter hook cwd "${cwd}"; allowing stop: ${error.message || String(error)}`);
    return;
  }
  let config;
  try {
    config = getConfig(cwd);
  } catch (error) {
    if (error instanceof StateReadError) {
      warnGate(`state unreadable; allowing stop: ${error.message}`);
      return;
    }
    throw error;
  }
  if (!config.reviewGateEnabled) {
    return;
  }
  if (config.reviewGateMode !== "multi-role") {
    warnGate(`unknown gate mode "${config.reviewGateMode}"; allowing stop`);
    return;
  }

  const reviewable = hasReviewableGitChanges(cwd);
  if (!reviewable.reviewable) {
    return;
  }
  const diffHash = workingTreeFingerprint(cwd);
  const baseline = readJson(turnBaselineFileForCwd(cwd), null);
  if (baseline?.workingTreeFingerprint === diffHash) {
    return;
  }
  if (config.lastAllowedReviewGateDiffHash === diffHash) {
    return;
  }

  const roles = DEFAULT_MULTI_REVIEW_ROLES.map((name) => ({
    name,
    directive: REVIEW_ROLES[name].directive
  }));
  args.scope = "working-tree";
  args.timeout = REVIEW_GATE_ROLE_TIMEOUT_MS;
  try {
    attachSemanticContext(args, { reviewGate: true, command: "review-gate" });
  } catch (error) {
    warnGate(`semantic context validation failed; allowing degraded gate: ${error.message || String(error)}`);
    args.semantic = {
      enabled: true,
      status: "unavailable",
      provider: "",
      promptBlock: [
        '<semantic_context provider="" status="unavailable" reason="validation_error">',
        "Semantic context was requested but unavailable. Treat this review as degraded.",
        "</semantic_context>"
      ].join("\n"),
      report: {
        semanticProvider: "",
        semanticStatus: "unavailable",
        semanticBytes: 0,
        semanticDurationMs: 0,
        semanticFailureReason: "validation_error",
        semanticFailed: true
      }
    };
  }
  if (args.semantic?.report?.semanticFailed) {
    warnGate(`semantic context unavailable (${args.semantic.report.semanticFailureReason}); running degraded gate`);
  }

  const gitContext = collectGitContext(args);
  const blocks = [];
  for (const role of roles) {
    const prompt = reviewGateRolePrompt(role, args, gitContext);
    const result = claudePrint(prompt, args);
    if (result.errorCode === "ETIMEDOUT" || result.error.includes("ETIMEDOUT")) {
      warnGate(`role ${role.name} timed out; allowing stop`);
      continue;
    }
    if (result.status !== 0) {
      const detail = (result.stderr || result.error || result.stdout || "claude --print failed").trim();
      warnGate(`role ${role.name} failed; allowing stop: ${detail}`);
      continue;
    }
    const verdict = parseGateVerdict(result.stdout);
    if (verdict.kind === "block") {
      blocks.push({ role: role.name, reason: verdict.reason, output: verdict.output });
    } else if (verdict.kind === "invalid") {
      warnGate(`role ${role.name} returned invalid gate output; allowing stop: ${verdict.reason}`);
    }
  }

  if (blocks.length) {
    const reason = blocks
      .map((block) => `${block.role}: ${block.reason}`)
      .join("; ");
    process.stdout.write(`${JSON.stringify({
      decision: "block",
      reason: `Claude review gate found blocking issues: ${reason}`
    })}\n`);
    return;
  }
  if (args.semantic?.report?.semanticFailed) {
    args.semanticVerdict = "DEGRADED_PASS";
  }
  recordCommandReport("review-gate", args, {
    status: 0,
    stdout: "",
    stderr: "",
    error: "",
    errorCode: ""
  }, startedAt);
  setConfig(cwd, "lastAllowedReviewGateDiffHash", diffHash);
}

function parseStructuredReviewResult(stdout, options = {}) {
  return normalizeReviewOutput(extractJsonObject(stdout), options);
}

function renderRoleReviewSections(title, results, roleLabel = "Role") {
  const succeeded = results.filter(({ result }) => result.status === 0).map(({ role }) => role.name);
  const failed = results.filter(({ result }) => result.status !== 0).map(({ role }) => role.name);
  const noun = roleLabel.toLowerCase();
  const plural = noun === "lens" ? "lenses" : `${noun}s`;
  const sections = [
    title,
    ...results.map(({ role, result }) => [
      `## ${roleLabel}: ${role.name}`,
      result.stdout.trim() || "(no stdout)",
      result.status === 0 ? "" : [
        "",
        `${roleLabel} failed with exit status ${result.status}.`,
        result.stderr || result.error ? `stderr: ${(result.stderr || result.error).trim()}` : ""
      ].filter(Boolean).join("\n")
    ].filter(Boolean).join("\n")),
    "## Orchestration Summary",
    `execution mode: parallel`,
    `${plural} requested: ${results.map(({ role }) => role.name).join(", ")}`,
    `${plural} succeeded: ${succeeded.length ? succeeded.join(", ") : "(none)"}`,
    `${plural} failed: ${failed.length ? failed.join(", ") : "(none)"}`,
    `exit policy: exits non-zero if any ${noun} fails; completed ${noun} output remains visible.`
  ];
  return { text: `${sections.join("\n\n")}\n`, failed };
}

async function runParallelRoleReviews(roles, args, gitContext, promptBuilder) {
  const pending = roles.map(async (role) => ({
    role,
    result: await claudePrintAsync(promptBuilder(role, args, gitContext), args)
  }));
  return Promise.all(pending);
}

function renderStructuredRoleReview(results) {
  const parsedResults = [];
  const failures = [];
  for (const { role, result } of results) {
    if (result.status !== 0) {
      failures.push(`${role.name}: Claude exited ${result.status}: ${(result.stderr || result.error || "").trim()}`);
      continue;
    }
    try {
      parsedResults.push({
        role,
        result: parseStructuredReviewResult(result.stdout, { role: role.name })
      });
    } catch (error) {
      failures.push(`${role.name}: ${error.message || String(error)}; raw output: ${result.stdout.trim()}`);
    }
  }
  if (failures.length) {
    return {
      ok: false,
      text: `Invalid structured multi-review output:\n${failures.map((failure) => `- ${failure}`).join("\n")}\n`
    };
  }
  return {
    ok: true,
    text: `${JSON.stringify(aggregateRoleReviewOutputs(parsedResults), null, 2)}\n`
  };
}

async function runClaudeTask(kind, rawArgs) {
  const startedAt = new Date().toISOString();
  if (maybeStartBackground(kind, rawArgs)) {
    return;
  }
  let args;
  try {
    args = parseArgs(rawArgs);
    if (kind === "adversarial-review" && args.roles !== undefined) {
      throw new Error("--roles is only valid for multi-review; use --adversarial-lenses for adversarial-review.");
    }
    if (kind === "adversarial-review") {
      args.resolvedAdversarialLenses = resolveAdversarialLenses(args);
    }
    if (kind === "adversarial-review" && args.parallel && args.jsonOutput) {
      throw new Error("adversarial-review --parallel does not support --json; use sequential --json for a single structured verdict.");
    }
    if (args.roles !== undefined) {
      args.reviewRoles = resolveReviewRoles(args);
    }
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  const scope = args.scope ?? "auto";
  if (!VALID_SCOPES.has(scope)) {
    console.error(`Invalid --scope "${scope}". Valid scopes: auto, working-tree, branch.`);
    process.exit(2);
  }
  if (scope === "branch" && !args.base) {
    console.error("--scope branch requires --base <ref>.");
    process.exit(2);
  }
  args.scope = scope;
  try {
    attachSemanticContext(args, { allowed: ["review", "adversarial-review"].includes(kind), command: kind });
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  if (kind === "adversarial-review" && args.parallel) {
    const gitContext = collectGitContext(args);
    const roles = args.resolvedAdversarialLenses.map((lens) => ({
      name: lens.name,
      directive: lens.directive
    }));
    const results = await runParallelRoleReviews(roles, args, gitContext, multiReviewRolePrompt);
    const rendered = renderRoleReviewSections("# Claude Parallel Adversarial Review", results, "Lens");
    const aggregateResult = {
      status: rendered.failed.length ? 1 : 0,
      stdout: rendered.text,
      stderr: "",
      error: "",
      errorCode: ""
    };
    recordCommandReport(kind, args, aggregateResult, startedAt, undefined, results);
    process.stdout.write(rendered.text);
    process.exit(rendered.failed.length ? 1 : 0);
  }
  const prompt = kind === "plan" ? planPrompt(args) : kind === "rescue" ? rescuePrompt(args) : reviewPrompt(kind, args);
  let rescueBefore = null;
  if (kind === "rescue" && args.write) {
    if (run("git", ["rev-parse", "--is-inside-work-tree"]).status !== 0) {
      console.error("rescue --write requires a git repository.");
      process.exit(2);
    }
    rescueBefore = workingTreeFingerprint(process.cwd());
  }
  const result = claudePrint(prompt, args);

  if (result.status !== 0) {
    recordCommandReport(kind, args, result, startedAt);
    process.stderr.write(result.stderr || result.error || "claude --print failed\n");
    process.exit(result.status);
  }
  if (kind === "adversarial-review" && args.jsonOutput) {
    try {
      const parsed = normalizeAdversarialOutput(validateAdversarialJson(extractJsonObject(result.stdout)));
      recordCommandReport(kind, args, result, startedAt, parsed);
      process.stdout.write(`${JSON.stringify(parsed, null, 2)}\n`);
    } catch (error) {
      recordCommandReport(kind, args, { ...result, status: 1 }, startedAt);
      process.stderr.write(`Invalid structured adversarial output: ${error.message || String(error)}\n`);
      process.stdout.write(result.stdout);
      process.exit(1);
    }
    return;
  }
  if (kind === "review" && args.jsonOutput) {
    try {
      const parsed = parseStructuredReviewResult(result.stdout);
      recordCommandReport(kind, args, result, startedAt, parsed);
      process.stdout.write(`${JSON.stringify(parsed, null, 2)}\n`);
    } catch (error) {
      recordCommandReport(kind, args, { ...result, status: 1 }, startedAt);
      process.stderr.write(`Invalid structured review output: ${error.message || String(error)}\n`);
      process.stdout.write(result.stdout);
      process.exit(1);
    }
    return;
  }
  if (kind === "rescue" && args.write) {
    const rescueAfter = workingTreeFingerprint(process.cwd());
    process.stderr.write(`[claude-for-codex rescue] write-mode fingerprint ${rescueBefore} -> ${rescueAfter}\n`);
  }
  recordCommandReport(kind, args, result, startedAt);
  process.stdout.write(result.stdout);
}

async function runClaudeMultiReview(rawArgs) {
  const startedAt = new Date().toISOString();
  if (maybeStartBackground("multi-review", rawArgs)) {
    return;
  }
  let args;
  try {
    args = parseArgs(rawArgs);
    args.reviewRoles = args.roles === undefined
      ? DEFAULT_MULTI_REVIEW_ROLES.map((name) => ({
          name,
          directive: REVIEW_ROLES[name].directive
        }))
      : resolveReviewRoles(args);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  const scope = args.scope ?? "auto";
  if (!VALID_SCOPES.has(scope)) {
    console.error(`Invalid --scope "${scope}". Valid scopes: auto, working-tree, branch.`);
    process.exit(2);
  }
  if (scope === "branch" && !args.base) {
    console.error("--scope branch requires --base <ref>.");
    process.exit(2);
  }
  args.scope = scope;
  try {
    attachSemanticContext(args, { command: "multi-review" });
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }

  const gitContext = collectGitContext(args);
  const runInParallel = !args.sequential;
  let results = [];
  if (runInParallel) {
    results = await runParallelRoleReviews(args.reviewRoles, args, gitContext, multiReviewRolePrompt);
  } else {
    for (const role of args.reviewRoles) {
      const prompt = multiReviewRolePrompt(role, args, gitContext);
      const result = claudePrint(prompt, args);
      results.push({ role, result });
    }
  }

  if (args.jsonOutput) {
    const renderedJson = renderStructuredRoleReview(results);
    if (!renderedJson.ok) {
      recordCommandReport("multi-review", args, {
        status: 1,
        stdout: "",
        stderr: renderedJson.text,
        error: "",
        errorCode: ""
      }, startedAt, undefined, results);
      process.stderr.write(renderedJson.text);
      process.exit(1);
    }
    let parsedAggregate;
    try {
      parsedAggregate = JSON.parse(renderedJson.text);
    } catch {
      parsedAggregate = undefined;
    }
    recordCommandReport("multi-review", args, {
      status: 0,
      stdout: renderedJson.text,
      stderr: "",
      error: "",
      errorCode: ""
    }, startedAt, parsedAggregate, results);
    process.stdout.write(renderedJson.text);
    process.exit(0);
  }

  const rendered = renderRoleReviewSections("# Claude Multi-Agent Review", results, "Role");
  const summaryMode = `execution mode: ${runInParallel ? "parallel" : "sequential"}`;
  const output = rendered.text.replace("execution mode: parallel", summaryMode);
  recordCommandReport("multi-review", args, {
    status: rendered.failed.length ? 1 : 0,
    stdout: output,
    stderr: "",
    error: "",
    errorCode: ""
  }, startedAt, undefined, results);
  process.stdout.write(output);
  process.exit(rendered.failed.length ? 1 : 0);
}

function printJobs() {
  process.stdout.write(`${JSON.stringify(listJobs(process.cwd()), null, 2)}\n`);
}

function printResult(rawArgs) {
  const jobId = rawArgs[0];
  if (!jobId) {
    console.error("Usage: claude-companion.mjs result <job-id>");
    process.exit(2);
  }
  let payload;
  try {
    payload = resultForJob(process.cwd(), jobId);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(payload.status === "ok" ? 0 : 1);
}

function runCancel(rawArgs) {
  const jobId = rawArgs[0];
  if (!jobId) {
    console.error("Usage: claude-companion.mjs cancel <job-id>");
    process.exit(2);
  }
  let payload;
  try {
    payload = cancelJob(process.cwd(), jobId);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(payload.status === "cancelled" ? 0 : 1);
}

function runJobWorker(rawArgs) {
  const jobId = rawArgs[0];
  if (!jobId) {
    process.exit(2);
  }
  const job = readJob(process.cwd(), jobId);
  if (!job) {
    process.exit(1);
  }
  if (job.status === "cancelled") {
    process.exit(0);
  }
  if (!BACKGROUND_CAPABLE_COMMANDS.has(job.command)) {
    finishJob(process.cwd(), jobId, {
      status: 2,
      stdout: "",
      stderr: `Unsupported background job command "${job.command}".`,
      error: ""
    });
    process.exit(2);
  }
  markJobRunning(process.cwd(), jobId, process.pid);
  const result = spawnSync(process.execPath, [process.argv[1], job.command, ...(job.args ?? [])], {
    cwd: job.cwd || process.cwd(),
    env: {
      ...process.env,
      CLAUDE_FOR_CODEX_JOB_WORKER: "1"
    },
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
    timeout: 30 * 60 * 1000
  });
  const current = readJob(process.cwd(), jobId);
  if (current?.status === "cancelled") {
    process.exit(0);
  }
  finishJob(process.cwd(), jobId, {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message ?? result.error) : ""
  });
  process.exit(result.status ?? 1);
}

function runStoredJobCommand(job) {
  if (!BACKGROUND_CAPABLE_COMMANDS.has(job.command)) {
    return Promise.resolve({
      status: 2,
      stdout: "",
      stderr: `Unsupported reserved job command "${job.command}".`,
      error: ""
    });
  }
  const foregroundArgs = stripBackgroundArgs(job.args ?? []);
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [process.argv[1], job.command, ...foregroundArgs], {
      cwd: job.cwd || process.cwd(),
      env: {
        ...process.env,
        CLAUDE_FOR_CODEX_JOB_WORKER: "1"
      },
      detached: true,
      stdio: ["ignore", "pipe", "pipe"]
    });
    const started = Date.now();
    const timeout = setTimeout(() => {
      stopChildGroup("SIGTERM");
    }, 30 * 60 * 1000);
    const stdoutChunks = [];
    const stderrChunks = [];
    let settled = false;
    let stopRequested = false;

    function stopChildGroup(signal) {
      stopRequested = true;
      try {
        process.kill(-child.pid, signal);
      } catch {
        try {
          child.kill(signal);
        } catch {
          // Child may already have exited.
        }
      }
    }

    function handleWrapperSignal(signal) {
      stopChildGroup(signal);
    }

    process.once("SIGTERM", handleWrapperSignal);
    process.once("SIGINT", handleWrapperSignal);

    child.stdout?.on("data", (chunk) => stdoutChunks.push(Buffer.from(chunk)));
    child.stderr?.on("data", (chunk) => stderrChunks.push(Buffer.from(chunk)));
    child.once("error", (error) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timeout);
      process.removeListener("SIGTERM", handleWrapperSignal);
      process.removeListener("SIGINT", handleWrapperSignal);
      resolve({
        status: 1,
        stdout: Buffer.concat(stdoutChunks).toString("utf8"),
        stderr: Buffer.concat(stderrChunks).toString("utf8"),
        error: error.message || String(error)
      });
    });
    child.once("close", (status, signal) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timeout);
      process.removeListener("SIGTERM", handleWrapperSignal);
      process.removeListener("SIGINT", handleWrapperSignal);
      resolve({
        status: status ?? (signal ? 1 : 0),
        stdout: Buffer.concat(stdoutChunks).toString("utf8"),
        stderr: Buffer.concat(stderrChunks).toString("utf8"),
        error: stopRequested || Date.now() - started >= 30 * 60 * 1000 ? `Child terminated by ${signal ?? "timeout"}.` : ""
      });
    });
  });
}

async function runReservedJob(rawArgs) {
  let jobId;
  try {
    jobId = parseJobIdArg(rawArgs);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }

  let claim;
  try {
    claim = claimReservedJob(process.cwd(), jobId, process.pid);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  if (claim.status !== "claimed") {
    process.stdout.write(`${JSON.stringify({
      status: claim.status,
      jobId,
      message: "Reserved job was not queued and was not executed."
    }, null, 2)}\n`);
    process.exit(1);
  }

  const result = await runStoredJobCommand(claim.job);
  const finished = finishJob(process.cwd(), claim.job.id, result);
  process.stdout.write(`${JSON.stringify({
    status: finished.status,
    jobId: finished.id,
    exitStatus: finished.exitStatus
  }, null, 2)}\n`);
  process.exit(result.status ?? 1);
}

const [command, ...rawArgs] = process.argv.slice(2);

if (!VALID_COMMANDS.has(command)) {
  console.error(`Usage: claude-companion.mjs ${Array.from(VALID_COMMANDS).join("|")} [args]`);
  process.exit(2);
}

switch (command) {
  case "setup":
    printSetup(rawArgs);
    break;
  case "capabilities":
    printCapabilities();
    break;
  case "review":
    await runClaudeTask("review", rawArgs);
    break;
  case "adversarial-review":
    await runClaudeTask("adversarial-review", rawArgs);
    break;
  case "multi-review":
    await runClaudeMultiReview(rawArgs);
    break;
  case "plan":
    await runClaudeTask("plan", rawArgs);
    break;
  case "rescue":
    await runClaudeTask("rescue", rawArgs);
    break;
  case "status":
    printStatus();
    break;
  case "review-gate":
    runReviewGate(rawArgs);
    break;
  case "jobs":
    printJobs();
    break;
  case "result":
    printResult(rawArgs);
    break;
  case "cancel":
    runCancel(rawArgs);
    break;
  case "report":
    printReport(rawArgs);
    break;
  case "release-check":
    runReleaseCheckCommand(rawArgs);
    break;
  case "__run-job":
    runJobWorker(rawArgs);
    break;
  case "reserve-job":
    try {
      process.stdout.write(`${JSON.stringify(handleReserveJob(rawArgs), null, 2)}\n`);
    } catch (error) {
      console.error(error.message || String(error));
      process.exit(2);
    }
    break;
  case "run-reserved-job":
    await runReservedJob(rawArgs);
    break;
}
