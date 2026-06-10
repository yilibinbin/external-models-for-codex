#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import {
  cancelJob,
  canStartBackgroundJob,
  claimJobForRun,
  claimReservedJob,
  createJob,
  enrichJobLifecycle,
  findActiveJobByIdempotencyKey,
  finishJob,
  listJobs,
  recordJobHeartbeat,
  recordJobProgress,
  reapLostJobs,
  readJob,
  reserveJob,
  resultForJob,
  updateJob,
  withWorkspaceJobLock
} from "./lib/jobs.mjs";
import {
  DEFAULT_BACKGROUND_WAIT_MS,
  DEFAULT_MAX_ACTIVE_JOBS,
  HARD_JOB_TIMEOUT_MS,
  JOB_HEARTBEAT_INTERVAL_MS,
  MAX_BACKGROUND_WAIT_MS,
  MAX_STORED_OUTPUT_BYTES,
  deriveJobIdempotencyKey,
  gitSignalTimeoutMs,
  isTerminalJobStatus,
  parsePositiveInteger
} from "./lib/job-lifecycle.mjs";
import { progressEventsFromLines } from "./lib/progress.mjs";
import { createGitMcpConfig } from "./lib/mcp-config.mjs";
import { postMailboxMessage, listMailboxThreads, showMailboxThread } from "./lib/mailbox.mjs";
import { claimLease, listLeases, releaseLease } from "./lib/leases.mjs";
import { renderPromptTemplate } from "./lib/prompt-template.mjs";
import { latestReport, listReports, reportFromResult, safeWriteReport } from "./lib/reports.mjs";
import { runReleaseCheck } from "./lib/release-check.mjs";
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
  backendCapabilities,
  buildDenyToolsAfterOmission,
  configuredWriteDenyTools,
  denyToolsDiagnosticEnv,
  formatDenyToolsForCli,
  nonModelStdoutDiagnostic,
  parseUnknownDenyToolFailure,
  resolveBackend,
  runSdkNativeReview,
  runSdkPrompt
} from "./lib/claude-backend.mjs";
import {
  buildNativeReviewAgents,
  nativeReviewTeamPrompt
} from "./lib/claude-native-review.mjs";
import {
  aggregateRoleReviewOutputs,
  normalizeAdversarialOutput,
  normalizeReviewOutput
} from "./lib/render-review.mjs";
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
  rolePackSummary,
  rolesForPack,
  userRolePackDir,
  validateBuiltInRolePacks,
  validateRolePackFile
} from "./lib/role-packs.mjs";
import {
  buildSemanticContext,
  parseSemanticOptions,
  semanticCapabilities,
  validateSemanticArgs
} from "./lib/semantic-context.mjs";
import {
  QUALITY_ENV,
  VALID_QUALITIES,
  VALID_EFFORTS,
  applyQualityPolicy,
  assertValidQuality,
  assertSafeModelAliasOrId,
  assertValidEffort
} from "./lib/quality-policy.mjs";
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
import { gitCommandTimedOut } from "./lib/git-timeout.mjs";
import {
  captureProcessGroupIdentity,
  currentProcessPlatform,
  isProcessAlive,
  processGroupHasLiveMembers,
  supportsPosixProcessGroups,
  validateProcessGroupLeader
} from "./lib/process.mjs";
import {
  hookFingerprintOptions,
  workingTreeFingerprint,
  workingTreeFingerprintDetails,
  workingTreeFingerprintMatches
} from "./lib/worktree-fingerprint.mjs";

const VALID_COMMANDS = new Set(["setup", "capabilities", "review", "adversarial-review", "multi-review", "ultrareview", "plan", "status", "review-gate", "jobs", "result", "cancel", "rescue", "report", "release-check", "github-actions", "roles", "mailbox", "leases", "recommend-execution-mode", "__run-job", "reserve-job", "run-reserved-job", "subagent-command"]);
const BACKGROUND_CAPABLE_COMMANDS = new Set(["review", "adversarial-review", "multi-review", "rescue"]);
const SUBAGENT_DELEGATABLE_COMMANDS = new Set(["review", "adversarial-review", "multi-review", "rescue"]);
const VALID_AGENT_TEAMS = new Set(["plugin", "sdk-subagents"]);
const POSITIVE_DECIMAL_PATTERN = /^(?:[1-9]\d*(?:\.\d+)?|0\.\d*[1-9]\d*)$/;
const VALID_SCOPES = new Set(["auto", "working-tree", "branch"]);
const VALID_REVIEW_GATE_MODES = new Set(["multi-role"]);
const CLAUDE_CODE_PATH_ENV = "CLAUDE_CODE_PATH";
const REVIEW_GATE_ENV = "CLAUDE_FOR_CODEX_REVIEW_GATE";
const REVIEW_GATE_TIMEOUT_MS = 15 * 60 * 1000;
const REVIEW_GATE_ROLE_TIMEOUT_MS = 2 * 60 * 1000;
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
const BACKGROUND_EXECUTION_CONTROL_ENVS = Object.freeze([
  CLAUDE_CODE_PATH_ENV,
  QUALITY_ENV,
  "CLAUDE_FOR_CODEX_BACKEND",
  "CLAUDE_FOR_CODEX_DENY_TOOLS",
  "CLAUDE_FOR_CODEX_KEEP_MCP_CONFIG",
  "CLAUDE_FOR_CODEX_ROLE_PACK_DIR",
  "CLAUDE_FOR_CODEX_SDK_MODULE"
]);
const STRUCTURED_REVIEW_FINDING_SCHEMA = Object.freeze({
  type: "object",
  additionalProperties: false,
  required: ["severity", "title", "body", "file", "line_start", "line_end", "confidence", "recommendation"],
  properties: {
    severity: { type: "string", enum: ["critical", "high", "medium", "low"] },
    title: { type: "string" },
    body: { type: "string" },
    file: { type: "string" },
    line_start: { type: "integer", minimum: 1 },
    line_end: { type: "integer", minimum: 1 },
    confidence: { type: "number", minimum: 0, maximum: 1 },
    recommendation: { type: "string" }
  }
});

const STRUCTURED_REVIEW_SCHEMA = Object.freeze({
  type: "object",
  additionalProperties: false,
  required: ["verdict", "summary", "findings", "next_steps"],
  properties: {
    verdict: { type: "string", enum: ["approve", "needs-attention"] },
    summary: { type: "string" },
    findings: {
      type: "array",
      items: STRUCTURED_REVIEW_FINDING_SCHEMA
    },
    next_steps: {
      type: "array",
      items: { type: "string" }
    }
  }
});

const SDK_MULTI_REVIEW_OUTPUT_SCHEMA = Object.freeze({
  type: "object",
  additionalProperties: false,
  required: ["role_results"],
  properties: {
    role_results: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["role", "result"],
        properties: {
          agent: { type: "string" },
          role: { type: "string" },
          result: {
            type: "object",
            additionalProperties: false,
            required: ["status"],
            properties: {
              status: { type: "string", enum: ["ok", "failed"] },
              review: STRUCTURED_REVIEW_SCHEMA,
              error: { type: "string" }
            }
          }
        }
      }
    }
  }
});

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? process.cwd(),
    env: options.env ? { ...process.env, ...options.env } : process.env,
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

function claudeSubcommandHelpText(args) {
  const result = runClaude(args, { timeout: 5000 });
  return result.status === 0 ? result.stdout : "";
}

function flagSupport(helpText, flags) {
  return Object.fromEntries(flags.map((flag) => [flag, helpText.includes(flag)]));
}

function nativeClaudeCliCapabilities(helpText = claudeHelpText(), { probeSubcommands = false } = {}) {
  const agentsHelp = probeSubcommands ? claudeSubcommandHelpText(["agents", "--help"]) : "";
  const ultrareviewHelp = probeSubcommands ? claudeSubcommandHelpText(["ultrareview", "--help"]) : "";
  return {
    nativeAgents: {
      agentFlag: helpText.includes("--agent "),
      agentsJson: helpText.includes("--agents"),
      agentsCommand: agentsHelp.includes("claude agents") || agentsHelp.includes("Usage:")
    },
    structuredOutput: {
      jsonSchema: helpText.includes("--json-schema")
    },
    streaming: {
      streamJson: helpText.includes("stream-json"),
      includePartialMessages: helpText.includes("--include-partial-messages")
    },
    sessions: {
      resume: helpText.includes("--resume"),
      continue: helpText.includes("--continue"),
      sessionId: helpText.includes("--session-id"),
      forkSession: helpText.includes("--fork-session")
    },
    budget: {
      maxBudgetUsd: helpText.includes("--max-budget-usd"),
      fallbackModel: helpText.includes("--fallback-model")
    },
    ultrareview: {
      available: ultrareviewHelp.includes("claude ultrareview") || ultrareviewHelp.includes("Usage:"),
      json: ultrareviewHelp.includes("--json"),
      timeout: ultrareviewHelp.includes("--timeout")
    }
  };
}

function semanticProviderCandidates() {
  return semanticCapabilities(process.cwd(), process.env).providers;
}

function sdkAvailability() {
  const capabilities = backendCapabilities(process.env, process.cwd()).claudeSdk;
  return {
    ...capabilities,
    error: capabilities.available ? "" : "SDK package not resolved"
  };
}

function buildCapabilitiesReport({ probeNativeSubcommands = false } = {}) {
  const helpText = claudeHelpText();
  const nativeCapabilities = nativeClaudeCliCapabilities(helpText, {
    probeSubcommands: probeNativeSubcommands
  });
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
      ]),
      ...nativeCapabilities
    },
    claudeSdk: sdkAvailability(),
    backend: backendCapabilities(process.env, process.cwd()),
    qualityPolicy: {
      defaultQualityEnv: QUALITY_ENV,
      qualities: VALID_QUALITIES,
      efforts: VALID_EFFORTS,
      ultracodeEffortSupported: false,
      ultrareviewAutomatic: false
    },
    git: commandVersion("git"),
    githubCli: commandVersion("gh"),
    hooks: hookDiagnostics(),
    mcp: mcpDiagnostics(),
    rolePacks: {
      builtIn: listRolePacks(),
      userPackDirectory: userRolePackDir(process.env),
      userPackExecution: false
    },
    mailbox: {
      threads: listMailboxThreads(process.cwd(), process.env).threads.length
    },
    leases: {
      active: listLeases(process.cwd(), process.env).leases.length
    },
    semanticProviders: semanticProviderCandidates(),
    semanticContext: semanticCapabilities(process.cwd(), process.env)
  };
}

function hasHead(runGit = git) {
  return runGit(["rev-parse", "--verify", "HEAD"]).status === 0;
}

function hasBaseRef(base, runGit = git) {
  return runGit(["rev-parse", "--verify", `${base}^{commit}`]).status === 0;
}

