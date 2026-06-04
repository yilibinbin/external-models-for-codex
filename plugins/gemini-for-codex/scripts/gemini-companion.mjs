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
import { loadPromptTemplate, renderPromptTemplate } from "./lib/prompt-template.mjs";
import { renderStructuredReview, validateStructuredReview } from "./lib/render-review.mjs";
import {
  ContextProviderError,
  parseContextOptions,
  resolveContext,
  resolveProviderSelection
} from "./lib/context-provider.mjs";
import {
  readReviewJson,
  renderReviewComment,
  renderWorkflow,
  reviewToAnnotations,
  validateWorkflow,
  workflowPath,
  writeWorkflow
} from "./lib/github-actions.mjs";
import {
  ADVERSARIAL_LENSES,
  DEFAULT_ADVERSARIAL_LENSES,
  DEFAULT_MULTI_REVIEW_ROLES,
  REVIEW_ROLES,
  defaultRoleObjects,
  hashRolePack,
  listRolePacks,
  resolveExplicitRoles,
  resolveRolePack,
  rolePackGateCompatible,
  rolePackNativeAgentsCompatible,
  rolePackReportMetadata,
  rolePackSummary,
  rolesForPack,
  validateBuiltInRolePacks,
  validateRolePackFile
} from "./lib/role-packs.mjs";
import {
  listMailboxThreads,
  mailboxSummary,
  postMailboxMessage,
  showMailboxThread
} from "./lib/mailbox.mjs";
import {
  claimLease,
  leaseSummary,
  listLeases,
  releaseLease
} from "./lib/leases.mjs";
import { mailboxDirForCwd, leasesDirForCwd } from "./lib/state.mjs";

const ROOT_DIR = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const VALID_COMMANDS = new Set(["setup", "capabilities", "report", "release-check", "github-actions", "roles", "mailbox", "leases", "review", "adversarial-review", "multi-review", "plan", "status", "review-gate", "jobs", "result", "cancel", "rescue", "recommend-execution-mode", "sessions", "__run-job", "reserve-job", "run-reserved-job"]);
const BACKGROUND_CAPABLE_COMMANDS = new Set(["review", "adversarial-review", "multi-review", "rescue"]);
const VALID_SCOPES = new Set(["auto", "working-tree", "branch"]);
const VALID_REVIEW_GATE_MODES = new Set(["multi-role"]);
const GEMINI_CLI_PATH_ENV = "GEMINI_CLI_PATH";
const REVIEW_GATE_ENV = "GEMINI_FOR_CODEX_REVIEW_GATE";
const REVIEW_GATE_TIMEOUT_MS = 15 * 60 * 1000;
const REVIEW_GATE_ROLE_TIMEOUT_MS = 2 * 60 * 1000;
const REPORT_VERSION = 1;
let geminiHelpReportCache = null;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? process.cwd(),
    env: options.env ?? process.env,
    encoding: "utf8",
    input: options.input,
    maxBuffer: 20 * 1024 * 1024,
    timeout: options.timeout ?? 15 * 60 * 1000
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

function candidateGeminiCommands() {
  const executableNames = process.platform === "win32" ? ["gemini.cmd", "gemini.exe", "gemini"] : ["gemini"];
  const home = process.env.HOME || os.homedir();
  const candidates = [];
  const add = (candidate) => {
    if (candidate) {
      candidates.push(candidate);
    }
  };
  const addBin = (dir) => {
    for (const name of executableNames) {
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
  addBin(process.env.PNPM_HOME);
  addBin(process.env.NPM_CONFIG_PREFIX ? path.join(process.env.NPM_CONFIG_PREFIX, "bin") : "");
  addBin(process.env.npm_config_prefix ? path.join(process.env.npm_config_prefix, "bin") : "");
  addBin(process.env.HOMEBREW_PREFIX ? path.join(process.env.HOMEBREW_PREFIX, "bin") : "");

  for (const pattern of [
    path.join(home, ".nvm", "versions", "node", "*", "bin", "gemini"),
    path.join(home, ".fnm", "node-versions", "*", "installation", "bin", "gemini"),
    path.join(home, ".asdf", "installs", "nodejs", "*", "bin", "gemini")
  ]) {
    candidates.push(...expandExecutableCandidates(pattern));
  }

  for (const dir of ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]) {
    addBin(dir);
  }
  return [...new Set(candidates)];
}

function geminiCommand() {
  const configuredPath = process.env[GEMINI_CLI_PATH_ENV];
  if (configuredPath && isExecutable(configuredPath)) {
    return configuredPath;
  }
  const pathCommand = findOnPath("gemini");
  if (pathCommand) {
    return pathCommand;
  }
  for (const candidate of candidateGeminiCommands()) {
    if (isExecutable(candidate)) {
      return candidate;
    }
  }
  return "gemini";
}

function runGemini(args, options = {}) {
  return run(geminiCommand(), args, options);
}

function hasBinary(name) {
  return run(name, ["--version"]).status === 0;
}

function hasGemini() {
  return runGemini(["--version"]).status === 0;
}

function geminiHelpReport() {
  const result = runGemini(["--help"]);
  const help = result.stdout || result.stderr || "";
  const requiredFlags = ["--prompt", "--output-format", "--approval-mode", "--skip-trust"];
  if (process.env.GEMINI_FOR_CODEX_SANDBOX === "on") {
    requiredFlags.push("--sandbox");
  }
  const missing = requiredFlags.filter((flag) => !help.includes(flag));
  return {
    checked: result.status === 0,
    ok: result.status === 0 && missing.length === 0,
    missing,
    requiredFlags,
    capabilities: geminiCapabilitiesFromHelp(help),
    error: result.status === 0 ? "" : (result.stderr || result.error || "gemini --help failed").trim()
  };
}

function geminiCapabilitiesFromHelp(help) {
  const text = String(help ?? "");
  return {
    resume: text.includes("--resume"),
    sessionId: text.includes("--session-id"),
    sessionFile: text.includes("--session-file"),
    listSessions: text.includes("--list-sessions"),
    worktree: text.includes("--worktree"),
    includeDirectories: text.includes("--include-directories")
  };
}

function currentGeminiCapabilities() {
  if (!geminiHelpReportCache) {
    geminiHelpReportCache = geminiHelpReport();
  }
  return geminiHelpReportCache.capabilities;
}

function requireGeminiCapability(capability, flag) {
  const capabilities = currentGeminiCapabilities();
  if (!capabilities[capability]) {
    throw new Error(`Gemini CLI does not report support for ${flag}. Run setup to inspect available capabilities.`);
  }
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
    } else if (arg === "--role-pack") {
      parsed.rolePack = readOptionValue(tokens, index, arg).trim();
      if (!parsed.rolePack) {
        throw new Error("Missing value for --role-pack.");
      }
      index += 1;
    } else if (arg === "--role-pack-file") {
      throw new Error("--role-pack-file is validate/inspect-only and is not accepted by review commands.");
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
    } else if (arg === "--structured" || arg === "--review-json") {
      parsed.structuredReview = true;
    } else if (arg === "--native-agents") {
      parsed.nativeAgents = true;
    } else if (arg === "--use-mailbox") {
      parsed.useMailbox = true;
    } else if (arg === "--advisory-leases") {
      parsed.advisoryLeases = true;
    } else if (arg === "--context-provider") {
      parsed.contextProvider = readOptionValue(tokens, index, arg);
      index += 1;
    } else if (arg.startsWith("--context-provider=")) {
      parsed.contextProvider = arg.slice("--context-provider=".length);
      if (!parsed.contextProvider) {
        throw new Error("Missing value for --context-provider.");
      }
    } else if (arg === "--context-budget") {
      parsed.contextBudget = readOptionValue(tokens, index, arg);
      index += 1;
    } else if (arg.startsWith("--context-budget=")) {
      parsed.contextBudget = arg.slice("--context-budget=".length);
      if (!parsed.contextBudget) {
        throw new Error("Missing value for --context-budget.");
      }
    } else if (arg === "--context-timeout-ms") {
      parsed.contextTimeoutMs = readOptionValue(tokens, index, arg);
      index += 1;
    } else if (arg.startsWith("--context-timeout-ms=")) {
      parsed.contextTimeoutMs = arg.slice("--context-timeout-ms=".length);
      if (!parsed.contextTimeoutMs) {
        throw new Error("Missing value for --context-timeout-ms.");
      }
    } else if (arg === "--context-strict") {
      parsed.contextStrict = true;
    } else if (arg.startsWith("--resume=")) {
      parsed.resume = arg.slice("--resume=".length) || "latest";
    } else if (arg === "--resume") {
      parsed.resume = "latest";
    } else if (arg === "--fresh") {
      parsed.fresh = true;
    } else if (arg === "--session-id") {
      parsed.sessionId = readOptionValue(tokens, index, arg);
      index += 1;
    } else if (arg.startsWith("--session-id=")) {
      parsed.sessionId = arg.slice("--session-id=".length);
      if (!parsed.sessionId) {
        throw new Error("Missing value for --session-id.");
      }
    } else if (arg.startsWith("--worktree=")) {
      parsed.worktree = arg.slice("--worktree=".length) || true;
    } else if (arg === "--worktree") {
      parsed.worktree = true;
    } else if (arg === "--write") {
      parsed.write = true;
    } else {
      parsed._.push(arg);
    }
  }
  return parsed;
}