function parseArgs(argv, options = {}) {
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
      const pathValue = readOptionValue(tokens, index, arg);
      parsed.paths.push(pathValue);
      parsed.path = pathValue;
      index += 1;
    } else if (arg === "--model") {
      parsed.model = assertSafeModelAliasOrId(readOptionValue(tokens, index, arg));
      index += 1;
    } else if (arg === "--effort") {
      parsed.effort = assertValidEffort(readOptionValue(tokens, index, arg));
      index += 1;
    } else if (arg === "--quality") {
      parsed.quality = readOptionValue(tokens, index, arg).trim().toLowerCase();
      if (!VALID_QUALITIES.includes(parsed.quality)) {
        throw new Error(`Invalid --quality "${parsed.quality}". Valid values: ${VALID_QUALITIES.join(", ")}.`);
      }
      index += 1;
    } else if (arg.startsWith("--backend=")) {
      parsed.backend = arg.slice("--backend=".length).trim();
      if (!parsed.backend) {
        throw new Error("Missing value for --backend.");
      }
    } else if (arg === "--backend") {
      parsed.backend = readOptionValue(tokens, index, arg);
      index += 1;
    } else if (arg === "--agent-team") {
      parsed.agentTeam = readOptionValue(tokens, index, arg).trim();
      if (!parsed.agentTeam) {
        throw new Error("Missing value for --agent-team.");
      }
      index += 1;
    } else if (arg === "--native-structured") {
      parsed.nativeStructured = true;
    } else if (arg === "--stream-progress") {
      parsed.streamProgress = true;
    } else if (arg === "--max-budget-usd") {
      const value = readOptionValue(tokens, index, arg).trim();
      if (!POSITIVE_DECIMAL_PATTERN.test(value)) {
        throw new Error("--max-budget-usd must be a positive decimal number.");
      }
      parsed.maxBudgetUsd = value;
      index += 1;
    } else if (arg === "--fallback-model") {
      parsed.fallbackModel = readOptionValue(tokens, index, arg).trim();
      if (!parsed.fallbackModel) {
        throw new Error("Missing value for --fallback-model.");
      }
      index += 1;
    } else if (arg === "--confirm-cost") {
      parsed.confirmCost = true;
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
    } else if (arg === "--wait-timeout-ms") {
      parsed.waitTimeoutMs = Number(readOptionValue(tokens, index, arg));
      index += 1;
    } else if (arg.startsWith("--wait-timeout-ms=")) {
      parsed.waitTimeoutMs = Number(arg.slice("--wait-timeout-ms=".length));
    } else if (arg === "--json" || arg === "--json-output") {
      parsed.jsonOutput = true;
    } else if (arg === "--write") {
      parsed.write = true;
    } else if (arg === "--parallel") {
      parsed.parallel = true;
    } else if (arg === "--sequential") {
      parsed.sequential = true;
    } else if (arg === "--use-mailbox") {
      parsed.useMailbox = true;
    } else if (arg === "--advisory-leases") {
      parsed.advisoryLeases = true;
    } else {
      const semanticIndex = parseSemanticOptions(tokens, index, parsed);
      if (semanticIndex !== index) {
        index = semanticIndex;
        continue;
      }
      if (options.rejectUnknownOptions && arg.startsWith("-")) {
        throw new Error(`Unsupported option ${arg}`);
      }
      parsed._.push(arg);
    }
  }
  if (parsed.parallel && parsed.sequential) {
    throw new Error("Choose either --parallel or --sequential.");
  }
  if (parsed.waitTimeoutMs !== undefined && (!Number.isFinite(parsed.waitTimeoutMs) || parsed.waitTimeoutMs < 0)) {
    throw new Error("--wait-timeout-ms must be a non-negative number.");
  }
  return parsed;
}

const MULTI_REVIEW_ONLY_OPTIONS = Object.freeze([
  ["agentTeam", "--agent-team"],
  ["nativeStructured", "--native-structured"],
  ["maxBudgetUsd", "--max-budget-usd"],
  ["fallbackModel", "--fallback-model"]
]);
const STREAM_PROGRESS_COMMANDS = new Set(["review", "adversarial-review", "multi-review", "rescue"]);
const PROMPT_COMMANDS = new Set(["review", "adversarial-review", "multi-review", "plan", "rescue"]);

function validateNativeModeOptions(args, command) {
  for (const [property, option] of MULTI_REVIEW_ONLY_OPTIONS) {
    if (args[property] !== undefined && command !== "multi-review") {
      throw new Error(`Unsupported option ${option}: only valid for multi-review.`);
    }
  }
  if (args.streamProgress !== undefined && !STREAM_PROGRESS_COMMANDS.has(command)) {
    throw new Error("Unsupported option --stream-progress: only valid for SDK prompt commands.");
  }
  if (args.confirmCost !== undefined && command !== "ultrareview") {
    throw new Error("Unsupported option --confirm-cost: only valid for ultrareview.");
  }
}

function validateCommandNativeModeOptions(command, rawArgs) {
  const parsed = parseArgs(rawArgs, { rejectUnknownOptions: PROMPT_COMMANDS.has(command) });
  validateNativeModeOptions(parsed, command);
  if (command === "multi-review") {
    if (parsed.agentTeam !== undefined && !VALID_AGENT_TEAMS.has(parsed.agentTeam)) {
      throw new Error(`Invalid --agent-team "${parsed.agentTeam}". Valid values: ${Array.from(VALID_AGENT_TEAMS).join(", ")}.`);
    }
    if (parsed.agentTeam === "sdk-subagents" && parsed.sequential) {
      throw new Error("--agent-team sdk-subagents cannot be combined with --sequential");
    }
  }
  return parsed;
}

function validateBackendArgs(args) {
  args.backend = resolveBackend(args, process.env);
  return args.backend;
}

const CLI_ONLY_MULTI_REVIEW_OPTIONS = Object.freeze([
  ["maxBudgetUsd", "--max-budget-usd"],
  ["fallbackModel", "--fallback-model"]
]);

function validateBackendCompatibleOptions(args) {
  if (args.backend !== "sdk") {
    return;
  }
  for (const [property, option] of CLI_ONLY_MULTI_REVIEW_OPTIONS) {
    if (args[property] !== undefined) {
      throw new Error(`Unsupported option ${option}: CLI-only and not supported for SDK backend.`);
    }
  }
}

function validateSdkSubagentsBackend(args) {
  if (args.agentTeam === "sdk-subagents" && args.backend !== "sdk") {
    throw new Error("--agent-team sdk-subagents requires --backend sdk or CLAUDE_FOR_CODEX_BACKEND=sdk.");
  }
}

function commandHelp(command) {
  const common = [
    "--scope auto|working-tree|branch",
    "--base <ref>",
    "--path <path>",
    "--model <model>",
    "--effort <effort>",
    "--quality auto|fast|standard|strong|max",
    "--backend cli|sdk",
    "--json",
    "--stream-progress"
  ];
  const help = {
    review: [
      "Usage: claude-companion.mjs review [options] [focus]",
      ...common,
      "--role <role>",
      "--roles <a,b>",
      "--semantic-context off|auto|<provider>"
    ],
    "adversarial-review": [
      "Usage: claude-companion.mjs adversarial-review [options] [focus]",
      ...common,
      "--adversarial-lens <lens>",
      "--adversarial-lenses <a,b>",
      "--parallel"
    ],
    "multi-review": [
      "Usage: claude-companion.mjs multi-review [options] [focus]",
      ...common,
      "--role <role>",
      "--roles <a,b>",
      "--role-pack <pack>",
      "--agent-team plugin|sdk-subagents",
      "--native-structured",
      "--parallel",
      "--sequential",
      "--use-mailbox",
      "--advisory-leases"
    ],
    rescue: [
      "Usage: claude-companion.mjs rescue [options] [focus]",
      ...common,
      "--write"
    ],
    plan: [
      "Usage: claude-companion.mjs plan [options] [focus]",
      ...common
    ],
    ultrareview: [
      "Usage: claude-companion.mjs ultrareview --confirm-cost [options] [target]",
      "--json",
      "--timeout <minutes>",
      "--confirm-cost"
    ],
    "review-gate": [
      "Usage: claude-companion.mjs review-gate [options]",
      "--backend cli|sdk",
      "--semantic-context off|auto|<provider>"
    ]
  };
  return help[command] ? `${help[command].join("\n")}\n` : "";
}

function maybePrintCommandHelp(command, rawArgs) {
  let tokens;
  try {
    tokens = normalizeArgv(rawArgs);
  } catch {
    return false;
  }
  if (!tokens.includes("--help") && !tokens.includes("-h")) {
    return false;
  }
  const text = commandHelp(command);
  if (!text) {
    return false;
  }
  process.stdout.write(text);
  return true;
}

function resolveReviewRoles(args) {
  if (args.rolePack !== undefined && args.roles !== undefined) {
    throw new Error("--role-pack conflicts with --roles/--role.");
  }
  if (args.rolePack !== undefined) {
    const pack = resolveRolePack(args.rolePack);
    args.rolePackSummary = rolePackSummary(pack);
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

function collectGitContext(options) {
  const runGit = options.gitRunner ?? git;
  const scope = options.scope ?? "auto";
  const base = options.base;
  const paths = options.paths?.length ? options.paths : options.path ? [options.path] : [];
  const pathLabel = paths.join(" ");
  const pathArgs = paths.length ? ["--", ...paths] : [];
  const headExists = hasHead(runGit);
  const baseExists = Boolean(base) && headExists && hasBaseRef(base, runGit);
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

  const status = includeWorkingTree ? runGit(["status", "--short", "--untracked-files=all", ...pathArgs]) : null;
  const stagedStat = includeWorkingTree ? runGit(["diff", "--cached", "--stat", ...pathArgs]) : null;
  const stagedDiff = includeWorkingTree ? runGit(["diff", "--cached", ...pathArgs]) : null;
  const unstagedStat = includeWorkingTree ? runGit(["diff", "--stat", ...pathArgs]) : null;
  const unstagedDiff = includeWorkingTree ? runGit(["diff", ...pathArgs]) : null;
  const branchStat = includeBaseBranch
    ? baseExists
      ? runGit(["diff", "--stat", `${base}...HEAD`, ...pathArgs])
      : safeResult(`(${baseIssue}; branch diff skipped)`)
    : null;
  const branchNameOnly = includeBaseBranch
    ? baseExists
      ? runGit(["diff", "--name-only", `${base}...HEAD`, ...pathArgs])
      : includeWorkingTree && status
        ? changedFilesFromStatus(status)
        : safeResult(`(${baseIssue}; branch name-only skipped)`)
    : includeHeadNameOnly && headExists
      ? runGit(["diff", "--name-only", "HEAD", ...pathArgs])
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

function qualityGitSignals(args) {
  if (git(["rev-parse", "--is-inside-work-tree"]).status !== 0) {
    return { changedFiles: 0, diffLines: 0 };
  }
  const paths = args.paths?.length ? args.paths : args.path ? [args.path] : [];
  const pathArgs = paths.length ? ["--", ...paths] : [];
  const scope = args.scope ?? "auto";
  const branchDiff = (scope === "auto" || scope === "branch") && args.base
    ? git(["diff", "--numstat", `${args.base}...HEAD`, ...pathArgs])
    : null;
  const status = git(["status", "--short", "--untracked-files=all", ...pathArgs]);
  const staged = git(["diff", "--cached", "--numstat", ...pathArgs]);
  const unstaged = git(["diff", "--numstat", ...pathArgs]);
  if (status.status !== 0 || staged.status !== 0 || unstaged.status !== 0 || (branchDiff && branchDiff.status !== 0)) {
    return { changedFiles: 0, diffLines: 0 };
  }
  const files = new Set();
  for (const line of status.stdout.split(/\r?\n/)) {
    const file = line.slice(3).trim();
    if (file) files.add(file);
  }
  let diffLines = 0;
  for (const result of [branchDiff, staged, unstaged].filter(Boolean)) {
    for (const line of result.stdout.split(/\r?\n/)) {
      const [added, deleted, file] = line.split(/\t/);
      if (file) files.add(file);
      const addedNumber = added === "-" ? 0 : Number(added || 0);
      const deletedNumber = deleted === "-" ? 0 : Number(deleted || 0);
      diffLines += (Number.isFinite(addedNumber) ? addedNumber : 0) + (Number.isFinite(deletedNumber) ? deletedNumber : 0);
    }
  }
  return { changedFiles: files.size, diffLines };
}

function applyCommandQualityPolicy(command, args) {
  const requestedQuality = args.quality ?? process.env[QUALITY_ENV] ?? "auto";
  const needsSignals = String(requestedQuality).trim().toLowerCase() === "auto";
  return applyQualityPolicy(command, args, process.env, needsSignals ? qualityGitSignals(args) : {});
}

function buildClaudePrintInvocation(prompt, options) {
  const args = [
    "--print",
    "--permission-mode",
    options.write ? "bypassPermissions" : "dontAsk",
  ];
  let mcpConfig = null;

  if (!options.write) {
    const denyTools = Array.isArray(options.denyTools)
      ? options.denyTools
      : configuredWriteDenyTools(process.env);
    const formattedDenyTools = formatDenyToolsForCli(denyTools);
    if (!formattedDenyTools) {
      throw new Error("Claude read-only review requires at least one disallowed write tool.");
    }
    mcpConfig = createGitMcpConfig(process.cwd(), process.env);
    args.push(
      "--disable-slash-commands",
      "--no-session-persistence",
      "--setting-sources",
      "",
      "--tools",
      READ_ONLY_BUILTIN_TOOLS.join(","),
      "--allowedTools",
      READ_ONLY_MCP_TOOLS.join(",")
    );
    args.push("--disallowedTools", formattedDenyTools);
    args.push(
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
  if (options.maxBudgetUsd) {
    args.push("--max-budget-usd", options.maxBudgetUsd);
  }
  if (options.fallbackModel) {
    args.push("--fallback-model", options.fallbackModel);
  }

  args.push(prompt);
  return { args, mcpConfig };
}

function logUnknownDenyRetry(candidate, remainingTools) {
  console.error(
    '[claude-for-codex] Claude runtime rejected deny rule "' + candidate + '" before review; retrying without that deny candidate. For a persistent manual override, set ' + denyToolsDiagnosticEnv(remainingTools) + '.'
  );
}

function claudeFailureDiagnostic(result) {
  return result.stderr || result.error || nonModelStdoutDiagnostic(result.stdout) || "claude --print failed\n";
}

function claudePrint(prompt, options) {
  let denyTools = configuredWriteDenyTools(process.env);
  const omitted = new Set();
  while (true) {
    // Retry may narrow only the defense-in-depth deny-list; the read-only allow-list and strict MCP boundary must remain invariant.
    const { args, mcpConfig } = buildClaudePrintInvocation(prompt, { ...options, denyTools });
    let result;
    try {
      result = runClaude(args, {
        timeout: options.timeout,
        env: options.write ? undefined : { CLAUDE_FOR_CODEX_ISOLATED_REVIEW: "1" }
      });
    } finally {
      if (mcpConfig && process.env.CLAUDE_FOR_CODEX_KEEP_MCP_CONFIG !== "1") {
        mcpConfig.cleanup();
      }
    }
    if (options.write || result.status === 0) {
      return result;
    }
    const candidate = parseUnknownDenyToolFailure(result, denyTools);
    if (!candidate || omitted.has(candidate)) {
      return result;
    }
    const remainingTools = buildDenyToolsAfterOmission(denyTools, candidate);
    if (remainingTools.length === denyTools.length || remainingTools.length === 0) {
      return result;
    }
    omitted.add(candidate);
    denyTools = remainingTools;
    logUnknownDenyRetry(candidate, denyTools);
  }
}

function runClaudeAsync(args, options = {}) {
  return new Promise((resolve) => {
    const child = spawn(claudeCommand(), args, {
      cwd: options.cwd ?? process.cwd(),
      env: options.env ? { ...process.env, ...options.env } : process.env,
      stdio: ["ignore", "pipe", "pipe"]
    });
    const stdoutChunks = [];
    const stderrChunks = [];
    let settled = false;
    let timedOut = false;
    const timeout = options.timeout
      ? setTimeout(() => {
          timedOut = true;
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
        error: timedOut ? "ETIMEDOUT" : error.message || String(error),
        errorCode: timedOut ? "ETIMEDOUT" : error.code ? String(error.code) : ""
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
        status: timedOut ? 1 : status ?? (signal ? 1 : 0),
        stdout: Buffer.concat(stdoutChunks).toString("utf8"),
        stderr: Buffer.concat(stderrChunks).toString("utf8"),
        error: timedOut ? "ETIMEDOUT" : "",
        errorCode: timedOut ? "ETIMEDOUT" : ""
      });
    });
  });
}

async function claudePrintAsync(prompt, options) {
  if (resolveBackend(options, process.env) === "sdk") {
    return runSdkPrompt(prompt, options, { cwd: process.cwd(), timeout: options.timeout });
  }
  let denyTools = configuredWriteDenyTools(process.env);
  const omitted = new Set();
  while (true) {
    // Retry may narrow only the defense-in-depth deny-list; the read-only allow-list and strict MCP boundary must remain invariant.
    const { args, mcpConfig } = buildClaudePrintInvocation(prompt, { ...options, denyTools });
    let result;
    try {
      result = await runClaudeAsync(args, {
        timeout: options.timeout,
        env: options.write ? undefined : { CLAUDE_FOR_CODEX_ISOLATED_REVIEW: "1" }
      });
    } finally {
      if (mcpConfig && process.env.CLAUDE_FOR_CODEX_KEEP_MCP_CONFIG !== "1") {
        mcpConfig.cleanup();
      }
    }
    if (options.write || result.status === 0) {
      return result;
    }
    const candidate = parseUnknownDenyToolFailure(result, denyTools);
    if (!candidate || omitted.has(candidate)) {
      return result;
    }
    const remainingTools = buildDenyToolsAfterOmission(denyTools, candidate);
    if (remainingTools.length === denyTools.length || remainingTools.length === 0) {
      return result;
    }
    omitted.add(candidate);
    denyTools = remainingTools;
    logUnknownDenyRetry(candidate, denyTools);
  }
}

function stripBackgroundArgs(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const output = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "--background" || token === "--wait") {
      continue;
    }
    if (token === "--wait-timeout-ms") {
      index += 1;
      continue;
    }
    if (token.startsWith("--wait-timeout-ms=")) {
      continue;
    }
    output.push(token);
  }
  return output;
}

function materializeBackgroundArgs(command, rawArgs) {
  const foregroundArgs = stripBackgroundArgs(rawArgs);
  if (!STREAM_PROGRESS_COMMANDS.has(command)) {
    return foregroundArgs;
  }
  const parsed = validateCommandNativeModeOptions(command, foregroundArgs);
  validateBackendArgs(parsed);
  if (parsed.backend === "sdk" && !parsed.streamProgress) {
    return [...foregroundArgs, "--stream-progress"];
  }
  return foregroundArgs;
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

function parseReservedJobRunArgs(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  let cwd = process.cwd();
  const jobTokens = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "--cwd") {
      const value = tokens[index + 1];
      if (!value || value.startsWith("--")) {
        throw new Error("Missing --cwd value.");
      }
      cwd = path.resolve(value);
      index += 1;
      continue;
    }
    if (token.startsWith("--cwd=")) {
      const value = token.slice("--cwd=".length);
      if (!value) {
        throw new Error("Missing --cwd value.");
      }
      cwd = path.resolve(value);
      continue;
    }
    jobTokens.push(token);
  }
  return { jobId: parseJobIdArg(jobTokens), cwd };
}

function shortstatFileCount(text) {
  const match = String(text ?? "").match(/(\d+)\s+files?\s+changed/);
  return match ? Number(match[1]) : 0;
}

function shortstatChangedLines(text) {
  const insertions = String(text ?? "").match(/(\d+)\s+insertions?/);
  const deletions = String(text ?? "").match(/(\d+)\s+deletions?/);
  return (insertions ? Number(insertions[1]) : 0) + (deletions ? Number(deletions[1]) : 0);
}

function gitShort(args, cwd = process.cwd(), options = {}) {
  const env = options.env ?? process.env;
  const result = run("git", args, { cwd, timeout: gitSignalTimeoutMs(env) });
  return { ...result, timedOut: gitCommandTimedOut(result) };
}

function recommendModeArgs(rawArgs) {
  const tokens = normalizeArgv(rawArgs).filter((arg) => arg !== "--json" && arg !== "--json-output");
  const args = { command: "review", base: "", paths: [] };
  let commandSeen = false;
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "--") {
      args.paths.push(...tokens.slice(index + 1).filter(Boolean));
      break;
    }
    if (token === "--base") {
      args.base = readOptionValue(tokens, index, "--base");
      index += 1;
      continue;
    }
    if (token.startsWith("--base=")) {
      args.base = token.slice("--base=".length);
      continue;
    }
    if (token === "--path") {
      args.paths.push(readOptionValue(tokens, index, "--path"));
      index += 1;
      continue;
    }
    if (token.startsWith("--path=")) {
      args.paths.push(token.slice("--path=".length));
      continue;
    }
    if (!commandSeen && BACKGROUND_CAPABLE_COMMANDS.has(token)) {
      args.command = token;
      commandSeen = true;
    }
  }
  return args;
}

function recommendExecutionMode(cwd = process.cwd(), options = {}) {
  const inside = gitShort(["rev-parse", "--is-inside-work-tree"], cwd);
  if (inside.timedOut) {
    return {
      recommendation: "background",
      reason: "git signal collection timed out; use a tracked background review or retry status later",
      reviewable: false,
      fileCountEstimate: 0,
      changedLineEstimate: 0,
      hasUntracked: false,
      git: { repository: null, timedOut: true }
    };
  }
  if (inside.status !== 0) {
    return {
      recommendation: "background",
      reason: "not a git repository; manual/background review recommended",
      reviewable: false,
      fileCountEstimate: 0,
      changedLineEstimate: 0,
      hasUntracked: false,
      git: { repository: false }
    };
  }
  const paths = Array.isArray(options.paths) ? options.paths.filter(Boolean) : [];
  const pathArgs = paths.length ? ["--", ...paths] : [];
  const base = typeof options.base === "string" && options.base.trim() ? options.base.trim() : "";
  const status = gitShort(["status", "--short", "--untracked-files=all", ...pathArgs], cwd);
  const staged = gitShort(["diff", "--shortstat", "--cached", ...pathArgs], cwd);
  const unstaged = gitShort(["diff", "--shortstat", ...pathArgs], cwd);
  const branch = base ? gitShort(["diff", "--shortstat", `${base}...HEAD`, ...pathArgs], cwd) : null;
  const branchNames = base ? gitShort(["diff", "--name-only", `${base}...HEAD`, ...pathArgs], cwd) : null;
  if (status.timedOut || staged.timedOut || unstaged.timedOut || branch?.timedOut || branchNames?.timedOut) {
    return {
      recommendation: "background",
      reason: "git signal collection timed out; use a tracked background review or retry status later",
      reviewable: false,
      fileCountEstimate: 0,
      changedLineEstimate: 0,
      hasUntracked: false,
      git: { repository: true, timedOut: true }
    };
  }
  if ((branch && branch.status !== 0) || (branchNames && branchNames.status !== 0)) {
    return {
      recommendation: "background",
      reason: `base diff unavailable for "${base}"; use tracked background review or check the base ref`,
      reviewable: false,
      fileCountEstimate: 0,
      changedLineEstimate: 0,
      hasUntracked: false,
      git: { repository: true, base, baseDiffAvailable: false }
    };
  }
  const statusLines = status.stdout.trim() ? status.stdout.trim().split(/\r?\n/) : [];
  const hasUntracked = statusLines.some((line) => line.startsWith("??"));
  const branchFileLines = branchNames?.stdout.trim() ? branchNames.stdout.trim().split(/\r?\n/).filter(Boolean) : [];
  const fileCountEstimate = Math.max(
    statusLines.length,
    shortstatFileCount(staged.stdout) + shortstatFileCount(unstaged.stdout),
    shortstatFileCount(branch?.stdout ?? ""),
    branchFileLines.length
  );
  const changedLineEstimate = shortstatChangedLines(staged.stdout)
    + shortstatChangedLines(unstaged.stdout)
    + shortstatChangedLines(branch?.stdout ?? "");
  const command = options.command ?? "review";
  const forcedBackground = command === "multi-review" || command === "adversarial-review" || command === "rescue";
  const broad = hasUntracked || fileCountEstimate > 2 || changedLineEstimate > 50;
  const recommendation = forcedBackground || broad ? "background" : "foreground";
  return {
    recommendation,
    reason: recommendation === "background" ? "review appears broader than a small foreground review" : "small review scope",
    reviewable: statusLines.length > 0 || fileCountEstimate > 0,
    fileCountEstimate,
    changedLineEstimate,
    hasUntracked,
    git: { repository: true, ...(base ? { base, baseDiffAvailable: true } : {}) }
  };
}

function heartbeatIntervalMs(env = process.env) {
  return parsePositiveInteger(env.CLAUDE_FOR_CODEX_HEARTBEAT_INTERVAL_MS, JOB_HEARTBEAT_INTERVAL_MS, {
    min: 50,
    max: 60_000
  });
}

function hardJobTimeoutMs(env = process.env) {
  return parsePositiveInteger(env.CLAUDE_FOR_CODEX_HARD_TIMEOUT_MS, HARD_JOB_TIMEOUT_MS, {
    min: 1_000,
    max: 24 * 60 * 60 * 1000
  });
}