function resolveReviewRoles(args) {
  if (args.rolePack !== undefined && args.roles !== undefined) {
    throw new Error("--role-pack conflicts with --roles/--role.");
  }
  if (args.rolePack !== undefined) {
    const pack = resolveRolePack(args.rolePack);
    args.rolePackSummary = rolePackSummary(pack);
    args.rolePack = pack;
    return rolesForPack(pack);
  }
  if (args.roles === undefined) {
    return [];
  }
  if (!args.roles.length) {
    throw new Error("Missing value for --roles.");
  }
  return resolveExplicitRoles(args.roles);
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

function countStatusFiles(stdout) {
  return String(stdout ?? "").split("\n").filter((line) => line.trim()).length;
}

function shortstatFileCount(stdout) {
  const match = String(stdout ?? "").match(/(\d+)\s+files?\s+changed/);
  return match ? Number(match[1]) : 0;
}

function shortstatChangedLines(stdout) {
  const text = String(stdout ?? "");
  const insertions = Number(text.match(/(\d+)\s+insertions?/)?.[1] ?? 0);
  const deletions = Number(text.match(/(\d+)\s+deletions?/)?.[1] ?? 0);
  return insertions + deletions;
}

function gitStatus(cwd, args) {
  return run("git", args, {
    cwd,
    timeout: 10000,
    env: {
      ...process.env,
      LC_ALL: "C",
      LANG: "C"
    }
  });
}

function recommendExecutionMode(rawArgs) {
  let args;
  try {
    args = parseArgs(rawArgs);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  const cwd = process.cwd();
  const commands = [];
  const repo = gitStatus(cwd, ["rev-parse", "--show-toplevel"]);
  if (repo.status !== 0) {
    return {
      recommendation: "foreground",
      reason: "not a git repository",
      reviewable: false,
      fileCountEstimate: 0,
      changedLineEstimate: 0,
      hasUntracked: false,
      git: { repository: false, error: (repo.stderr || repo.error || "").trim() },
      commands
    };
  }

  const status = gitStatus(cwd, ["status", "--short", "--untracked-files=all"]);
  commands.push("git status --short --untracked-files=all");
  const statusFileCount = countStatusFiles(status.stdout);
  const hasUntracked = String(status.stdout).split("\n").some((line) => line.startsWith("??"));
  const staged = gitStatus(cwd, ["diff", "--shortstat", "--cached"]);
  commands.push("git diff --shortstat --cached");
  const unstaged = gitStatus(cwd, ["diff", "--shortstat"]);
  commands.push("git diff --shortstat");

  let branch = null;
  if (args.base) {
    const baseExists = hasBaseRef(args.base);
    if (baseExists && hasHead()) {
      const branchStat = gitStatus(cwd, ["diff", "--shortstat", `${args.base}...HEAD`]);
      commands.push(`git diff --shortstat ${args.base}...HEAD`);
      branch = {
        available: branchStat.status === 0,
        base: args.base,
        fileCount: shortstatFileCount(branchStat.stdout),
        changedLines: shortstatChangedLines(branchStat.stdout),
        error: branchStat.status === 0 ? "" : (branchStat.stderr || branchStat.error || "").trim()
      };
    } else {
      branch = {
        available: false,
        base: args.base,
        fileCount: 0,
        changedLines: 0,
        error: "base ref or HEAD is unavailable"
      };
    }
  }

  const fileCountEstimate = Math.max(
    statusFileCount,
    shortstatFileCount(staged.stdout) + shortstatFileCount(unstaged.stdout),
    branch?.fileCount ?? 0
  );
  const changedLineEstimate = shortstatChangedLines(staged.stdout)
    + shortstatChangedLines(unstaged.stdout)
    + (branch?.changedLines ?? 0);
  const reviewable = statusFileCount > 0 || (branch?.fileCount ?? 0) > 0;
  let recommendation = "foreground";
  let reason = "empty working tree or branch scope";
  if (branch && !branch.available) {
    recommendation = "background";
    reason = "branch base unavailable; manual/background review recommended";
  } else if (!reviewable) {
    recommendation = "foreground";
  } else if (hasUntracked || fileCountEstimate > 2 || changedLineEstimate > 50) {
    recommendation = "background";
    reason = "review appears larger than a small foreground review";
  } else {
    recommendation = "foreground";
    reason = "small review scope";
  }

  return {
    recommendation,
    reason,
    reviewable,
    fileCountEstimate,
    changedLineEstimate,
    hasUntracked,
    git: {
      repository: true,
      headAvailable: hasHead(),
      branch
    },
    commands
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
  const branchDiff = includeBaseBranch
    ? baseExists
      ? git(["diff", `${base}...HEAD`, ...pathArgs])
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
    branchDiff
      ? formatCommandResult(
          baseExists
            ? `git diff ${base}...HEAD${paths.length ? ` -- ${pathLabel}` : ""}`
            : "branch diff skipped",
          branchDiff
        )
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

function geminiPrintArgs(prompt, options = {}) {
  const args = [
    "--skip-trust",
    "--approval-mode",
    "plan",
    "--output-format",
    "json"
  ];

  if (options.model) {
    args.push("--model", options.model);
  }
  if (process.env.GEMINI_FOR_CODEX_SANDBOX === "on") {
    args.push("--sandbox");
  }
  if (options.resume && !options.fresh) {
    args.push("--resume", options.resume === true ? "latest" : String(options.resume));
  }
  if (options.sessionId) {
    args.push("--session-id", options.sessionId);
  }
  if (options.worktree) {
    args.push("--worktree");
    if (options.worktree !== true) {
      args.push(String(options.worktree));
    }
  }
  if (options.includeDirectories?.length) {
    for (const includeDirectory of options.includeDirectories) {
      args.push("--include-directories", includeDirectory);
    }
  }
  args.push("--prompt", prompt);
  return args;
}

function geminiPrint(prompt, options = {}) {
  const result = runGemini(geminiPrintArgs(prompt, options), { timeout: options.timeout, cwd: options.cwd });
  if (result.status !== 0) {
    return result;
  }
  const parsed = parseGeminiJson(result.stdout);
  return {
    ...result,
    status: parsed.ok ? 0 : 1,
    stdout: parsed.response,
    stderr: parsed.ok ? result.stderr : parsed.error
  };
}

function reportRoot(env = process.env) {
  const base = env.GEMINI_FOR_CODEX_DATA
    ? path.resolve(env.GEMINI_FOR_CODEX_DATA)
    : path.join(os.homedir(), ".codex", "gemini-for-codex");
  return path.join(base, "reports");
}

function reportFile(env = process.env) {
  return path.join(reportRoot(env), "latest.json");
}

function sanitizeReportPayload(payload) {
  const metadata = payload.contextMetadata || {};
  const safe = {
    version: REPORT_VERSION,
    timestamp: new Date().toISOString(),
    command: payload.command || "",
    cwdHash: createHash("sha256").update(canonicalWorkspaceRoot(payload.cwd || process.cwd())).digest("hex"),
    status: Number.isInteger(payload.status) ? payload.status : 0,
    geminiAvailable: Boolean(payload.geminiAvailable),
    contextProvider: String(metadata.contextProvider || ""),
    contextStatus: String(metadata.contextStatus || "disabled"),
    contextBytes: Number(metadata.contextBytes || 0),
    contextDurationMs: Number(metadata.contextDurationMs || 0),
    contextFailureReason: String(metadata.contextFailureReason || "disabled"),
    contextDegraded: Boolean(metadata.contextDegraded)
  };
  if (payload.rolePack) {
    safe.rolePack = {
      name: String(payload.rolePack.name || ""),
      source: String(payload.rolePack.source || ""),
      schema_version: Number(payload.rolePack.schema_version || 0),
      hash: String(payload.rolePack.hash || ""),
      roles: Array.isArray(payload.rolePack.roles) ? payload.rolePack.roles.map((role) => String(role)) : [],
      gate_compatible: Boolean(payload.rolePack.gate_compatible),
      native_agents_compatible: Boolean(payload.rolePack.native_agents_compatible)
    };
  }
  if (payload.mailbox) {
    safe.mailbox = {
      enabled: Boolean(payload.mailbox.enabled),
      threadIdHash: String(payload.mailbox.threadIdHash || ""),
      messageCount: Number(payload.mailbox.messageCount || 0),
      writeFailures: Number(payload.mailbox.writeFailures || 0)
    };
  }
  if (payload.leases) {
    safe.leases = {
      enabled: Boolean(payload.leases.enabled),
      claimed: Number(payload.leases.claimed || 0),
      conflicts: Number(payload.leases.conflicts || 0),
      degraded: Boolean(payload.leases.degraded)
    };
  }
  return safe;
}

function writeOperationReport(payload) {
  const safe = sanitizeReportPayload(payload);
  const file = reportFile();
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const tmp = `${file}.${process.pid}.${Date.now().toString(36)}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(safe, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(tmp, file);
  return safe;
}

function printReport(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  if (tokens.length && !tokens.every((token) => token === "--latest" || token === "--json")) {
    console.error("Usage: gemini-companion.mjs report [--latest] [--json]");
    process.exit(2);
  }
  const file = reportFile();
  if (!fs.existsSync(file)) {
    process.stdout.write(`${JSON.stringify({ available: false, reportFile: file }, null, 2)}\n`);
    process.exit(1);
  }
  process.stdout.write(fs.readFileSync(file, "utf8"));
}

function printCapabilities() {
  const preflight = geminiHelpReport();
  const cwd = process.cwd();
  const leases = listLeases(cwd);
  const mailbox = listMailboxThreads(cwd);
  process.stdout.write(`${JSON.stringify({
    available: hasGemini(),
    command: geminiCommand(),
    preflight,
    capabilities: preflight.capabilities,
    rolePacks: {
      available: true,
      builtIn: listRolePacks().map((pack) => pack.name)
    },
    mailbox: {
      directory: mailboxDirForCwd(cwd),
      threadCount: mailbox.threads.length
    },
    leases: {
      directory: leasesDirForCwd(cwd),
      activeCount: leases.active.length,
      degraded: leases.degraded,
      primitive: leases.primitive
    }
  }, null, 2)}\n`);
  process.exit(preflight.ok ? 0 : 1);
}

function defaultContextMetadata() {
  return {
    contextProvider: "",
    contextStatus: "disabled",
    contextBytes: 0,
    contextDurationMs: 0,
    contextFailureReason: "disabled",
    contextDegraded: false
  };
}

async function resolveCommandContext(args, { allowProviderErrors = false } = {}) {
  try {
    parseContextOptions(args);
    const resolved = await resolveContext(args, { cwd: process.cwd(), env: process.env, run });
    return resolved;
  } catch (error) {
    if (error instanceof ContextProviderError && allowProviderErrors) {
      return {
        block: "",
        metadata: {
          ...defaultContextMetadata(),
          contextStatus: "unavailable",
          contextFailureReason: error.reason || "unsafe-config",
          contextDegraded: true
        },
        warning: error.message || String(error)
      };
    }
    throw error;
  }
}

function scanFileForForbidden(filePath, forbidden) {
  if (!fs.existsSync(filePath)) {
    return [`missing file: ${filePath}`];
  }
  const text = fs.readFileSync(filePath, "utf8");
  return forbidden.filter((needle) => text.includes(needle)).map((needle) => `${filePath} contains forbidden string ${needle}`);
}

function releaseCheckOptions(rawArgs = []) {
  const tokens = normalizeArgv(rawArgs);
  const options = { ciSimulate: false };
  for (const token of tokens) {
    if (token === "--ci-simulate") {
      options.ciSimulate = true;
    } else {
      throw new Error(`Unknown release-check option "${token}".`);
    }
  }
  return options;
}

function runReleaseCheck(rawArgs = []) {
  let options;
  try {
    options = releaseCheckOptions(rawArgs);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  const failures = [];
  const manifestPath = path.join(ROOT_DIR, ".codex-plugin", "plugin.json");
  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    if (manifest.name !== "gemini-for-codex") failures.push("manifest name mismatch");
    if (manifest.version !== "0.8.0") failures.push(`manifest version is ${manifest.version}, expected 0.8.0`);
    const legacyPluginName = ["claude", "for", "codex"].join("-");
    if (JSON.stringify(manifest).includes(legacyPluginName)) failures.push(`manifest contains ${legacyPluginName}`);
  } catch (error) {
    failures.push(`manifest parse failed: ${error.message || String(error)}`);
  }
  failures.push(...scanFileForForbidden(path.join(ROOT_DIR, "README.md"), [
    ["", "Users", "fanghao"].join("/"),
    ["CLAUDE", "PLUGIN_ROOT"].join("_"),
    ["CLAUDE", "FOR_CODEX"].join("_")
  ]));
  const hooksPath = path.join(ROOT_DIR, "hooks", "hooks.json");
  try {
    const hooks = JSON.parse(fs.readFileSync(hooksPath, "utf8"));
    if (!hooks.hooks?.Stop) failures.push("hooks manifest does not register Stop");
  } catch (error) {
    failures.push(`hooks manifest parse failed: ${error.message || String(error)}`);
  }
  const providerChecks = releaseCheckProviderFixtures();
  failures.push(...providerChecks.failures);
  const rolePackChecks = checkRolePacks();
  failures.push(...rolePackChecks.failures);
  const coordinationChecks = checkCoordinationFixtures();
  failures.push(...coordinationChecks.failures);
  const githubActionsChecks = options.ciSimulate ? checkGithubActionsCi() : { ok: true, failures: [] };
  failures.push(...githubActionsChecks.failures);
  const payload = {
    ok: failures.length === 0,
    checks: {
      manifest: true,
      docsForbiddenStrings: true,
      hooks: true,
      contextProviderFixtures: providerChecks.ok,
      rolePacks: rolePackChecks.ok,
      mailboxLeases: coordinationChecks.ok,
      githubActionsCi: githubActionsChecks.ok
    },
    failures
  };
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(payload.ok ? 0 : 1);
}

function checkGithubActionsCi() {
  const failures = [];
  try {
    const plain = renderWorkflow(ROOT_DIR);
    const annotated = renderWorkflow(ROOT_DIR, { annotations: true });
    const plainValidation = validateWorkflow(plain);
    const annotationValidation = validateWorkflow(annotated, { annotations: true });
    if (!plainValidation.ok) {
      failures.push(...plainValidation.checks.filter((check) => !check.ok).map((check) => `github actions default failed: ${check.name}`));
    }
    if (!annotationValidation.ok) {
      failures.push(...annotationValidation.checks.filter((check) => !check.ok).map((check) => `github actions annotations failed: ${check.name}`));
    }
    if (!plain.includes("npm install -g @openai/codex")) failures.push("github actions missing Codex CLI install");
    if (!plain.includes("--ref gemini-for-codex-v0.8.0")) failures.push("github actions missing immutable Gemini release ref");
    if (plain.includes("pull_request_target")) failures.push("github actions contains pull_request_target");
  } catch (error) {
    failures.push(`github actions CI simulation failed: ${error.message || String(error)}`);
  }
  return { ok: failures.length === 0, failures };
}

function checkCoordinationFixtures() {
  const failures = [];
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "gemini-for-codex-coordination-"));
  const workspace = path.join(temp, "workspace");
  const data = path.join(temp, "data");
  fs.mkdirSync(workspace, { recursive: true });
  try {
    const summary = "AKIA\u001b[31mABCDEFGHIJKLMNOP /home/example/secret";
    const message = postMailboxMessage(workspace, {
      threadId: "thread-release-check",
      jobId: "job-release-check",
      role: "correctness",
      command: "multi-review",
      mode: "manual",
      status: "note",
      source: "manual",
      summary
    }, { ...process.env, GEMINI_FOR_CODEX_DATA: data });
    if (message.summary.includes("AKIA") || message.summary.includes("/home/example")) {
      failures.push("mailbox sanitizer fixture leaked secret or path");
    }
    const first = claimLease(workspace, { path: "file.txt", role: "correctness", ttl: "60s", mode: "manual" }, { ...process.env, GEMINI_FOR_CODEX_DATA: data });
    const second = claimLease(workspace, { path: "file.txt", role: "security", ttl: "60s", mode: "manual" }, { ...process.env, GEMINI_FOR_CODEX_DATA: data });
    if (first.status !== "claimed" || second.status !== "conflict") {
      failures.push("lease atomic same-path fixture did not produce one winner and one conflict");
    }
    const concurrent = runConcurrentLeaseFixture(workspace, data);
    if (!concurrent.ok) {
      failures.push(concurrent.reason);
    }
  } catch (error) {
    failures.push(`coordination fixture failed: ${error.message || String(error)}`);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
  return { ok: failures.length === 0, failures };
}

function runConcurrentLeaseFixture(workspace, data) {
  const script = `
    import { spawn } from "node:child_process";
    import fs from "node:fs";
    import path from "node:path";
    const [modulePath, workspace, data] = process.argv.slice(1);
    const start = path.join(data, "start");
    fs.mkdirSync(data, { recursive: true });
    const childCode = \`
      import fs from "node:fs";
      const [modulePath, workspace, data, start, role] = process.argv.slice(1);
      const m = await import(modulePath);
      while (!fs.existsSync(start)) { await new Promise((resolve) => setTimeout(resolve, 5)); }
      const result = m.claimLease(workspace, {path:"concurrent.txt", role, ttl:"60s", mode:"manual"}, {GEMINI_FOR_CODEX_DATA: data, HOME: process.env.HOME});
      console.log(JSON.stringify(result));
    \`;
    function run(role) {
      return new Promise((resolve) => {
        const child = spawn(process.execPath, ["--input-type=module", "-e", childCode, modulePath, workspace, data, start, role], { stdio: ["ignore", "pipe", "pipe"] });
        let stdout = "";
        let stderr = "";
        child.stdout.on("data", (chunk) => stdout += chunk);
        child.stderr.on("data", (chunk) => stderr += chunk);
        child.on("close", (status) => resolve({status, stdout, stderr}));
      });
    }
    const children = [run("correctness"), run("security")];
    fs.writeFileSync(start, "go");
    const results = await Promise.all(children);
    console.log(JSON.stringify(results));
  `;
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", script, path.join(ROOT_DIR, "scripts", "lib", "leases.mjs"), workspace, data], {
    encoding: "utf8",
    timeout: 10_000,
    maxBuffer: 1024 * 1024
  });
  if ((result.status ?? 1) !== 0) {
    return { ok: false, reason: `concurrent lease fixture failed: ${result.stderr || result.error || result.status}` };
  }
  try {
    const rows = JSON.parse(result.stdout.trim()).map((row) => JSON.parse(row.stdout));
    const claimed = rows.filter((row) => row.status === "claimed").length;
    const conflicts = rows.filter((row) => row.status === "conflict").length;
    return claimed === 1 && conflicts === 1
      ? { ok: true }
      : { ok: false, reason: `concurrent lease fixture expected one claim and one conflict, got ${result.stdout.trim()}` };
  } catch (error) {
    return { ok: false, reason: `concurrent lease fixture parse failed: ${error.message || String(error)}` };
  }
}

function checkRolePacks() {
  const failures = [];
  const builtIns = validateBuiltInRolePacks();
  failures.push(...builtIns.failures.map((failure) => `role pack invalid: ${failure}`));
  try {
    const packs = listRolePacks();
    const expected = ["backend", "default", "docs", "frontend", "minimal", "release", "security", "testing"];
    const names = packs.map((pack) => pack.name).sort();
    if (JSON.stringify(names) !== JSON.stringify(expected)) {
      failures.push(`role pack names mismatch: ${names.join(", ")}`);
    }
    for (const pack of packs) {
      if (!/^sha256:[a-f0-9]{64}$/.test(pack.hash)) {
        failures.push(`role pack ${pack.name} hash is not stable sha256 metadata`);
      }
    }
  } catch (error) {
    failures.push(`role pack check failed: ${error.message || String(error)}`);
  }
  return { ok: failures.length === 0, failures };
}

function releaseCheckProviderFixtures() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "gemini-for-codex-release-check-"));
  const workspace = path.join(temp, "workspace");
  const external = path.join(temp, "external");
  fs.mkdirSync(workspace, { recursive: true });
  fs.mkdirSync(external, { recursive: true });
  const provider = path.join(external, "provider-bin");
  fs.writeFileSync(provider, "#!/bin/sh\nexit 0\n", { encoding: "utf8", mode: 0o755 });
  const config = path.join(external, "providers.json");
  const failures = [];
  try {
    fs.writeFileSync(config, JSON.stringify({
      providers: {
        safe: {
          command: [provider],
          env: { GEMINI_CONTEXT_PROVIDER_MODE: "summary" }
        }
      },
      defaultProvider: "safe"
    }), { encoding: "utf8", mode: 0o600 });
    const selected = parseContextOptions({ contextProvider: "safe" });
    const resolved = {
      env: { ...process.env, GEMINI_FOR_CODEX_CONTEXT_CONFIG: config }
    };
    const safeSelection = resolveProviderSelectionForReleaseCheck(selected, workspace, resolved.env);
    if (safeSelection.status !== "selected") failures.push("safe provider fixture did not select");
    fs.writeFileSync(config, JSON.stringify({
      providers: { unsafe: { command: [path.join(workspace, "provider")] } },
      defaultProvider: "unsafe"
    }), { encoding: "utf8", mode: 0o600 });
    fs.writeFileSync(path.join(workspace, "provider"), "#!/bin/sh\nexit 0\n", { encoding: "utf8", mode: 0o755 });
    expectContextFixtureFailure("workspace provider executable should fail", { contextProvider: "unsafe" }, workspace, resolved.env, failures);
    fs.writeFileSync(config, JSON.stringify({
      providers: { shell: { command: ["/bin/sh"] } },
      defaultProvider: "shell"
    }), { encoding: "utf8", mode: 0o600 });
    expectContextFixtureFailure("shell trampoline should fail", { contextProvider: "shell" }, workspace, resolved.env, failures);
    fs.writeFileSync(config, JSON.stringify({
      providers: { badenv: { command: [provider], env: { SECRET: "x" } } },
      defaultProvider: "badenv"
    }), { encoding: "utf8", mode: 0o600 });
    expectContextFixtureFailure("unsafe env key should fail", { contextProvider: "badenv" }, workspace, resolved.env, failures);
    fs.writeFileSync(config, JSON.stringify({
      providers: { rel: { command: ["provider"] } },
      defaultProvider: "rel"
    }), { encoding: "utf8", mode: 0o600 });
    expectContextFixtureFailure("relative executable should fail", { contextProvider: "rel" }, workspace, resolved.env, failures);
  } catch (error) {
    failures.push(`provider fixture exception: ${error.message || String(error)}`);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
  return { ok: failures.length === 0, failures };
}

function resolveProviderSelectionForReleaseCheck(options, cwd, env) {
  return resolveProviderSelection(options, cwd, env);
}

function expectContextFixtureFailure(label, options, cwd, env, failures) {
  try {
    resolveProviderSelectionForReleaseCheck(parseContextOptions(options), cwd, env);
    failures.push(label);
  } catch (error) {
    if (!(error instanceof ContextProviderError)) {
      failures.push(`${label}: wrong error ${error.message || String(error)}`);
    }
  }
}

function runGeminiAsync(args, options = {}) {
  return new Promise((resolve) => {
    const child = spawn(geminiCommand(), args, {
      cwd: options.cwd ?? process.cwd(),
      env: process.env,
      stdio: ["pipe", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const timeoutMs = options.timeout ?? 15 * 60 * 1000;
    const timer = setTimeout(() => {
      if (!settled) {
        child.kill("SIGTERM");
      }
    }, timeoutMs);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
      if (stdout.length > 20 * 1024 * 1024) {
        child.kill("SIGTERM");
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
      if (stderr.length > 20 * 1024 * 1024) {
        child.kill("SIGTERM");
      }
    });
    child.stdin.end(options.input ?? "");
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ status: 1, stdout, stderr, error: String(error.message ?? error), errorCode: error.code ? String(error.code) : "" });
    });
    child.on("close", (status, signal) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({
        status: status ?? 1,
        stdout,
        stderr: signal ? `${stderr}${stderr ? "\n" : ""}terminated by ${signal}` : stderr,
        error: "",
        errorCode: ""
      });
    });
  });
}