function killGraceMs(env = process.env) {
  return parsePositiveInteger(env.CLAUDE_FOR_CODEX_KILL_GRACE_MS, 5_000, {
    min: 50,
    max: 60_000
  });
}

function maxActiveJobs(env = process.env) {
  return parsePositiveInteger(env.CLAUDE_FOR_CODEX_MAX_ACTIVE_JOBS, DEFAULT_MAX_ACTIVE_JOBS, {
    min: 1,
    max: 20
  });
}

function reviewGateTimeoutMs(env = process.env) {
  return parsePositiveInteger(env.CLAUDE_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS, REVIEW_GATE_TIMEOUT_MS, {
    min: 1_000,
    max: 30 * 60 * 1000
  });
}

function workerNodeBinary(env = process.env) {
  return env.CLAUDE_FOR_CODEX_WORKER_NODE || process.execPath;
}

function backgroundPlatformSupport(env = process.env) {
  const platform = currentProcessPlatform(env);
  if (supportsPosixProcessGroups(platform)) {
    return { ok: true, platform };
  }
  return {
    ok: false,
    platform,
    message: `Claude for Codex background jobs require POSIX process groups; platform "${platform}" is not supported. Run a foreground command or use a POSIX environment.`
  };
}

function assertBackgroundPlatformSupported(env = process.env) {
  const support = backgroundPlatformSupport(env);
  if (!support.ok) {
    throw new Error(support.message);
  }
  return support;
}

function backgroundExecutionControls(env = process.env) {
  const controls = Object.fromEntries(
    BACKGROUND_EXECUTION_CONTROL_ENVS.map((name) => [name, String(env[name] ?? "")])
  );
  controls.claudeCommand = claudeCommand();
  return controls;
}

function makeProgressLineBuffer(onLine) {
  let buffer = "";
  return {
    push(chunk) {
      buffer += Buffer.from(chunk).toString("utf8");
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        onLine(line);
      }
    },
    flush() {
      if (buffer) {
        onLine(buffer);
        buffer = "";
      }
    }
  };
}

function makeCappedOutputAccumulator(maxBytes = MAX_STORED_OUTPUT_BYTES) {
  const chunks = [];
  let totalBytes = 0;
  let storedBytes = 0;
  let truncated = false;
  return {
    push(chunk) {
      const buffer = Buffer.from(chunk);
      totalBytes += buffer.length;
      const remaining = maxBytes - storedBytes;
      if (remaining <= 0) {
        truncated = true;
        return;
      }
      if (buffer.length > remaining) {
        chunks.push(buffer.subarray(0, remaining));
        storedBytes += remaining;
        truncated = true;
        return;
      }
      chunks.push(buffer);
      storedBytes += buffer.length;
    },
    text() {
      return Buffer.concat(chunks, storedBytes).toString("utf8");
    },
    metadata() {
      return { bytes: totalBytes, storedBytes, truncated };
    }
  };
}

function startBackgroundJob(command, rawArgs) {
  if (!BACKGROUND_CAPABLE_COMMANDS.has(command)) {
    console.error(`${command} does not support --background.`);
    process.exit(2);
  }
  const support = backgroundPlatformSupport(process.env);
  if (!support.ok) {
    return { unsupportedPlatform: true, platform: support.platform, message: support.message };
  }
  const cwd = process.cwd();
  const foregroundArgs = materializeBackgroundArgs(command, rawArgs);
  const session = readJson(currentSessionFileForCwd(cwd), {});
  const fingerprint = workingTreeFingerprintDetails(cwd, foregroundArgs);
  const executionControls = backgroundExecutionControls(process.env);
  return withWorkspaceJobLock(cwd, process.env, () => {
    reapLostJobs(cwd);
    const idempotencyKey = fingerprint.timedOut
      ? ""
      : deriveJobIdempotencyKey({
        command,
        args: foregroundArgs,
        cwd,
        workspaceFingerprint: fingerprint.hash,
        executionControls
      });
    const existing = idempotencyKey ? findActiveJobByIdempotencyKey(cwd, idempotencyKey) : null;
    if (existing) {
      const existingIsQueuedHostForwardedReservation = existing.reservationMode === "host-forwarded"
        && Array.isArray(existing.workerCommand)
        && existing.status === "queued";
      // A queued host-forwarded reservation is waiting for a Codex subagent to
      // claim it; a direct --background request must start its own worker.
      if (!existingIsQueuedHostForwardedReservation) {
        return { ...existing, reusedExisting: true };
      }
    }
    const capacity = canStartBackgroundJob(cwd, process.env, maxActiveJobs());
    if (!capacity.ok) {
      return { capacityBlocked: true, capacity };
    }
    const job = createJob(cwd, {
      command,
      args: foregroundArgs,
      cwd,
      sessionId: session?.sessionId || "",
      submissionState: "starting",
      idempotencyKey,
      fingerprintTimedOut: fingerprint.timedOut
    });
    const workerNode = workerNodeBinary(process.env);
    if (!fs.existsSync(workerNode)) {
      const failed = finishJob(cwd, job.id, {
        status: 1,
        stdout: "",
        stderr: "",
        error: `Worker node binary not found: ${workerNode}`
      });
      return { launchFailed: true, job: failed ?? readJob(cwd, job.id) ?? job };
    }
    let child;
    try {
      child = spawn(workerNode, [process.argv[1], "__run-job", job.id], {
        cwd,
        env: process.env,
        detached: true,
        stdio: "ignore"
      });
    } catch (error) {
      const failed = finishJob(cwd, job.id, {
        status: 1,
        stdout: "",
        stderr: "",
        error: error.message || String(error)
      });
      return { launchFailed: true, job: failed ?? readJob(cwd, job.id) ?? job };
    }
    child.once("error", (error) => {
      finishJob(cwd, job.id, {
        status: 1,
        stdout: "",
        stderr: "",
        error: error.message || String(error)
      });
    });
    if (!child.pid) {
      const failed = finishJob(cwd, job.id, {
        status: 1,
        stdout: "",
        stderr: "",
        error: "Worker process did not expose a pid."
      });
      return { launchFailed: true, job: failed ?? readJob(cwd, job.id) ?? job };
    }
    child.unref();
    return updateJob(cwd, job.id, {
      workerPid: child.pid
    }) ?? job;
  });
}

function reserveBackgroundJob(command, commandArgs, workerCommand, jobId) {
  const cwd = process.cwd();
  const session = readJson(currentSessionFileForCwd(cwd), {});
  const fingerprint = workingTreeFingerprintDetails(cwd, commandArgs);
  const executionControls = backgroundExecutionControls(process.env);
  return withWorkspaceJobLock(cwd, process.env, () => {
    reapLostJobs(cwd);
    const idempotencyKey = fingerprint.timedOut
      ? ""
      : deriveJobIdempotencyKey({
        command,
        args: commandArgs,
        cwd,
        workspaceFingerprint: fingerprint.hash,
        executionControls
      });
    const existing = idempotencyKey ? findActiveJobByIdempotencyKey(cwd, idempotencyKey) : null;
    if (existing) {
      if (existing.reservationMode !== "host-forwarded" || !Array.isArray(existing.workerCommand)) {
        return { alreadyRunning: true, job: existing };
      }
      if (existing.status !== "queued") {
        return { alreadyRunning: true, job: existing };
      }
      return { reusedExisting: true, job: existing };
    }
    const capacity = canStartBackgroundJob(cwd, process.env, maxActiveJobs());
    if (!capacity.ok) {
      return { capacityBlocked: true, capacity };
    }
    const job = reserveJob(cwd, {
      id: jobId,
      command,
      args: commandArgs,
      cwd,
      focus: commandArgs.join(" "),
      sessionId: session?.sessionId || "",
      submissionState: "starting",
      idempotencyKey,
      fingerprintTimedOut: fingerprint.timedOut
    }, workerCommand);
    return { job };
  });
}

function elapsedMs(job) {
  const start = Date.parse(job.startedAt ?? job.createdAt ?? "");
  if (!Number.isFinite(start)) {
    return null;
  }
  const terminal = isTerminalJobStatus(job.status);
  const end = terminal
    ? Date.parse(job.finishedAt ?? job.updatedAt ?? "")
    : Date.now();
  return Math.max(0, (Number.isFinite(end) ? end : Date.now()) - start);
}

function progressPreview(job) {
  return typeof job.lastProgressMessage === "string" && job.lastProgressMessage.trim()
    ? [job.lastProgressMessage]
    : [];
}

function enrichCompanionJob(job) {
  return { ...enrichJobLifecycle(job), elapsedMs: elapsedMs(job), progressPreview: progressPreview(job) };
}

function isExpectedActiveWaitStatus(status) {
  return status === "queued" || status === "running";
}