async function geminiPrintAsync(prompt, options = {}) {
  const result = await runGeminiAsync(geminiPrintArgs(prompt, options), { timeout: options.timeout, cwd: options.cwd });
  if (result.status !== 0) {
    return result;
  }
  const parsed = parseGeminiJson(result.stdout);
  return {
    ...result,
    status: parsed.ok ? 0 : 1,
    stdout: parsed.response,
    stderr: parsed.ok ? result.stderr : parsed.error
  };
}

function parseGeminiJson(stdout) {
  let parsed;
  try {
    parsed = JSON.parse(stdout || "{}");
  } catch (error) {
    return { ok: false, response: "", error: `Invalid Gemini JSON output: ${error.message}` };
  }
  if (parsed.error) {
    return { ok: false, response: "", error: JSON.stringify(parsed.error) };
  }
  if (typeof parsed.response !== "string") {
    return { ok: false, response: "", error: "Gemini JSON output did not include a string response." };
  }
  return { ok: true, response: parsed.response, error: "" };
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
  const session = readJson(currentSessionFileForCwd(process.cwd()), {});
  const workerCommand = [
    process.argv0 || process.execPath,
    process.argv[1],
    "run-reserved-job"
  ];
  const job = reserveJob(process.cwd(), {
    command,
    args: commandArgs,
    cwd: process.cwd(),
    sessionId: session?.sessionId || "",
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
  return createHash("sha256").update(parts.join("\n--- gemini-for-codex ---\n")).digest("hex");
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

function reviewOutputContract(args) {
  if (args.structuredReview || args.jsonOutput) {
    return [
      "<output_contract>",
      "Return exactly one JSON object and no Markdown.",
      "Schema:",
      "{",
      '  "verdict": "approve | needs-attention",',
      '  "summary": "short review summary",',
      '  "findings": [',
      '    {"severity": "critical|high|medium|low", "title": "short title", "body": "issue, evidence, and impact", "file": "path", "line_start": 1, "line_end": 1, "confidence": 0.0, "recommendation": "concrete action"}',
      "  ],",
      '  "next_steps": ["concrete next step"]',
      "}",
      "Use verdict approve and an empty findings array when there are no findings.",
      "</output_contract>"
    ].join("\n");
  }
  return [
    "<output_contract>",
    "## Findings",
    "- [Severity] file:line - issue, evidence, impact, suggested direction",
    "## Open Questions",
    "## Residual Risk",
    "</output_contract>"
  ].join("\n");
}

function reviewPrompt(kind, args, contextBlock = "") {
  const focus = args._.join(" ").trim();
  const gitContext = collectGitContext(args);
  const adversarial = kind === "adversarial-review";
  const reviewRoles = args.reviewRoles?.length
    ? args.reviewRoles.map((role) => role.name).join(", ")
    : "";

  if (adversarial) {
    const adversarialLenses = args.resolvedAdversarialLenses ?? resolveAdversarialLenses(args);
    return renderPromptTemplate(loadPromptTemplate(ROOT_DIR, "adversarial-review"), {
      GIT_CONTEXT: gitContext,
      GEMINI_CONTEXT: contextBlock,
      ADVERSARIAL_LENSES: adversarialLensSection(adversarialLenses),
      FOCUS: focus ? `<focus>${focus}</focus>` : "",
      OUTPUT_CONTRACT: args.jsonOutput ? adversarialJsonContract() : adversarialVerdictContract()
    });
  }

  return renderPromptTemplate(loadPromptTemplate(ROOT_DIR, "review"), {
    GIT_CONTEXT: gitContext,
    GEMINI_CONTEXT: contextBlock,
    REVIEW_ROLES: reviewRoles ? `<review_roles>${reviewRoles}</review_roles>` : "",
    FOCUS: focus ? `<focus>${focus}</focus>` : "",
    OUTPUT_CONTRACT: reviewOutputContract(args)
  });
}

function multiReviewRolePrompt(role, args, gitContext, contextBlock = "") {
  const focus = args._.join(" ").trim();

  return renderPromptTemplate(loadPromptTemplate(ROOT_DIR, "multi-review-role"), {
    ROLE_NAME: role.name,
    ROLE_DIRECTIVE: role.directive,
    GIT_CONTEXT: gitContext,
    GEMINI_CONTEXT: contextBlock,
    FOCUS: focus ? `<focus>${focus}</focus>` : ""
  });
}

function nativeAgentName(roleName) {
  return `gfc_${roleName.replace(/[^a-zA-Z0-9_]/g, "_")}`;
}

function nativeAgentMarkdown(role) {
  return [
    "---",
    `name: ${nativeAgentName(role.name)}`,
    `description: Gemini for Codex ${role.name} read-only review subagent.`,
    "---",
    "",
    `You are the ${role.name} reviewer for Gemini for Codex.`,
    "",
    "Review only in a read-only capacity. Do not edit files, do not apply fixes, and do not suggest that you are about to apply fixes.",
    "Ground every finding in concrete changed-file evidence or explicit git context.",
    "Include exact file paths and line numbers when available.",
    "If there are no findings for your role, say so and list residual risks briefly.",
    "",
    "Role directive:",
    role.directive,
    "",
    "Return:",
    "## Findings",
    "- [Severity] file:line - issue, evidence, impact, suggested direction",
    "## Open Questions",
    "## Residual Risk"
  ].join("\n");
}

function writeNativeAgentWorkspace(roles) {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "gemini-for-codex-agents-"));
  const agentsDir = path.join(tempDir, ".gemini", "agents");
  fs.mkdirSync(agentsDir, { recursive: true });
  for (const role of roles) {
    fs.writeFileSync(path.join(agentsDir, `${nativeAgentName(role.name)}.md`), nativeAgentMarkdown(role), "utf8");
  }
  return tempDir;
}

function nativeMultiAgentPrompt(args, gitContext, contextBlock = "") {
  const focus = args._.join(" ").trim();
  const agentCalls = args.reviewRoles.map((role) => `@${nativeAgentName(role.name)}`).join(", ");
  return renderPromptTemplate(loadPromptTemplate(ROOT_DIR, "native-multi-agent"), {
    SUBAGENTS: args.reviewRoles.map((role) => `${nativeAgentName(role.name)}: ${role.directive}`).join("\n"),
    AGENT_CALLS: agentCalls,
    GIT_CONTEXT: gitContext,
    GEMINI_CONTEXT: contextBlock,
    FOCUS: focus ? `<focus>${focus}</focus>` : ""
  });
}

function reviewGateRolePrompt(role, args, gitContext, contextBlock = "") {
  return renderPromptTemplate(loadPromptTemplate(ROOT_DIR, "stop-review-gate"), {
    ROLE_NAME: role.name,
    ROLE_DIRECTIVE: role.directive,
    GIT_CONTEXT: gitContext,
    GEMINI_CONTEXT: contextBlock
  });
}

function planPrompt(args) {
  const focus = args._.join(" ").trim();
  const gitContext = collectGitContext(args);

  return [
    "<task>Create an independent implementation plan for Codex to compare against its own plan.</task>",
    gitContext,
    "<rules>",
    "- Do not edit files.",
    "- Separate observed facts from inferences.",
    "- Prefer small verifiable implementation steps.",
    "- Include tests for each meaningful behavior or risk area.",
    "- Identify risks, blind spots, rollback concerns, and unresolved assumptions.",
    "- End with a reconciliation checklist Codex can use against its own plan.",
    "</rules>",
    focus ? `<planning_request>${focus}</planning_request>` : "",
    "<output_contract>",
    "## Observed Facts",
    "## Inferences",
    "## Independent Implementation Plan",
    "## Tests",
    "## Risks And Blind Spots",
    "## Reconciliation Checklist",
    "</output_contract>"
  ].filter(Boolean).join("\n");
}

function rescuePrompt(args) {
  const focus = args._.join(" ").trim();
  const gitContext = collectGitContext(args);

  return [
    "<task>Diagnose a stuck or failed Codex implementation from Gemini's independent read-only perspective.</task>",
    gitContext,
    "<rules>",
    "- Do not edit files.",
    "- Do not suggest that you are currently applying fixes.",
    "- Identify the likely failure mode, missing context, or incorrect assumption.",
    "- Prefer a short recovery checklist Codex can execute.",
    "- Ground claims in current git state, changed files, and visible evidence.",
    "- If evidence is insufficient, say exactly what Codex should inspect next.",
    "</rules>",
    focus ? `<rescue_request>${focus}</rescue_request>` : "",
    "<output_contract>",
    "## Diagnosis",
    "## Evidence",
    "## Recovery Steps",
    "## Risks",
    "</output_contract>"
  ].filter(Boolean).join("\n");
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
  return key.includes("gemini-for-codex")
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

function buildSetupReport(actionsTaken = []) {
  const cwd = process.cwd();
  const stateReport = readStateReport(cwd);
  const config = stateReport.state.config;
  const preflight = geminiHelpReport();
  const mailbox = listMailboxThreads(cwd);
  const leases = listLeases(cwd);
  return {
    node: process.version,
    geminiAvailable: hasGemini(),
    geminiCommand: geminiCommand(),
    geminiPreflight: preflight,
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
    sessionCommands: ["sessions"],
    recommendationCommands: ["recommend-execution-mode"],
    geminiCapabilities: preflight.capabilities,
    capabilities: {
      gemini: preflight.capabilities,
      requiredFlags: {
        ok: preflight.ok,
        missing: preflight.missing,
        required: preflight.requiredFlags
      },
      commands: {
        capabilities: true,
        report: true,
        releaseCheck: true,
        contextProvider: true,
        rolePacks: true
      }
    },
    rolePacks: {
      available: true,
      builtIn: listRolePacks().map((pack) => pack.name)
    },
    mailbox: {
      directory: mailboxDirForCwd(cwd),
      threadCount: mailbox.threads.length
    },
    leases: {
      directory: leasesDirForCwd(cwd),
      activeCount: leases.active.length,
      degraded: leases.degraded,
      primitive: leases.primitive
    },
    hooks: hookDiagnostics(),
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
    actionsTaken.push(`Enabled Gemini review gate for ${canonicalWorkspaceRoot(cwd)}.`);
  } else if (options.disable) {
    setConfig(cwd, "reviewGateEnabled", false);
    actionsTaken.push(`Disabled Gemini review gate for ${canonicalWorkspaceRoot(cwd)}.`);
  }
  const report = {
    ...buildSetupReport(actionsTaken)
  };

  console.log(JSON.stringify(report, null, 2));
  process.exit(report.geminiAvailable && report.geminiPreflight.ok && report.gitAvailable && report.reviewGate.stateReadable ? 0 : 1);
}

function printStatus() {
  process.stdout.write(`${JSON.stringify({
    jobs: listJobs(process.cwd()),
    reviewGate: buildSetupReport().reviewGate
  }, null, 2)}\n`);
}

function parseGithubActionsOptions(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const subcommand = tokens.shift();
  const options = {
    subcommand,
    write: false,
    force: false,
    annotations: false,
    contextProvider: "off",
    timeoutMinutes: undefined,
    releaseRef: undefined,
    model: "",
    input: ""
  };
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "--write") {
      options.write = true;
    } else if (token === "--force") {
      options.force = true;
    } else if (token === "--annotations") {
      options.annotations = true;
    } else if (token === "--context-provider") {
      options.contextProvider = readOptionValue(tokens, index, token);
      index += 1;
    } else if (token === "--timeout-minutes") {
      options.timeoutMinutes = readOptionValue(tokens, index, token);
      index += 1;
    } else if (token === "--ref") {
      options.releaseRef = readOptionValue(tokens, index, token);
      index += 1;
    } else if (token === "--model") {
      options.model = readOptionValue(tokens, index, token);
      index += 1;
    } else if (token === "--input") {
      options.input = readOptionValue(tokens, index, token);
      index += 1;
    } else {
      throw new Error(`Unknown github-actions option "${token}".`);
    }
  }
  return options;
}