async function waitForJob(jobId, options = {}) {
  const started = Date.now();
  const cwd = process.cwd();
  const timeoutMs = parsePositiveInteger(options.timeoutMs, DEFAULT_BACKGROUND_WAIT_MS, {
    min: 0,
    max: MAX_BACKGROUND_WAIT_MS
  });
  let nextReapAt = 0;
  const maybeReapLostJobs = () => {
    const now = Date.now();
    if (now < nextReapAt) {
      return;
    }
    reapLostJobs(cwd);
    nextReapAt = now + 5_000;
  };
  while (Date.now() - started < timeoutMs) {
    maybeReapLostJobs();
    const job = readJob(cwd, jobId);
    if (job && isTerminalJobStatus(job.status)) {
      return { job, waitTimedOut: false };
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  reapLostJobs(cwd);
  const job = readJob(cwd, jobId);
  if (job && isTerminalJobStatus(job.status)) {
    return { job, waitTimedOut: false };
  }
  return {
    job: job ?? { id: jobId, status: "unknown" },
    waitTimedOut: true
  };
}

async function maybeStartBackground(command, rawArgs) {
  let parsed;
  try {
    parsed = validateCommandNativeModeOptions(command, rawArgs);
  } catch {
    return false;
  }
  if (!parsed.background) {
    return false;
  }
  const job = startBackgroundJob(command, rawArgs);
  if (job?.unsupportedPlatform) {
    process.stdout.write(`${JSON.stringify({
      status: "unsupported_platform",
      platform: job.platform,
      message: job.message
    }, null, 2)}\n`);
    process.exit(2);
  }
  if (job?.status === "workspace_locked") {
    process.stdout.write(`${JSON.stringify({
      status: "workspace_locked",
      message: job.reason ?? "Claude for Codex workspace job state is busy; retry later."
    }, null, 2)}\n`);
    process.exit(2);
  }
  if (job?.capacityBlocked) {
    process.stdout.write(`${JSON.stringify({
      status: "capacity_blocked",
      activeCount: job.capacity.activeCount,
      limit: job.capacity.limit,
      message: "Claude for Codex already has the maximum number of active background jobs. Use jobs/result/cancel before starting another."
    }, null, 2)}\n`);
    process.exit(2);
  }
  if (job?.launchFailed) {
    process.stdout.write(`${JSON.stringify({
      status: "launch_failed",
      job: enrichCompanionJob(job.job),
      message: "Claude for Codex could not start the background worker; the job was marked failed and will not occupy active capacity."
    }, null, 2)}\n`);
    process.exit(1);
  }
  if (parsed.wait) {
    const waited = await waitForJob(job.id, { timeoutMs: parsed.waitTimeoutMs });
    const stillRunning = waited.waitTimedOut && isExpectedActiveWaitStatus(waited.job.status);
    const responseJob = {
      ...waited.job,
      ...(job.reusedExisting ? { reusedExisting: true } : {})
    };
    process.stdout.write(`${JSON.stringify({
      status: stillRunning ? "running" : waited.job.status,
      waitTimedOut: waited.waitTimedOut,
      job: enrichCompanionJob(responseJob)
    }, null, 2)}\n`);
    process.exit(waited.job.status === "succeeded" || stillRunning ? 0 : 1);
  }
  process.stdout.write(`${JSON.stringify({ status: job.reusedExisting ? job.status : "queued", job: enrichCompanionJob(job) }, null, 2)}\n`);
  process.exit(0);
}

function handleReserveJob(rawArgs) {
  assertBackgroundPlatformSupported(process.env);
  const tokens = normalizeArgv(rawArgs);
  const command = tokens[0];
  if (!command) {
    throw new Error("Missing command to reserve.");
  }
  if (!BACKGROUND_CAPABLE_COMMANDS.has(command)) {
    throw new Error(`Command "${command}" cannot be reserved as a background job.`);
  }
  const commandArgs = materializeBackgroundArgs(command, tokens.slice(1));
  const parsed = validateCommandNativeModeOptions(command, commandArgs);
  validateBackendArgs(parsed);
  validateBackendCompatibleOptions(parsed);
  validateSdkSubagentsBackend(parsed);
  const jobId = `job-${randomUUID()}`;
  const workerCommand = [
    process.execPath,
    path.resolve(process.argv[1]),
    "run-reserved-job",
    "--job-id",
    jobId,
    "--cwd",
    process.cwd()
  ];
  const reserved = reserveBackgroundJob(command, commandArgs, workerCommand, jobId);
  if (reserved?.status === "workspace_locked") {
    return {
      status: "workspace_locked",
      message: reserved.reason ?? "Claude for Codex workspace job state is busy; retry later."
    };
  }
  if (reserved?.capacityBlocked) {
    return {
      status: "capacity_blocked",
      activeCount: reserved.capacity.activeCount,
      limit: reserved.capacity.limit,
      message: "Claude for Codex already has the maximum number of active background jobs. Use jobs/result/cancel before reserving another."
    };
  }
  if (reserved?.alreadyRunning) {
    return {
      status: "running",
      reusedExisting: true,
      job: {
        id: reserved.job.id,
        status: reserved.job.status,
        command: reserved.job.command,
        args: reserved.job.args ?? []
      },
      message: "An active background job already covers this request; do not dispatch a forwarding subagent."
    };
  }
  const job = reserved.job;
  const updated = job;

  return {
    status: "reserved",
    reusedExisting: Boolean(reserved.reusedExisting),
    job: {
      id: updated.id,
      status: updated.status,
      command: updated.command,
      args: updated.args ?? []
    },
    cwd: process.cwd(),
    workerCommand: updated.workerCommand ?? workerCommand,
    forwardingInstructions: "Dispatch exactly one forwarding subagent. The child must run workerCommand once as argv from the returned cwd, must not inspect or reinterpret the repository, and must return only the command result."
  };
}

function hasExplicitBackendArg(args) {
  return args.some((arg) => arg === "--backend" || arg.startsWith("--backend="));
}

function hasExplicitQualityArg(args) {
  return args.some((arg) => arg === "--quality" || arg.startsWith("--quality="));
}

function handleSubagentCommand(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const delegatedCommand = tokens[0];
  if (!delegatedCommand) {
    throw new Error("Missing command to delegate.");
  }
  if (!SUBAGENT_DELEGATABLE_COMMANDS.has(delegatedCommand)) {
    throw new Error(`Command "${delegatedCommand}" cannot be delegated to a Codex subagent.`);
  }

  const delegatedArgs = tokens.slice(1);
  const parsed = validateCommandNativeModeOptions(delegatedCommand, delegatedArgs);
  if (parsed.write) {
    throw new Error("--write cannot be delegated to a Codex subagent; run write-capable Claude commands only from the parent after reviewing the plan.");
  }
  if (parsed.background) {
    throw new Error("subagent-command is foreground-only; use reserve-job for --background delegation.");
  }
  validateBackendArgs(parsed);
  validateBackendCompatibleOptions(parsed);
  validateSdkSubagentsBackend(parsed);

  const materializedArgs = [...delegatedArgs];
  if (!hasExplicitBackendArg(materializedArgs)) {
    materializedArgs.push("--backend", parsed.backend);
  }
  if (!hasExplicitQualityArg(materializedArgs)) {
    const source = parsed.quality !== undefined ? "--quality" : QUALITY_ENV;
    const quality = assertValidQuality(parsed.quality ?? process.env[QUALITY_ENV] ?? "auto", source);
    materializedArgs.push("--quality", quality);
  }

  return {
    status: "ready",
    mode: "foreground",
    command: delegatedCommand,
    cwd: process.cwd(),
    workerCommand: [
      process.execPath,
      path.resolve(process.argv[1]),
      delegatedCommand,
      ...materializedArgs
    ],
    forwardingInstructions: "Dispatch exactly one Codex subagent. The subagent must run workerCommand exactly once as argv, must use the returned cwd, must preserve argv boundaries, must not inspect or reinterpret the repository first, and must not replace it with raw claude or claude -p."
  };
}

function hasReviewableGitChanges(cwd = process.cwd(), options = {}) {
  const inside = gitShort(["rev-parse", "--is-inside-work-tree"], cwd, options);
  if (inside.timedOut) {
    return { reviewable: false, reason: "git repository check timed out", timedOut: true };
  }
  if (inside.status !== 0) {
    return { reviewable: false, reason: "not a git repository" };
  }
  const status = gitShort(["status", "--short", "--untracked-files=all"], cwd, options);
  if (status.timedOut) {
    return { reviewable: false, reason: "git status timed out", timedOut: true };
  }
  if (status.status !== 0) {
    return { reviewable: false, reason: "git status failed" };
  }
  return {
    reviewable: Boolean(status.stdout.trim()),
    reason: status.stdout.trim() ? "git working tree has changes" : "no git changes"
  };
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
  process.stdout.write(`${JSON.stringify(buildCapabilitiesReport({ probeNativeSubcommands: true }), null, 2)}\n`);
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
    ciSimulate: false,
    timeoutMs: 30000
  };
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "--remote-install") {
      options.remoteInstall = true;
    } else if (token === "--require-remote-install") {
      options.remoteInstall = true;
      options.requireRemoteInstall = true;
    } else if (token === "--ci-simulate") {
      options.ciSimulate = true;
    } else if (token === "--ref") {
      options.releaseRef = readOptionValue(tokens, index, token);
      index += 1;
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
  const candidateRepoRoot = path.resolve(pluginRoot(), "..", "..");
  const releaseCheckRoot = fs.existsSync(path.join(candidateRepoRoot, "plugins", "claude-for-codex", ".codex-plugin", "plugin.json"))
    ? candidateRepoRoot
    : pluginRoot();
  const payload = runReleaseCheck(releaseCheckRoot, options);
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(payload.ok ? 0 : 1);
}

function parseGithubActionsOptions(tokens) {
  const options = {
    write: false,
    force: false,
    annotations: false,
    multiReview: false,
    roles: "correctness,security,tests",
    model: "",
    effort: "",
    quality: "standard",
    semanticContext: "off",
    timeoutMinutes: 30,
    releaseRef: undefined,
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
    } else if (token === "--multi-review") {
      options.multiReview = true;
    } else if (token === "--roles") {
      options.roles = readOptionValue(tokens, index, token);
      index += 1;
    } else if (token === "--model") {
      options.model = assertSafeModelAliasOrId(readOptionValue(tokens, index, token));
      index += 1;
    } else if (token === "--effort") {
      options.effort = assertValidEffort(readOptionValue(tokens, index, token));
      index += 1;
    } else if (token === "--quality") {
      options.quality = readOptionValue(tokens, index, token).trim().toLowerCase();
      if (!VALID_QUALITIES.includes(options.quality)) {
        throw new Error(`Invalid --quality "${options.quality}". Valid values: ${VALID_QUALITIES.join(", ")}.`);
      }
      index += 1;
    } else if (token === "--semantic-context") {
      options.semanticContext = readOptionValue(tokens, index, token);
      index += 1;
    } else if (token === "--timeout-minutes") {
      options.timeoutMinutes = Number(readOptionValue(tokens, index, token));
      index += 1;
    } else if (token === "--ref") {
      options.releaseRef = readOptionValue(tokens, index, token);
      index += 1;
    } else if (token === "--input") {
      options.input = readOptionValue(tokens, index, token);
      index += 1;
    } else if (token === "--json") {
      // JSON is the only structured validation output.
    } else {
      throw new Error(`Unknown github-actions option "${token}".`);
    }
  }
  return options;
}