function runGithubActions(rawArgs) {
  let options;
  try {
    options = parseGithubActionsOptions(rawArgs);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  if (!options.subcommand || !["render", "init", "validate", "render-comment", "render-annotations"].includes(options.subcommand)) {
    console.error("Usage: gemini-companion.mjs github-actions render|init|validate|render-comment|render-annotations [options]");
    process.exit(2);
  }
  try {
    if (options.subcommand === "render") {
      process.stdout.write(renderWorkflow(ROOT_DIR, options));
      return;
    }
    if (options.subcommand === "init") {
      const rendered = renderWorkflow(ROOT_DIR, options);
      if (!options.write) {
        process.stdout.write(`${JSON.stringify({ ok: true, written: false, path: workflowPath(process.cwd()) }, null, 2)}\n`);
        return;
      }
      const target = writeWorkflow(process.cwd(), rendered, { force: options.force });
      process.stdout.write(`${JSON.stringify({ ok: true, written: true, path: target }, null, 2)}\n`);
      return;
    }
    if (options.subcommand === "validate") {
      const target = workflowPath(process.cwd());
      const text = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : renderWorkflow(ROOT_DIR, options);
      const validation = validateWorkflow(text, options);
      process.stdout.write(`${JSON.stringify(validation, null, 2)}\n`);
      process.exit(validation.ok ? 0 : 1);
    }
    if (options.subcommand === "render-comment") {
      if (!options.input) throw new Error("Missing --input for render-comment.");
      process.stdout.write(`${renderReviewComment(readReviewJson(options.input))}\n`);
      return;
    }
    if (options.subcommand === "render-annotations") {
      if (!options.input) throw new Error("Missing --input for render-annotations.");
      process.stdout.write(`${JSON.stringify(reviewToAnnotations(readReviewJson(options.input)), null, 2)}\n`);
    }
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
}

function stripTerminalControls(text) {
  return String(text).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "");
}

function runRolesCommand(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const subcommand = tokens.shift();
  const jsonOutput = tokens.includes("--json");
  const filtered = tokens.filter((token) => token !== "--json");
  try {
    if (subcommand === "list") {
      if (filtered.length) {
        throw new Error("Usage: gemini-companion.mjs roles list [--json]");
      }
      const packs = listRolePacks();
      if (jsonOutput) {
        process.stdout.write(`${JSON.stringify({ rolePacks: packs }, null, 2)}\n`);
        return;
      }
      process.stdout.write([
        "Gemini role packs:",
        ...packs.map((pack) => `- ${pack.name}: ${pack.roles.join(", ")}${pack.gate_compatible ? " (gate-compatible)" : ""}`)
      ].join("\n") + "\n");
      return;
    }
    if (subcommand === "inspect") {
      const packName = filtered[0];
      if (!packName || filtered.length !== 1) {
        throw new Error("Usage: gemini-companion.mjs roles inspect <pack> [--json]");
      }
      const summary = rolePackSummary(resolveRolePack(packName));
      if (jsonOutput) {
        process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
        return;
      }
      process.stdout.write([
        `Role pack: ${summary.name}`,
        `source: ${summary.source}`,
        `schema: ${summary.schema_version}`,
        `roles: ${summary.roles.join(", ")}`,
        `gate-compatible: ${summary.gate_compatible ? "yes" : "no"}`,
        `native-agents-compatible: ${summary.native_agents_compatible ? "yes" : "no"}`,
        `hash: ${summary.hash}`,
        `description: ${summary.description}`
      ].join("\n") + "\n");
      return;
    }
    if (subcommand === "validate") {
      const file = filtered[0];
      if (!file || filtered.length !== 1) {
        throw new Error("Usage: gemini-companion.mjs roles validate <file> [--json]");
      }
      const pack = validateRolePackFile(file, { cwd: process.cwd(), mode: "validate" });
      const summary = rolePackSummary(pack);
      const payload = { ok: true, executable: false, reason: "User role packs are validate/inspect-only and are not executable by review commands.", rolePack: summary };
      if (jsonOutput) {
        process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
        return;
      }
      process.stdout.write(`ok: ${summary.name} validates; execution is not enabled for user-authored role packs\n`);
      return;
    }
    throw new Error("Usage: gemini-companion.mjs roles list|inspect|validate [options]");
  } catch (error) {
    console.error(stripTerminalControls(error.message || String(error)));
    process.exit(2);
  }
}

function runMailboxCommand(rawArgs) {
  const [subcommand, ...rest] = normalizeArgv(rawArgs);
  const jsonOutput = rest.includes("--json");
  try {
    if (subcommand === "list") {
      if (rest.some((token) => token !== "--json")) {
        throw new Error("Usage: gemini-companion.mjs mailbox list [--json]");
      }
      const payload = listMailboxThreads(process.cwd());
      process.stdout.write(jsonOutput ? `${JSON.stringify(payload, null, 2)}\n` : `${payload.threads.map((thread) => `${thread.threadId}: ${thread.messageCount}`).join("\n")}\n`);
      return;
    }
    if (subcommand === "show") {
      const id = rest.find((token) => token !== "--json");
      if (!id || rest.filter((token) => token !== "--json").length !== 1) {
        throw new Error("Usage: gemini-companion.mjs mailbox show <thread-or-job-id> [--json]");
      }
      const payload = showMailboxThread(process.cwd(), id);
      process.stdout.write(jsonOutput ? `${JSON.stringify(payload, null, 2)}\n` : `${payload.messages.map((message) => `${message.createdAt} ${message.role} ${message.status}: ${message.summary}`).join("\n")}\n`);
      return;
    }
    if (subcommand === "post") {
      const options = { role: "manual", summary: "", jobId: "" };
      for (let index = 0; index < rest.length; index += 1) {
        const token = rest[index];
        if (token === "--json") {
          continue;
        }
        if (token === "--job-id") {
          options.jobId = readOptionValue(rest, index, token);
          index += 1;
        } else if (token === "--summary") {
          options.summary = readOptionValue(rest, index, token);
          index += 1;
        } else if (token === "--role") {
          options.role = readOptionValue(rest, index, token);
          index += 1;
        } else {
          throw new Error(`Unknown mailbox option "${token}".`);
        }
      }
      if (!options.jobId || !options.summary) {
        throw new Error("Usage: gemini-companion.mjs mailbox post --job-id <id> --summary <text> [--role <role>] [--json]");
      }
      const payload = postMailboxMessage(process.cwd(), {
        threadId: options.jobId,
        jobId: options.jobId,
        role: options.role,
        command: "manual",
        mode: "manual",
        status: "note",
        source: "manual",
        summary: options.summary
      });
      process.stdout.write(jsonOutput ? `${JSON.stringify(payload, null, 2)}\n` : `posted: ${payload.id}\n`);
      return;
    }
    throw new Error("Usage: gemini-companion.mjs mailbox list|show|post [options]");
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
}

function runLeasesCommand(rawArgs) {
  const [subcommand, ...rest] = normalizeArgv(rawArgs);
  const jsonOutput = rest.includes("--json");
  try {
    if (subcommand === "list") {
      if (rest.some((token) => token !== "--json")) {
        throw new Error("Usage: gemini-companion.mjs leases list [--json]");
      }
      const payload = listLeases(process.cwd());
      process.stdout.write(jsonOutput ? `${JSON.stringify(payload, null, 2)}\n` : `${payload.active.map((lease) => `${lease.id}: ${lease.path} ${lease.role}`).join("\n")}\n`);
      return;
    }
    if (subcommand === "claim") {
      const options = { role: "manual", ttl: "600s", path: "", jobId: "" };
      for (let index = 0; index < rest.length; index += 1) {
        const token = rest[index];
        if (token === "--json") {
          continue;
        }
        if (token === "--path") {
          options.path = readOptionValue(rest, index, token);
          index += 1;
        } else if (token === "--role") {
          options.role = readOptionValue(rest, index, token);
          index += 1;
        } else if (token === "--ttl") {
          options.ttl = readOptionValue(rest, index, token);
          index += 1;
        } else if (token === "--job-id") {
          options.jobId = readOptionValue(rest, index, token);
          index += 1;
        } else {
          throw new Error(`Unknown leases option "${token}".`);
        }
      }
      if (!options.path) {
        throw new Error("Usage: gemini-companion.mjs leases claim --path <path> --role <role> --ttl <duration> [--job-id <id>] [--json]");
      }
      const payload = claimLease(process.cwd(), { ...options, mode: "manual" });
      process.stdout.write(jsonOutput ? `${JSON.stringify(payload, null, 2)}\n` : `${payload.status}${payload.lease ? `: ${payload.lease.id}` : ""}\n`);
      return;
    }
    if (subcommand === "release") {
      const id = rest.find((token) => token !== "--json");
      if (!id || rest.filter((token) => token !== "--json").length !== 1) {
        throw new Error("Usage: gemini-companion.mjs leases release <lease-id> [--json]");
      }
      const payload = releaseLease(process.cwd(), id);
      process.stdout.write(jsonOutput ? `${JSON.stringify(payload, null, 2)}\n` : `${payload.status}: ${id}\n`);
      return;
    }
    throw new Error("Usage: gemini-companion.mjs leases list|claim|release [options]");
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
}

function parseGateVerdict(rawOutput) {
  const text = String(rawOutput ?? "").trim();
  if (!text) {
    return { kind: "invalid", reason: "empty Gemini gate output" };
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

function validateGeminiSessionOptions(args) {
  if (args.resume && args.fresh) {
    throw new Error("Choose either --resume or --fresh, not both.");
  }
  if (args.resume) {
    requireGeminiCapability("resume", "--resume");
  }
  if (args.sessionId) {
    requireGeminiCapability("sessionId", "--session-id");
  }
  if (args.worktree) {
    requireGeminiCapability("worktree", "--worktree");
  }
}

function warnGate(message) {
  process.stderr.write(`[gemini-for-codex review-gate] ${message}\n`);
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

async function runReviewGate(rawArgs) {
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

  let roles;
  try {
    if (args.rolePack !== undefined) {
      const pack = resolveRolePack(args.rolePack);
      if (!rolePackGateCompatible(pack)) {
        process.stdout.write(`${JSON.stringify({
          decision: "block",
          reason: `Gemini review gate role pack "${pack.name}" is not gate-compatible.`
        })}\n`);
        return;
      }
      args.rolePackSummary = rolePackSummary(pack);
      args.rolePack = pack;
      roles = rolesForPack(pack);
    } else {
      roles = defaultRoleObjects();
    }
  } catch (error) {
    process.stdout.write(`${JSON.stringify({
      decision: "block",
      reason: `Gemini review gate role pack configuration error: ${error.message || String(error)}`
    })}\n`);
    return;
  }
  args.scope = "working-tree";
  args.timeout = REVIEW_GATE_ROLE_TIMEOUT_MS;

  const gitContext = collectGitContext(args);
  let context = { block: "", metadata: defaultContextMetadata() };
  if (args.contextProvider !== undefined || args.contextBudget !== undefined || args.contextTimeoutMs !== undefined || args.contextStrict) {
    try {
      context = await resolveCommandContext(args, { allowProviderErrors: true });
      if (context.warning) {
        warnGate(`context provider unavailable; running gate without context: ${context.warning}`);
      }
    } catch (error) {
      warnGate(`context provider failed before execution; running gate without context: ${error.message || String(error)}`);
      context = {
        block: "",
        metadata: { ...defaultContextMetadata(), contextStatus: "unavailable", contextFailureReason: error.reason || "unsafe-config", contextDegraded: true }
      };
    }
    if (args.contextStrict && context.metadata.contextStatus !== "available" && context.metadata.contextStatus !== "disabled") {
      warnGate(`context provider unavailable in strict mode; allowing stop without Gemini: ${context.metadata.contextFailureReason}`);
      writeOperationReport({ command: "review-gate", cwd, status: 2, geminiAvailable: hasGemini(), contextMetadata: context.metadata, rolePack: args.rolePackSummary });
      return;
    }
    if (context.metadata.contextDegraded) {
      warnGate(`context degraded (${context.metadata.contextFailureReason}); gate decision will fail open unless Gemini returns BLOCK`);
    }
  }
  const blocks = [];
  for (const role of roles) {
    const prompt = reviewGateRolePrompt(role, args, gitContext, context.block);
    const result = geminiPrint(prompt, args);
    if (result.errorCode === "ETIMEDOUT" || result.error.includes("ETIMEDOUT")) {
      warnGate(`role ${role.name} timed out; allowing stop`);
      continue;
    }
    if (result.status !== 0) {
      const detail = (result.stderr || result.error || result.stdout || "gemini review failed").trim();
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
      reason: `Gemini review gate found blocking issues: ${reason}`
    })}\n`);
    return;
  }
  setConfig(cwd, "lastAllowedReviewGateDiffHash", diffHash);
  writeOperationReport({ command: "review-gate", cwd, status: 0, geminiAvailable: hasGemini(), contextMetadata: context.metadata, rolePack: args.rolePackSummary });
}

async function runGeminiTask(kind, rawArgs) {
  if (maybeStartBackground(kind, rawArgs)) {
    return;
  }
  let args;
  try {
    args = parseArgs(rawArgs);
    if (kind !== "multi-review" && args.roles !== undefined) {
      throw new Error("--roles is only valid for multi-review; use --adversarial-lenses for adversarial-review.");
    }
    if (kind !== "multi-review" && args.rolePack !== undefined) {
      throw new Error("--role-pack is only valid for multi-review and manual review-gate.");
    }
    if (args.structuredReview && kind !== "review") {
      throw new Error("--structured is only valid for review.");
    }
    if (args.jsonOutput && kind !== "review" && kind !== "adversarial-review") {
      throw new Error("--json is only valid for review and adversarial-review.");
    }
    if (kind === "review" && args.jsonOutput && args.structuredReview) {
      throw new Error("--json and --structured cannot be combined for review.");
    }
    if (kind === "adversarial-review") {
      args.resolvedAdversarialLenses = resolveAdversarialLenses(args);
    }
    if (kind === "multi-review") {
      args.reviewRoles = resolveReviewRoles(args);
    }
    if (args.write) {
      throw new Error("Gemini for Codex is read-only; --write is not supported.");
    }
    if (args.effort) {
      throw new Error("--effort is not supported by Gemini for Codex.");
    }
    if ((kind === "plan" || kind === "rescue") && (args.contextProvider !== undefined || args.contextBudget !== undefined || args.contextTimeoutMs !== undefined || args.contextStrict)) {
      throw new Error("Context provider flags are only supported for review, adversarial-review, multi-review, and manual review-gate.");
    }
    validateGeminiSessionOptions(args);
    if (kind === "review" || kind === "adversarial-review") {
      parseContextOptions(args);
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
  let context = { block: "", metadata: defaultContextMetadata() };
  if (kind === "review" || kind === "adversarial-review") {
    try {
      context = await resolveCommandContext(args);
    } catch (error) {
      console.error(error.message || String(error));
      process.exit(2);
    }
    if (args.contextStrict && context.metadata.contextStatus !== "available" && context.metadata.contextStatus !== "disabled") {
      console.error(`Context provider unavailable in strict mode: ${context.metadata.contextFailureReason}`);
      writeOperationReport({ command: kind, cwd: process.cwd(), status: 2, geminiAvailable: hasGemini(), contextMetadata: context.metadata });
      process.exit(2);
    }
  }
  const prompt = kind === "plan" ? planPrompt(args) : kind === "rescue" ? rescuePrompt(args) : reviewPrompt(kind, args, context.block);
  const result = geminiPrint(prompt, args);
  writeOperationReport({ command: kind, cwd: process.cwd(), status: result.status, geminiAvailable: hasGemini(), contextMetadata: context.metadata });

  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.error || "gemini review failed\n");
    process.exit(result.status);
  }
  if (kind === "adversarial-review" && args.jsonOutput) {
    try {
      const parsed = validateAdversarialJson(extractJsonObject(result.stdout));
      process.stdout.write(`${JSON.stringify(parsed, null, 2)}\n`);
    } catch (error) {
      process.stderr.write(`Invalid structured adversarial output: ${error.message || String(error)}\n`);
      process.stdout.write(result.stdout);
      process.exit(1);
    }
    return;
  }
  if (kind === "review" && args.jsonOutput) {
    try {
      const parsed = validateStructuredReview(extractJsonObject(result.stdout));
      process.stdout.write(`${JSON.stringify(parsed, null, 2)}\n`);
    } catch (error) {
      process.stderr.write(`Invalid structured review output: ${error.message || String(error)}\n`);
      process.stdout.write(result.stdout);
      process.exit(1);
    }
    return;
  }
  if (kind === "review" && args.structuredReview) {
    try {
      const parsed = validateStructuredReview(extractJsonObject(result.stdout));
      process.stdout.write(`${renderStructuredReview(parsed)}\n`);
    } catch (error) {
      process.stderr.write(`Invalid structured review output: ${error.message || String(error)}\n`);
      process.stdout.write(result.stdout);
      process.exit(1);
    }
    return;
  }
  process.stdout.write(result.stdout);
}

async function runGeminiMultiReview(rawArgs) {
  if (maybeStartBackground("multi-review", rawArgs)) {
    return;
  }
  let args;
  try {
    args = parseArgs(rawArgs);
    if (args.write) {
      throw new Error("Gemini for Codex is read-only; --write is not supported.");
    }
    if (args.effort) {
      throw new Error("--effort is not supported by Gemini for Codex.");
    }
    parseContextOptions(args);
    if (args.structuredReview) {
      throw new Error("--structured is only valid for review.");
    }
    validateGeminiSessionOptions(args);
    args.reviewRoles = (args.roles === undefined && args.rolePack === undefined)
      ? defaultRoleObjects()
      : resolveReviewRoles(args);
    if (args.nativeAgents && args.rolePackSummary) {
      if (!rolePackNativeAgentsCompatible(args.rolePack)) {
        throw new Error(`Role pack "${args.rolePack.name}" is not compatible with Gemini native agents.`);
      }
      const maxNativeAgents = args.rolePack.limits?.max_native_agents;
      if (Number.isInteger(maxNativeAgents) && args.reviewRoles.length > maxNativeAgents) {
        throw new Error(`Role pack "${args.rolePack.name}" has ${args.reviewRoles.length} roles, exceeding max_native_agents ${maxNativeAgents}.`);
      }
      if (!currentGeminiCapabilities().includeDirectories) {
        throw new Error("Gemini native role-pack review requires Gemini CLI --include-directories support.");
      }
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

  const gitContext = collectGitContext(args);
  let context = { block: "", metadata: defaultContextMetadata() };
  try {
    context = await resolveCommandContext(args);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  if (args.contextStrict && context.metadata.contextStatus !== "available" && context.metadata.contextStatus !== "disabled") {
    console.error(`Context provider unavailable in strict mode: ${context.metadata.contextFailureReason}`);
    writeOperationReport({ command: "multi-review", cwd: process.cwd(), status: 2, geminiAvailable: hasGemini(), contextMetadata: context.metadata, rolePack: args.rolePackSummary });
    process.exit(2);
  }
  let results;
  let nativeWorkspace = "";
  const mailboxThreadId = args.useMailbox ? `thread-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 8)}` : "";
  const leaseResults = [];
  let mailboxWriteFailures = 0;
  if (args.advisoryLeases) {
    if (!args.paths.length) {
      process.stderr.write("[gemini-for-codex leases] --advisory-leases supplied without --path; skipping leases.\n");
    } else {
      for (const leasePath of args.paths) {
        try {
          leaseResults.push(claimLease(process.cwd(), {
            path: leasePath,
            role: args.nativeAgents ? "native-gemini-subagents" : args.reviewRoles[0]?.name || "multi-review",
            ttl: "600s",
            mode: args.nativeAgents ? "native-agents" : "plugin-managed"
          }));
        } catch (error) {
          leaseResults.push({ status: "degraded", degraded: true, reason: error.message || String(error) });
        }
      }
    }
  }
  function writeMailbox(message) {
    if (!mailboxThreadId) {
      return;
    }
    try {
      postMailboxMessage(process.cwd(), {
        threadId: mailboxThreadId,
        command: "multi-review",
        source: "runtime",
        ...message
      });
    } catch (error) {
      mailboxWriteFailures += 1;
      process.stderr.write(`[gemini-for-codex mailbox] ${error.message || String(error)}\n`);
    }
  }
  function releaseClaimedLeases() {
    for (const leaseResult of leaseResults) {
      if (leaseResult.status === "claimed" && leaseResult.lease?.id) {
        try {
          const released = releaseLease(process.cwd(), leaseResult.lease.id);
          if (released.status !== "released") {
            process.stderr.write(`[gemini-for-codex leases] failed to release ${leaseResult.lease.id}: ${released.reason || released.status}\n`);
          }
        } catch (error) {
          process.stderr.write(`[gemini-for-codex leases] failed to release ${leaseResult.lease.id}: ${error.message || String(error)}\n`);
        }
      }
    }
  }
  try {
    if (args.nativeAgents) {
      try {
        writeMailbox({
          role: "native-gemini-subagents",
          mode: "native-agents",
          status: "started",
          summary: `Gemini native-agent review started for roles: ${args.reviewRoles.map((role) => role.name).join(", ")}.`
        });
        nativeWorkspace = writeNativeAgentWorkspace(args.reviewRoles);
        const result = geminiPrint(nativeMultiAgentPrompt(args, gitContext, context.block), {
          ...args,
          cwd: nativeWorkspace,
          includeDirectories: currentGeminiCapabilities().includeDirectories ? [process.cwd()] : []
        });
        results = [{ role: { name: "native-gemini-subagents" }, result }];
        writeMailbox({
          role: "native-gemini-subagents",
          mode: "native-agents",
          status: result.status === 0 ? "succeeded" : "failed",
          summary: `Gemini native-agent review ${result.status === 0 ? "succeeded" : "failed"} with exit status ${result.status}.`
        });
      } finally {
        if (nativeWorkspace) {
          fs.rmSync(nativeWorkspace, { recursive: true, force: true });
        }
      }
    } else {
      results = await Promise.all(args.reviewRoles.map(async (role) => {
        writeMailbox({
          role: role.name,
          mode: "plugin-managed",
          status: "started",
          summary: `Role ${role.name} started.`
        });
        const prompt = multiReviewRolePrompt(role, args, gitContext, context.block);
        const result = await geminiPrintAsync(prompt, args);
        writeMailbox({
          role: role.name,
          mode: "plugin-managed",
          status: result.status === 0 ? "succeeded" : "failed",
          summary: `Role ${role.name} ${result.status === 0 ? "succeeded" : "failed"} with exit status ${result.status}.`
        });
        return { role, result };
      }));
    }
  } finally {
    releaseClaimedLeases();
  }

  const succeeded = results.filter(({ result }) => result.status === 0).map(({ role }) => role.name);
  const failed = results.filter(({ result }) => result.status !== 0).map(({ role }) => role.name);
  const leaseMetadata = leaseSummary(leaseResults);
  const sections = [
    args.nativeAgents ? "# Gemini Native Subagent Review" : "# Gemini Multi-Agent Review",
    ...results.map(({ role, result }) => [
      `## Role: ${role.name}`,
      result.stdout.trim() || "(no stdout)",
      result.status === 0 ? "" : [
        "",
        `Role failed with exit status ${result.status}.`,
        result.stderr || result.error ? `stderr: ${(result.stderr || result.error).trim()}` : ""
      ].filter(Boolean).join("\n")
    ].filter(Boolean).join("\n")),
    "## Orchestration Summary",
    `roles requested: ${args.reviewRoles.map((role) => role.name).join(", ")}`,
    `orchestration: ${args.nativeAgents ? "Gemini native subagents" : "parallel Gemini CLI role fan-out"}`,
    `roles succeeded: ${succeeded.length ? succeeded.join(", ") : "(none)"}`,
    `roles failed: ${failed.length ? failed.join(", ") : "(none)"}`,
    args.advisoryLeases ? `advisory leases: ${leaseMetadata.claimed} claimed, ${leaseMetadata.conflicts} conflicts${leaseMetadata.degraded ? ", degraded" : ""}` : "",
    "exit policy: exits non-zero if any role fails; completed role output remains visible."
  ].filter(Boolean);
  if (!args.nativeAgents) {
    writeMailbox({
      role: "summary",
      mode: "plugin-managed",
      status: failed.length ? "failed" : "succeeded",
      summary: `Multi-review completed. Succeeded: ${succeeded.length}; failed: ${failed.length}.`
    });
  }
  process.stdout.write(`${sections.join("\n\n")}\n`);
  writeOperationReport({
    command: "multi-review",
    cwd: process.cwd(),
    status: failed.length ? 1 : 0,
    geminiAvailable: hasGemini(),
    contextMetadata: context.metadata,
    rolePack: args.rolePackSummary,
    mailbox: args.useMailbox ? mailboxSummary(process.cwd(), mailboxThreadId, process.env, { writeFailures: mailboxWriteFailures }) : undefined,
    leases: args.advisoryLeases ? leaseMetadata : undefined
  });
  process.exit(failed.length ? 1 : 0);
}