function runGithubActionsCommand(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const subcommand = tokens.shift();
  if (!subcommand || !["render", "init", "validate", "render-comment", "render-annotations"].includes(subcommand)) {
    console.error("Usage: claude-companion.mjs github-actions render|init|validate|render-comment|render-annotations [options]");
    process.exit(2);
  }
  let options;
  try {
    options = parseGithubActionsOptions(tokens);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  try {
    if (subcommand === "render") {
      process.stdout.write(renderWorkflow(pluginRoot(), options));
      return;
    }
    if (subcommand === "init") {
      const text = renderWorkflow(pluginRoot(), options);
      if (!options.write) {
        process.stdout.write(`${JSON.stringify({ ok: true, written: false, path: workflowPath(process.cwd()) }, null, 2)}\n`);
        return;
      }
      const target = writeWorkflow(process.cwd(), text, { force: options.force });
      process.stdout.write(`${JSON.stringify({ ok: true, written: true, path: target }, null, 2)}\n`);
      return;
    }
    if (subcommand === "validate") {
      const target = workflowPath(process.cwd());
      const text = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : renderWorkflow(pluginRoot(), options);
      const payload = validateWorkflow(text, options);
      process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
      process.exit(payload.ok ? 0 : 1);
    }
    if (subcommand === "render-comment") {
      if (!options.input) {
        throw new Error("render-comment requires --input <review-json>.");
      }
      process.stdout.write(`${renderReviewComment(readReviewJson(options.input))}\n`);
      return;
    }
    if (subcommand === "render-annotations") {
      if (!options.input) {
        throw new Error("render-annotations requires --input <review-json>.");
      }
      process.stdout.write(`${JSON.stringify(reviewToAnnotations(readReviewJson(options.input)), null, 2)}\n`);
    }
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(subcommand === "init" ? 1 : 2);
  }
}

function runRolesCommand(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const subcommand = tokens.shift();
  const jsonOutput = tokens.includes("--json");
  const filtered = tokens.filter((token) => token !== "--json");
  try {
    if (subcommand === "list") {
      const packs = listRolePacks();
      if (jsonOutput) {
        process.stdout.write(`${JSON.stringify({ rolePacks: packs }, null, 2)}\n`);
        return;
      }
      process.stdout.write([
        "Claude role packs:",
        ...packs.map((pack) => `- ${pack.name}: ${pack.roles.join(", ")}${pack.gate_compatible ? " (gate-compatible)" : ""}`)
      ].join("\n") + "\n");
      return;
    }
    if (subcommand === "inspect") {
      const packName = filtered[0];
      if (!packName || filtered.length !== 1) {
        throw new Error("Usage: claude-companion.mjs roles inspect <pack> [--json]");
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
        `hash: ${summary.hash}`,
        `description: ${summary.description}`
      ].join("\n") + "\n");
      return;
    }
    if (subcommand === "validate") {
      const file = filtered[0];
      if (!file || filtered.length !== 1) {
        throw new Error("Usage: claude-companion.mjs roles validate <file> [--json]");
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
    throw new Error("Usage: claude-companion.mjs roles list|inspect|validate [options]");
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
}

function runMailboxCommand(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const subcommand = tokens.shift();
  const jsonOutput = tokens.includes("--json");
  const args = tokens.filter((token) => token !== "--json");
  try {
    if (subcommand === "list") {
      const payload = listMailboxThreads(process.cwd(), process.env);
      process.stdout.write(jsonOutput
        ? `${JSON.stringify(payload, null, 2)}\n`
        : [`Mailbox threads:`, ...payload.threads.map((thread) => `- ${thread.threadId}: ${thread.messageCount} messages`)].join("\n") + "\n");
      return;
    }
    if (subcommand === "show") {
      const threadId = args[0];
      if (!threadId || args.length !== 1) {
        throw new Error("Usage: claude-companion.mjs mailbox show <thread-or-job-id> [--json]");
      }
      const payload = showMailboxThread(process.cwd(), threadId, process.env);
      process.stdout.write(jsonOutput
        ? `${JSON.stringify(payload, null, 2)}\n`
        : payload.messages.map((message) => `${message.createdAt} ${message.status} ${message.role}: ${message.summary}`).join("\n") + "\n");
      return;
    }
    if (subcommand === "post") {
      const options = { role: "manual" };
      for (let index = 0; index < args.length; index += 1) {
        const token = args[index];
        if (token === "--job-id") {
          options.jobId = readOptionValue(args, index, token);
          index += 1;
        } else if (token === "--summary") {
          options.summary = readOptionValue(args, index, token);
          index += 1;
        } else if (token === "--role") {
          options.role = readOptionValue(args, index, token);
          index += 1;
        } else {
          throw new Error(`Unknown mailbox option "${token}".`);
        }
      }
      if (!options.jobId || !options.summary) {
        throw new Error("Usage: claude-companion.mjs mailbox post --job-id <id> --summary <text> [--role <role>]");
      }
      const payload = postMailboxMessage(process.cwd(), {
        threadId: options.jobId,
        jobId: options.jobId,
        role: options.role,
        command: "mailbox",
        status: "note",
        summary: options.summary,
        source: "manual"
      }, process.env);
      process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
      return;
    }
    throw new Error("Usage: claude-companion.mjs mailbox list|show|post [options]");
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
}

function runLeasesCommand(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const subcommand = tokens.shift();
  const jsonOutput = tokens.includes("--json");
  const args = tokens.filter((token) => token !== "--json");
  try {
    if (subcommand === "list") {
      const payload = listLeases(process.cwd(), process.env);
      process.stdout.write(jsonOutput
        ? `${JSON.stringify(payload, null, 2)}\n`
        : [`Active leases:`, ...payload.leases.map((lease) => `- ${lease.id}: ${lease.path} (${lease.role}) expires ${lease.expiresAt}`)].join("\n") + "\n");
      return;
    }
    if (subcommand === "claim") {
      const options = {};
      for (let index = 0; index < args.length; index += 1) {
        const token = args[index];
        if (token === "--path") {
          options.path = readOptionValue(args, index, token);
          index += 1;
        } else if (token === "--role") {
          options.role = readOptionValue(args, index, token);
          index += 1;
        } else if (token === "--ttl") {
          options.ttl = readOptionValue(args, index, token);
          index += 1;
        } else if (token === "--job-id") {
          options.jobId = readOptionValue(args, index, token);
          index += 1;
        } else {
          throw new Error(`Unknown leases option "${token}".`);
        }
      }
      if (!options.path || !options.role || !options.ttl) {
        throw new Error("Usage: claude-companion.mjs leases claim --path <path> --role <role> --ttl <duration> [--job-id <id>]");
      }
      const payload = claimLease(process.cwd(), options, process.env);
      process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
      return;
    }
    if (subcommand === "release") {
      const leaseId = args[0];
      if (!leaseId || args.length !== 1) {
        throw new Error("Usage: claude-companion.mjs leases release <lease-id> [--json]");
      }
      const payload = releaseLease(process.cwd(), leaseId, process.env, { manual: true });
      process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
      process.exit(payload.status === "released" ? 0 : 1);
    }
    throw new Error("Usage: claude-companion.mjs leases list|claim|release [options]");
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
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

async function runReviewGate(rawArgs) {
  const startedAt = new Date().toISOString();
  if (String(process.env[REVIEW_GATE_ENV] ?? "").toLowerCase() === "off") {
    return;
  }

  let args;
  try {
    args = parseArgs(rawArgs);
    validateBackendArgs(args);
  } catch (error) {
    warnGate(`argument parse failed; allowing stop: ${error.message || String(error)}`);
    return;
  }
  if (rawArgs.includes("reserve-job") || args.background || args.wait) {
    warnGate("background/wait/reserve-job flags are ignored for Stop hook review gate; allowing stop");
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

  const hookOptions = hookFingerprintOptions();
  const reviewable = hasReviewableGitChanges(cwd, hookOptions);
  if (!reviewable.reviewable) {
    if (reviewable.timedOut) {
      warnGate(`${reviewable.reason}; allowing stop`);
    }
    return;
  }
  const diffFingerprint = workingTreeFingerprintDetails(cwd, [], hookOptions);
  const diffHash = diffFingerprint.hash;
  const diffFingerprintUsable = !diffFingerprint.timedOut;
  if (diffFingerprint.timedOut && !diffFingerprint.budgetExceeded) {
    warnGate("working-tree fingerprint timed out; allowing stop and skipping cached gate decision");
    return;
  }
  if (diffFingerprint.budgetExceeded) {
    warnGate("working-tree fingerprint budget exceeded; running review without cached gate decision");
  }
  if (diffFingerprintUsable) {
    const baseline = readJson(turnBaselineFileForCwd(cwd), null);
    if (workingTreeFingerprintMatches(baseline?.workingTreeFingerprint, diffFingerprint)) {
      return;
    }
    if (config.lastAllowedReviewGateDiffHash === diffHash) {
      return;
    }
  }

  let roles;
  try {
    if (args.rolePack !== undefined) {
      const pack = resolveRolePack(args.rolePack);
      if (!rolePackGateCompatible(pack)) {
        process.stdout.write(`${JSON.stringify({
          decision: "block",
          reason: `Claude review gate role pack "${pack.name}" is not gate-compatible.`
        })}\n`);
        return;
      }
      args.rolePackSummary = rolePackSummary(pack);
      roles = rolesForPack(pack);
    } else {
      roles = defaultRoleObjects();
    }
  } catch (error) {
    process.stdout.write(`${JSON.stringify({
      decision: "block",
      reason: `Claude review gate role pack configuration error: ${error.message || String(error)}`
    })}\n`);
    return;
  }
  args.scope = "working-tree";
  const gateQualityRequest = args.quality ?? process.env[QUALITY_ENV];
  if (gateQualityRequest === undefined || String(gateQualityRequest).trim().toLowerCase() === "auto") {
    args.quality = "standard";
  }
  try {
    applyCommandQualityPolicy("review-gate", args);
  } catch (error) {
    warnGate(`quality policy failed; allowing stop: ${error.message || String(error)}`);
    return;
  }
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

  const gitContext = collectGitContext({
    ...args,
    gitRunner: (gitArgs) => gitShort(gitArgs, cwd, hookOptions)
  });
  const blocks = [];
  const gateDeadline = Date.now() + reviewGateTimeoutMs();
  let allowCount = 0;
  let gateReviewComplete = true;
  for (const role of roles) {
    const remainingMs = gateDeadline - Date.now();
    if (remainingMs <= 0) {
      gateReviewComplete = false;
      warnGate("review gate aggregate timeout reached; allowing stop");
      break;
    }
    const prompt = reviewGateRolePrompt(role, args, gitContext);
    const result = await claudePrintAsync(prompt, {
      ...args,
      timeout: Math.min(REVIEW_GATE_ROLE_TIMEOUT_MS, remainingMs)
    });
    if (result.errorCode === "ETIMEDOUT" || result.error.includes("ETIMEDOUT")) {
      gateReviewComplete = false;
      warnGate(`role ${role.name} timed out; allowing stop`);
      continue;
    }
    if (result.status !== 0) {
      gateReviewComplete = false;
      const detail = claudeFailureDiagnostic(result).trim();
      warnGate(`role ${role.name} failed; allowing stop: ${detail}`);
      continue;
    }
    const verdict = parseGateVerdict(result.stdout);
    if (verdict.kind === "block") {
      blocks.push({ role: role.name, reason: verdict.reason, output: verdict.output });
    } else if (verdict.kind === "allow") {
      allowCount += 1;
    } else if (verdict.kind === "invalid") {
      gateReviewComplete = false;
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
  if (diffFingerprintUsable && gateReviewComplete && allowCount > 0) {
    setConfig(cwd, "lastAllowedReviewGateDiffHash", diffHash);
  }
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

function sdkSubagentStatus(value, entry) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value === 0 ? 0 : 1;
  }
  if (typeof value === "string") {
    return new Set(["ok", "pass", "passed", "success", "succeeded"]).has(value.trim().toLowerCase()) ? 0 : 1;
  }
  return entry?.error ? 1 : 0;
}

function normalizeSdkReviewObject(value, roleNameForError) {
  try {
    return normalizeReviewOutput(value, { role: roleNameForError });
  } catch (error) {
    throw new Error(`role ${roleNameForError} has invalid role_results[].result.review: ${error.message || String(error)}`);
  }
}

function normalizeSdkSubagentRoleEntry(entry, { requireReview = false } = {}) {
  if (!entry || typeof entry !== "object" || typeof entry.role !== "string") {
    return null;
  }
  const source = entry.result === undefined ? entry : entry.result;
  if (!source || typeof source !== "object") {
    return null;
  }
  if (source.status !== undefined && typeof source.status !== "string" && typeof source.status !== "number") {
    return null;
  }
  if (source.review !== undefined && (!source.review || typeof source.review !== "object" || Array.isArray(source.review))) {
    return null;
  }
  if (source.text !== undefined && typeof source.text !== "string") {
    return null;
  }
  if (source.error !== undefined && typeof source.error !== "string") {
    return null;
  }
  if (requireReview && source.review === undefined && source.status !== "failed") {
    throw new Error(`role_results[].result.review is required for ${entry.role}.`);
  }
  return {
    role: entry.role,
    status: source.status,
    text: source.text,
    error: source.error,
    review: source.review === undefined ? undefined : normalizeSdkReviewObject(source.review, entry.role)
  };
}

function normalizeSdkSubagentJson(stdout, { requireReview = false } = {}) {
  let parsed;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    return { ok: false, reason: "SDK output was not valid JSON." };
  }
  if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.role_results)) {
    return { ok: false, reason: "SDK output must contain role_results array." };
  }
  const roleResults = [];
  for (const entry of parsed.role_results) {
    let roleEntry;
    try {
      roleEntry = normalizeSdkSubagentRoleEntry(entry, { requireReview });
    } catch (error) {
      return { ok: false, reason: error.message || String(error) };
    }
    if (!roleEntry) {
      return { ok: false, reason: "Each role result must include role and result fields." };
    }
    roleResults.push(roleEntry);
  }
  return { ok: true, parsed: { ...parsed, role_results: roleResults } };
}

function sdkSubagentCoverageError(expectedRoles, roleResults) {
  const expected = expectedRoles.map((role) => role.name);
  const actual = roleResults.map((entry) => entry.role);
  const countRoles = (roles) => {
    const counts = new Map();
    for (const role of roles) {
      counts.set(role, (counts.get(role) ?? 0) + 1);
    }
    return counts;
  };
  const expectedCounts = countRoles(expected);
  const actualCounts = countRoles(actual);
  const missing = [...expectedCounts.entries()]
    .filter(([role, count]) => (actualCounts.get(role) ?? 0) < count)
    .map(([role]) => role);
  const extra = [...actualCounts.entries()]
    .filter(([role, count]) => (expectedCounts.get(role) ?? 0) < count)
    .map(([role]) => role);
  if (!missing.length && !extra.length) {
    return "";
  }
  const parts = ["SDK subagent role coverage mismatch."];
  if (missing.length) {
    parts.push(`missing=${missing.join(",")}`);
  }
  if (extra.length) {
    parts.push(`extra=${extra.join(",")}`);
  }
  return parts.join(" ");
}

async function runSdkSubagentMultiReview(args, gitContext) {
  if (args.backend !== "sdk") {
    throw new Error("--agent-team sdk-subagents requires --backend sdk or CLAUDE_FOR_CODEX_BACKEND=sdk.");
  }
  const structuredJson = Boolean(args.jsonOutput && args.nativeStructured);
  const agents = buildNativeReviewAgents(args.reviewRoles, { model: args.model, effort: args.effort, structuredJson });
  const focusText = args._.join(" ");
  const prompt = nativeReviewTeamPrompt(args.reviewRoles, gitContext, focusText, { structuredJson });
  const aggregate = await runSdkNativeReview(prompt, args, {
    cwd: process.cwd(),
    timeout: args.timeout,
    agents,
    outputSchema: structuredJson ? SDK_MULTI_REVIEW_OUTPUT_SCHEMA : undefined,
    streamProgress: Boolean(args.streamProgress)
  });
  if (aggregate.status !== 0) {
    return { aggregate, results: [] };
  }
  const structuredOutput = aggregate.metadata?.structuredOutput;
  const normalized = normalizeSdkSubagentJson(
    structuredOutput === undefined ? aggregate.stdout : JSON.stringify(structuredOutput),
    { requireReview: structuredJson }
  );
  if (!normalized.ok) {
    const message = `Invalid SDK subagent JSON output.${normalized.reason ? ` ${normalized.reason}` : ""}`;
    return {
      aggregate: {
        ...aggregate,
        status: 1,
        stderr: message,
        error: message,
        errorCode: "SDK_SUBAGENT_INVALID_JSON"
      },
      results: []
    };
  }
  const coverageError = sdkSubagentCoverageError(args.reviewRoles, normalized.parsed.role_results);
  if (coverageError) {
    return {
      aggregate: {
        ...aggregate,
        status: 1,
        stdout: "",
        stderr: coverageError,
        error: coverageError,
        errorCode: "SDK_SUBAGENT_ROLE_COVERAGE"
      },
      results: []
    };
  }
  const roleResults = new Map(normalized.parsed.role_results.map((entry) => [entry.role, entry]));
  const results = args.reviewRoles.map((role) => {
    const entry = roleResults.get(role.name);
    return {
      role,
      result: {
        status: entry ? sdkSubagentStatus(entry.status, entry) : 1,
        stdout: entry?.text ?? "",
        stderr: entry?.error ?? (entry ? "" : `Missing SDK subagent result for role ${role.name}.`),
        error: entry?.error ?? "",
        errorCode: entry?.error ? "SDK_SUBAGENT_ROLE_ERROR" : "",
        backend: "sdk",
        metadata: {
          orchestration: "sdk-subagents",
          structuredReview: entry?.review
        }
      }
    };
  });
  return { aggregate, results };
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
        result: result.metadata?.structuredReview
          ? result.metadata.structuredReview
          : parseStructuredReviewResult(result.stdout, { role: role.name })
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
    text: `${JSON.stringify(aggregateRoleReviewOutputs(parsedResults), null, 2)}\n`,
    parsedResults
  };
}

async function runClaudeTask(kind, rawArgs) {
  const startedAt = new Date().toISOString();
  if (await maybeStartBackground(kind, rawArgs)) {
    return;
  }
  let args;
  try {
    args = parseArgs(rawArgs);
    validateNativeModeOptions(args, kind);
    validateBackendArgs(args);
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
    applyCommandQualityPolicy(kind, args);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
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
  const result = await claudePrintAsync(prompt, args);

  if (result.status !== 0) {
    recordCommandReport(kind, args, result, startedAt);
    process.stderr.write(claudeFailureDiagnostic(result));
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
  if (process.env.CLAUDE_FOR_CODEX_JOB_WORKER === "1" && result.stderr) {
    process.stderr.write(result.stderr);
  }
  process.stdout.write(result.stdout);
}

async function runClaudeMultiReview(rawArgs) {
  const startedAt = new Date().toISOString();
  if (await maybeStartBackground("multi-review", rawArgs)) {
    return;
  }
  let args;
  try {
    args = validateCommandNativeModeOptions("multi-review", rawArgs);
    args.agentTeam = args.agentTeam ?? "plugin";
    validateBackendArgs(args);
    validateBackendCompatibleOptions(args);
    args.reviewRoles = (args.roles === undefined && args.rolePack === undefined)
      ? defaultRoleObjects()
      : resolveReviewRoles(args);
    args.nativeOrchestration = args.agentTeam === "sdk-subagents"
      ? { enabled: true, mode: "sdk-subagents", roleCount: args.reviewRoles.length }
      : { enabled: false };
    validateSdkSubagentsBackend(args);
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
    applyCommandQualityPolicy("multi-review", args);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  try {
    attachSemanticContext(args, { command: "multi-review" });
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }

  const mailboxThreadId = args.useMailbox ? `review-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 8)}` : "";
  const mailboxFailures = [];
  const leaseResults = [];
  if (args.advisoryLeases) {
    if (!args.paths.length) {
      process.stderr.write("[claude-for-codex leases] --advisory-leases skipped because no --path was supplied.\n");
    } else {
      for (const role of args.reviewRoles) {
        for (const reviewPath of args.paths) {
          try {
            leaseResults.push(claimLease(process.cwd(), {
              path: reviewPath,
              role: role.name,
              ttl: "10m",
              jobId: mailboxThreadId || `review-${Date.now().toString(36)}`
            }, process.env));
          } catch (error) {
            leaseResults.push({ status: "degraded", reason: error.message || String(error), role: role.name, path: reviewPath });
          }
        }
      }
    }
  }
  if (args.useMailbox) {
    for (const role of args.reviewRoles) {
      try {
        postMailboxMessage(process.cwd(), {
          threadId: mailboxThreadId,
          jobId: mailboxThreadId,
          role: role.name,
          command: "multi-review",
          status: "running",
          summary: `Role ${role.name} started.`,
          source: "runtime"
        }, process.env);
      } catch (error) {
        mailboxFailures.push(error.message || String(error));
      }
    }
  }

  const gitContext = collectGitContext(args);
  const runInParallel = !args.sequential;
  const executionMode = args.agentTeam === "sdk-subagents" ? "sdk-subagents" : runInParallel ? "parallel" : "sequential";
  let results = [];
  let nativeAggregate;
  if (args.agentTeam === "sdk-subagents") {
    const nativeRun = await runSdkSubagentMultiReview(args, gitContext);
    nativeAggregate = nativeRun.aggregate;
    results = nativeRun.results;
    if (nativeAggregate.status !== 0) {
      const stderr = nativeAggregate.stderr || nativeAggregate.error || "SDK subagent multi-review failed.";
      recordCommandReport("multi-review", args, {
        ...nativeAggregate,
        stdout: "",
        stderr,
        error: nativeAggregate.error || stderr,
        backend: "sdk"
      }, startedAt, undefined, results);
      process.stderr.write(`${stderr.trim()}\n`);
      process.exit(nativeAggregate.status || 1);
    }
  } else if (runInParallel) {
    results = await runParallelRoleReviews(args.reviewRoles, args, gitContext, multiReviewRolePrompt);
  } else {
    for (const role of args.reviewRoles) {
      const prompt = multiReviewRolePrompt(role, args, gitContext);
      const result = await claudePrintAsync(prompt, args);
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
        errorCode: "",
        backend: args.agentTeam === "sdk-subagents" ? "sdk" : undefined,
        metadata: nativeAggregate?.metadata ?? {}
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
    const parsedByRole = new Map((renderedJson.parsedResults ?? [])
      .map(({ role, result }) => [role.name, result]));
    const reportRoleResults = results.map((entry) => ({
      ...entry,
      parsed: parsedByRole.get(entry.role.name)
    }));
    recordCommandReport("multi-review", args, {
      status: 0,
      stdout: renderedJson.text,
      stderr: "",
      error: "",
      errorCode: ""
    }, startedAt, parsedAggregate, reportRoleResults);
    process.stdout.write(renderedJson.text);
    process.exit(0);
  }

  const rendered = renderRoleReviewSections("# Claude Multi-Agent Review", results, "Role");
  const summaryMode = `execution mode: ${executionMode}`;
  if (args.useMailbox) {
    for (const { role, result } of results) {
      try {
        postMailboxMessage(process.cwd(), {
          threadId: mailboxThreadId,
          jobId: mailboxThreadId,
          role: role.name,
          command: "multi-review",
          status: result.status === 0 ? "succeeded" : "failed",
          summary: `Role ${role.name} completed with exit status ${result.status}.`,
          source: "runtime"
        }, process.env);
      } catch (error) {
        mailboxFailures.push(error.message || String(error));
      }
    }
  }
  args.mailboxSummary = args.useMailbox ? {
    enabled: true,
    threadId: mailboxThreadId,
    messageCount: args.reviewRoles.length + results.length,
    writeFailures: mailboxFailures.length
  } : { enabled: false };
  args.leaseSummary = args.advisoryLeases ? {
    enabled: true,
    claimed: leaseResults.filter((entry) => entry.status === "claimed").length,
    conflicts: leaseResults.filter((entry) => entry.status === "conflict").length,
    degraded: leaseResults.some((entry) => entry.status === "degraded")
  } : { enabled: false };
  const mailboxLine = args.useMailbox ? `\nmailbox thread: ${mailboxThreadId}; write failures: ${mailboxFailures.length}` : "";
  const leaseLine = args.advisoryLeases ? `\nleases claimed: ${args.leaseSummary.claimed}; conflicts: ${args.leaseSummary.conflicts}; degraded: ${args.leaseSummary.degraded ? "yes" : "no"}` : "";
  const output = rendered.text.replace("execution mode: parallel", summaryMode) + mailboxLine + leaseLine;
  recordCommandReport("multi-review", args, {
    status: rendered.failed.length ? 1 : 0,
    stdout: output,
    stderr: "",
    error: "",
    errorCode: "",
    backend: args.agentTeam === "sdk-subagents" ? "sdk" : undefined,
    metadata: nativeAggregate?.metadata ?? {}
  }, startedAt, undefined, results);
  process.stdout.write(output);
  process.exit(rendered.failed.length ? 1 : 0);
}

function runClaudeUltrareview(rawArgs) {
  const tokens = normalizeArgv(rawArgs);
  const forwarded = [];
  let confirmed = process.env.CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW === "1";
  let timeoutMinutes;
  for (let index = 0; index < tokens.length; index += 1) {
    const arg = tokens[index];
    if (arg === "--confirm-cost") {
      confirmed = true;
    } else if (arg === "--json") {
      forwarded.push(arg);
    } else if (arg === "--timeout") {
      const minutes = tokens[index + 1];
      if (!minutes || !/^[1-9]\d*$/.test(minutes)) {
        console.error("Missing or invalid --timeout minutes.");
        process.exit(2);
      }
      timeoutMinutes = Number(minutes);
      forwarded.push(arg, minutes);
      index += 1;
    } else if (arg.startsWith("-")) {
      console.error(`Unsupported ultrareview option: ${arg}`);
      process.exit(2);
    } else {
      forwarded.push(arg);
    }
  }
  if (!confirmed) {
    console.error("ultrareview may use remote/cloud usage and usage-credit billing; pass --confirm-cost or set CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1 to continue.");
    process.exit(2);
  }
  const spawnTimeout = timeoutMinutes ? (timeoutMinutes + 1) * 60 * 1000 : 35 * 60 * 1000;
  const result = runClaude(["ultrareview", ...forwarded], { timeout: spawnTimeout });
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  if (result.error) {
    process.stderr.write(result.error);
  }
  process.exit(result.status);
}

function printJobs() {
  reapLostJobs(process.cwd());
  const payload = listJobs(process.cwd());
  process.stdout.write(`${JSON.stringify({
    ...payload,
    jobs: payload.jobs.map((job) => enrichCompanionJob(job))
  }, null, 2)}\n`);
}

function printResult(rawArgs) {
  let jobId;
  try {
    jobId = parseJobIdArg(rawArgs);
  } catch (_error) {
    console.error("Usage: claude-companion.mjs result <job-id>");
    process.exit(2);
  }
  let payload;
  try {
    reapLostJobs(process.cwd());
    payload = resultForJob(process.cwd(), jobId);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
  if (payload.job) {
    payload = { ...payload, job: enrichCompanionJob(payload.job) };
  }
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(payload.status === "ok" ? 0 : 1);
}

function runRecommendExecutionMode(rawArgs) {
  process.stdout.write(`${JSON.stringify(recommendExecutionMode(process.cwd(), recommendModeArgs(rawArgs)), null, 2)}\n`);
}

function runCancel(rawArgs) {
  let jobId;
  try {
    jobId = parseJobIdArg(rawArgs);
  } catch (_error) {
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

async function runJobWorker(rawArgs) {
  try {
    assertBackgroundPlatformSupported(process.env);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
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
  const claim = claimJobForRun(process.cwd(), jobId, process.pid);
  if (claim.status !== "claimed") {
    process.stdout.write(`${JSON.stringify({ status: claim.status, jobId, reason: claim.reason ?? "" }, null, 2)}\n`);
    process.exit(1);
  }
  const result = await runStoredJobCommand(claim.job, { stateCwd: process.cwd() });
  const current = readJob(process.cwd(), jobId);
  if (current?.status === "cancelled") {
    process.exit(0);
  }
  finishJob(process.cwd(), jobId, result);
  process.exit(result.status ?? 1);
}

function runStoredJobCommand(job, options = {}) {
  try {
    assertBackgroundPlatformSupported(process.env);
  } catch (error) {
    return Promise.resolve({
      status: 2,
      stdout: "",
      stderr: error.message || String(error),
      error: ""
    });
  }
  if (!BACKGROUND_CAPABLE_COMMANDS.has(job.command)) {
    return Promise.resolve({
      status: 2,
      stdout: "",
      stderr: `Unsupported reserved job command "${job.command}".`,
      error: ""
    });
  }
  const stateCwd = options.stateCwd ?? process.cwd();
  const foregroundArgs = stripBackgroundArgs(job.args ?? []);
  return new Promise((resolve) => {
    const hardMs = hardJobTimeoutMs();
    const killMs = killGraceMs();
    const started = Date.now();
    const stdoutOutput = makeCappedOutputAccumulator();
    const stderrOutput = makeCappedOutputAccumulator();
    let settled = false;
    let stopRequested = false;
    let hardTimedOut = false;
    let hardTimedOutAt = 0;
    let childProcessGroupIdentity = null;
    let killTimer = null;
    let heartbeat = null;
    let timeout = null;
    let child;
    let childExitedBeforeIdentity = false;
    let deliveredToValidatedChildGroup = false;
    const progressLines = makeProgressLineBuffer((line) => {
      const parsed = progressEventsFromLines([line]);
      // Malformed counts are parser diagnostics for tests and future telemetry.
      // The worker ignores them here to avoid turning model stderr into noisy
      // supervision output while still accepting well-formed progress events.
      for (const event of parsed.events) {
        recordJobProgress(stateCwd, job.id, {
          phase: event.phase || "running",
          lastProgressMessage: event.message || "",
          lastProgressRole: event.role || "",
          childPid: child?.pid ?? null,
          childProcessGroupPid: child?.pid ?? null,
          childProcessGroupIdentity
        });
      }
    });

    function commandResult(status, error = "") {
      const stdout = stdoutOutput.metadata();
      const stderr = stderrOutput.metadata();
      return {
        status,
        stdout: stdoutOutput.text(),
        stderr: stderrOutput.text(),
        stdoutBytes: stdout.bytes,
        stderrBytes: stderr.bytes,
        stdoutTruncated: stdout.truncated,
        stderrTruncated: stderr.truncated,
        error
      };
    }

    function stopChildGroup(signal, options = {}) {
      stopRequested = true;
      if (!child?.pid) {
        return false;
      }
      if (childProcessGroupIdentity) {
        const validation = validateProcessGroupLeader(child.pid, childProcessGroupIdentity);
        if (!validation.ok) {
          if (
            validation.reason === "process not found"
            && deliveredToValidatedChildGroup
            && processGroupHasLiveMembers(child.pid)
          ) {
            try {
              process.kill(-child.pid, signal);
              return true;
            } catch {
              return false;
            }
          }
          return false;
        }
        if (options.requireLiveGroup && !processGroupHasLiveMembers(child.pid)) {
          return false;
        }
        try {
          process.kill(validation.signalPid, signal);
          deliveredToValidatedChildGroup = true;
          return true;
        } catch {
          return false;
        }
      }
      if (options.requireLiveGroup && !processGroupHasLiveMembers(child.pid)) {
        return false;
      }
      try {
        process.kill(-child.pid, signal);
        return true;
      } catch {
        if (options.requireLiveGroup) {
          return false;
        }
        try {
          child.kill(signal);
          return true;
        } catch {
          // Child may already have exited.
        }
      }
      return false;
    }

    function stopUnvalidatedChild(signal) {
      stopRequested = true;
      if (!child?.pid) {
        return;
      }
      try {
        child.kill(signal);
      } catch {
        // Child may already have exited.
      }
    }

    function cleanup(options = {}) {
      clearInterval(heartbeat);
      clearTimeout(timeout);
      if (!options.keepKillTimer) {
        clearTimeout(killTimer);
      }
      process.removeListener("SIGTERM", signalHandler);
      process.removeListener("SIGINT", signalHandler);
    }

    function stopChildWithEscalation(reasonSignal = "SIGTERM") {
      // Invariant: every path that resolves the worker result sets `settled`
      // first. `killTimer` belongs to the active escalation branch until close
      // either cancels it or intentionally keeps/replaces it below.
      stopChildGroup(reasonSignal === "SIGKILL" ? "SIGKILL" : "SIGTERM");
      if (reasonSignal !== "SIGKILL") {
        clearTimeout(killTimer);
        killTimer = setTimeout(() => {
          const delivered = stopChildGroup("SIGKILL", { requireLiveGroup: hardTimedOut });
          if (!hardTimedOut) {
            return;
          }
          killTimer = setTimeout(() => {
            if (settled) {
              return;
            }
            const stillAlive = child?.pid ? (isProcessAlive(child.pid) || processGroupHasLiveMembers(child.pid)) : false;
            settled = true;
            progressLines.flush();
            cleanup();
            resolve({
              ...commandResult(1, stillAlive
                ? "Child process group still alive after hard timeout SIGKILL escalation."
                : delivered
                ? "Child process did not emit close after hard timeout SIGKILL escalation."
                : "Child process group was not live when hard timeout SIGKILL escalation ran.")
            });
          }, 1_000);
        }, killMs);
      }
    }

    const signalHandler = () => stopChildWithEscalation("SIGTERM");

    process.once("SIGTERM", signalHandler);
    process.once("SIGINT", signalHandler);

    child = spawn(process.execPath, [process.argv[1], job.command, ...foregroundArgs], {
      cwd: job.cwd || process.cwd(),
      env: {
        ...process.env,
        CLAUDE_FOR_CODEX_JOB_WORKER: "1"
      },
      detached: true,
      stdio: ["ignore", "pipe", "pipe"]
    });
    if (stopRequested) {
      stopChildWithEscalation("SIGTERM");
    }

    child.stdout?.on("data", (chunk) => stdoutOutput.push(chunk));
    child.stderr?.on("data", (chunk) => {
      const buffer = Buffer.from(chunk);
      stderrOutput.push(buffer);
      progressLines.push(buffer);
    });
    child.once("exit", () => {
      childExitedBeforeIdentity = true;
    });

    // This bounded synchronous probe runs only at child startup. It prevents
    // PID-reuse-unsafe process-group signaling; signals delivered during the
    // short probe are handled immediately after it returns.
    childProcessGroupIdentity = child.pid ? captureProcessGroupIdentity(child.pid) : null;
    if (child.pid && !childProcessGroupIdentity) {
      const fastExited = childExitedBeforeIdentity || child.exitCode !== null || child.signalCode !== null || !isProcessAlive(child.pid);
      if (fastExited) {
        childExitedBeforeIdentity = true;
      } else {
        settled = true;
        stopUnvalidatedChild("SIGKILL");
        resolve({
          status: 1,
          stdout: "",
          stderr: "",
          error: "Child process group identity could not be validated after spawn; refusing to supervise an unsafe signal target."
        });
        cleanup();
        return;
      }
    }
    recordJobProgress(stateCwd, job.id, {
      phase: "submitted",
      childPid: child.pid ?? null,
      childProcessGroupPid: child.pid ?? null,
      childProcessGroupIdentity
    });
    heartbeat = setInterval(() => {
      recordJobHeartbeat(stateCwd, job.id, {
        phase: "running",
        childPid: child.pid ?? null,
        childProcessGroupPid: child.pid ?? null,
        childProcessGroupIdentity
      });
    }, heartbeatIntervalMs());

    timeout = setTimeout(() => {
      hardTimedOut = true;
      hardTimedOutAt = Date.now();
      stopChildWithEscalation("SIGTERM");
    }, hardMs);

    child.once("error", (error) => {
      if (settled) {
        return;
      }
      settled = true;
      progressLines.flush();
      cleanup();
      resolve({
        ...commandResult(1, error.message || String(error))
      });
    });
    child.once("close", (status, signal) => {
      if (settled) {
        return;
      }
      settled = true;
      progressLines.flush();
      if (hardTimedOut && signal !== "SIGKILL") {
        // The child closed during the hard-timeout grace window. Keep ownership
        // of the escalation timer long enough to preserve timeout-as-failure
        // semantics even if the process reports a clean exit after SIGTERM.
        cleanup({ keepKillTimer: true });
        clearTimeout(killTimer);
        const remainingKillGraceMs = Math.max(0, (hardTimedOutAt || Date.now()) + killMs - Date.now());
        killTimer = setTimeout(() => {
          stopChildGroup("SIGKILL", { requireLiveGroup: true });
          clearTimeout(killTimer);
          resolve({
            ...commandResult(1, `Child terminated by ${signal ?? "SIGTERM"} after hard timeout before SIGKILL escalation.`)
          });
        }, remainingKillGraceMs);
        return;
      }
      cleanup();
      resolve({
        ...commandResult(hardTimedOut ? 1 : status ?? (signal ? 1 : 0), hardTimedOut
          ? `Child terminated by ${signal ?? "timeout"} after hard timeout.`
          : stopRequested
          ? `Child terminated by ${signal ?? "SIGTERM"} after stop request.`
          : "")
      });
    });
  });
}

async function runReservedJob(rawArgs) {
  let jobId;
  let stateCwd;
  try {
    ({ jobId, cwd: stateCwd } = parseReservedJobRunArgs(rawArgs));
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }

  let claim;
  try {
    claim = claimReservedJob(stateCwd, jobId, process.pid);
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

  const result = await runStoredJobCommand(claim.job, { stateCwd });
  const finished = finishJob(stateCwd, claim.job.id, result);
  if (!finished || finished.status === "locked") {
    process.stdout.write(`${JSON.stringify({
      status: "finish_failed",
      jobId: claim.job.id,
      exitStatus: result.status ?? 1,
      message: finished?.reason ?? "Reserved job finished but final state could not be persisted."
    }, null, 2)}\n`);
    process.exit(1);
  }
  process.stdout.write(`${JSON.stringify({
    status: finished.status,
    jobId: finished.id ?? claim.job.id,
    exitStatus: finished.exitStatus ?? result.status ?? 1
  }, null, 2)}\n`);
  process.exit(result.status ?? 1);
}

const [command, ...rawArgs] = process.argv.slice(2);

if (!VALID_COMMANDS.has(command)) {
  console.error(`Usage: claude-companion.mjs ${Array.from(VALID_COMMANDS).join("|")} [args]`);
  process.exit(2);
}

if (maybePrintCommandHelp(command, rawArgs)) {
  process.exit(0);
}

if (command !== "reserve-job" && command !== "subagent-command") {
  try {
    validateCommandNativeModeOptions(command, rawArgs);
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(2);
  }
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
  case "ultrareview":
    runClaudeUltrareview(rawArgs);
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
    await runReviewGate(rawArgs);
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
  case "github-actions":
    runGithubActionsCommand(rawArgs);
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
  case "recommend-execution-mode":
    runRecommendExecutionMode(rawArgs);
    break;
  case "__run-job":
    await runJobWorker(rawArgs);
    break;
  case "reserve-job":
    try {
      const payload = handleReserveJob(rawArgs);
      process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
      if (payload.status === "capacity_blocked" || payload.status === "workspace_locked") {
        process.exit(2);
      }
    } catch (error) {
      console.error(error.message || String(error));
      process.exit(2);
    }
    break;
  case "subagent-command":
    try {
      process.stdout.write(`${JSON.stringify(handleSubagentCommand(rawArgs), null, 2)}\n`);
    } catch (error) {
      console.error(error.message || String(error));
      process.exit(2);
    }
    break;
  case "run-reserved-job":
    await runReservedJob(rawArgs);
    break;
}