function printJobs() {
  process.stdout.write(`${JSON.stringify(listJobs(process.cwd()), null, 2)}\n`);
}

function printExecutionRecommendation(rawArgs) {
  process.stdout.write(`${JSON.stringify(recommendExecutionMode(rawArgs), null, 2)}\n`);
}

function printSessions() {
  const capabilities = currentGeminiCapabilities();
  if (!capabilities.listSessions) {
    process.stdout.write(`${JSON.stringify({
      available: false,
      reason: "Gemini CLI does not report --list-sessions support."
    }, null, 2)}\n`);
    process.exit(1);
  }
  const result = runGemini(["--list-sessions"]);
  process.stdout.write(`${JSON.stringify({
    available: result.status === 0,
    status: result.status,
    stdout: result.stdout,
    stderr: result.stderr || result.error
  }, null, 2)}\n`);
  process.exit(result.status === 0 ? 0 : 1);
}

function printResult(rawArgs) {
  const jobId = rawArgs[0];
  if (!jobId) {
    console.error("Usage: gemini-companion.mjs result <job-id>");
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
    console.error("Usage: gemini-companion.mjs cancel <job-id>");
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
      GEMINI_FOR_CODEX_JOB_WORKER: "1"
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
        GEMINI_FOR_CODEX_JOB_WORKER: "1"
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
  console.error(`Usage: gemini-companion.mjs ${Array.from(VALID_COMMANDS).join("|")} [args]`);
  process.exit(2);
}

switch (command) {
  case "setup":
    printSetup(rawArgs);
    break;
  case "capabilities":
    printCapabilities();
    break;
  case "report":
    printReport(rawArgs);
    break;
  case "release-check":
    runReleaseCheck(rawArgs);
    break;
  case "github-actions":
    runGithubActions(rawArgs);
    break;
  case "roles":
    runRolesCommand(rawArgs);
    break;
  case "mailbox":
    runMailboxCommand(rawArgs);
    break;
  case "leases":
    runLeasesCommand(rawArgs);
    break;
  case "review":
    await runGeminiTask("review", rawArgs);
    break;
  case "adversarial-review":
    await runGeminiTask("adversarial-review", rawArgs);
    break;
  case "multi-review":
    await runGeminiMultiReview(rawArgs);
    break;
  case "plan":
    await runGeminiTask("plan", rawArgs);
    break;
  case "rescue":
    await runGeminiTask("rescue", rawArgs);
    break;
  case "status":
    printStatus();
    break;
  case "review-gate":
    await runReviewGate(rawArgs);
    break;
  case "jobs":
    printJobs();
    break;
  case "recommend-execution-mode":
    printExecutionRecommendation(rawArgs);
    break;
  case "sessions":
    printSessions();
    break;
  case "result":
    printResult(rawArgs);
    break;
  case "cancel":
    runCancel(rawArgs);
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
