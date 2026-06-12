#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import {
  antigravityPreflight,
  antigravityModelDiagnostics,
  antigravityPrint,
  antigravityPrintAsync,
  MODEL_PROVIDER_ENV,
  MODEL_ENV,
  normalizedModelProvider,
  selectedModel
} from "./lib/antigravity-runtime.mjs";
import { classifyAgyOutcome } from "./lib/agy-outcome.mjs";
import {
  cancelJob,
  claimReservedJob,
  createJob,
  findReusableJob,
  finishJob,
  listJobs,
  markJobMetadataPersistenceFailed,
  markJobRunning,
  markJobViewed,
  readJob,
  reserveJob,
  updateJob,
  withWorkspaceJobLock
} from "./lib/jobs.mjs";
import { captureProcessIdentity } from "./lib/process.mjs";
import {
  classifyJobLiveness,
  deriveJobIdempotencyKey,
  isTerminalJobStatus,
  jobHeartbeatIntervalMs,
  maxActiveJobs
} from "./lib/job-lifecycle.mjs";
import { worktreeFingerprint } from "./lib/worktree-fingerprint.mjs";
import { antigravityDoctor } from "./lib/doctor.mjs";
import {
  renderWorkflow,
  validateWorkflow,
  writeWorkflow
} from "./lib/github-actions.mjs";
import {
  claimLease,
  listLeases,
  releaseLease
} from "./lib/leases.mjs";
import {
  listMailboxThreads,
  postMailboxMessage,
  showMailboxThread
} from "./lib/mailbox.mjs";
import {
  readPromptTemplate,
  renderTemplate
} from "./lib/prompt-template.mjs";
import {
  latestReport,
  operationReport,
  writeOperationReport
} from "./lib/reports.mjs";
import {
  BUILT_IN_ROLE_PACKS,
  REVIEW_ROLES,
  resolveRoles
} from "./lib/role-packs.mjs";
import {
  appendStructuredReviewInstructions,
  extractJsonObject,
  validateStructuredReview
} from "./lib/structured-output.mjs";
import {
  PLUGIN_VERSION,
  RELEASE_REF
} from "./lib/version.mjs";

const VALID_COMMANDS = new Set([
  "setup",
  "capabilities",
  "doctor",
  "review",
  "adversarial-review",
  "multi-review",
  "roles",
  "plan",
  "rescue",
  "report",
  "jobs",
  "status",
  "result",
  "cancel",
  "mailbox",
  "leases",
  "github-actions",
  "reserve-job",
  "run-reserved-job",
  "__run-job",
  "review-gate",
  "real-smoke",
  "release-check"
]);
const USER_VISIBLE_COMMANDS = [...VALID_COMMANDS].filter((command) => command !== "__run-job");
const REVIEW_COMMANDS = new Set(["review", "adversarial-review", "plan", "rescue"]);
const VALID_MODEL_PROVIDERS = new Set(["gemini", "claude"]);
const ROOT_DIR = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const REPO_ROOT_DIR = path.resolve(ROOT_DIR, "..", "..");
const COMPANION_PATH = fileURLToPath(import.meta.url);
const DEFAULT_TIMEOUT_MS = 15 * 60 * 1000;
const REVIEW_GATE_TIMEOUT_ENV = "ANTIGRAVITY_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS";
const REVIEW_GATE_DEFAULT_TIMEOUT_MS = 14 * 60 * 1000;
const REVIEW_GATE_WRAPPER_TIMEOUT_MS = 870 * 1000;
const REVIEW_GATE_WRAPPER_GRACE_MS = 30 * 1000;
const BACKGROUND_SUPERVISOR_TIMEOUT_GRACE_MS = 1000;
const CHILD_OUTPUT_MAX_BUFFER = 20 * 1024 * 1024;
const GIT_TIMEOUT_MS = 30 * 1000;
const GIT_MAX_BUFFER = 2 * 1024 * 1024;
const GIT_OUTPUT_EXCERPT_BYTES = 64 * 1024;
const UNTRACKED_FILE_LIMIT = 10;
const UNTRACKED_BYTES_PER_FILE = 16 * 1024;

function usage(command = "") {
  if (command === "review") {
    return "Usage: antigravity-companion.mjs review [--model-provider gemini|claude] [--model <label>] [--structured] [--json] [focus]\n";
  }
  if (command === "multi-review") {
    return "Usage: antigravity-companion.mjs multi-review [--model-provider gemini|claude] [--model <label>] [--roles correctness,security,tests,release,adversarial] [--role-pack default|security|release] [--use-mailbox] [--advisory-leases] [focus]\n";
  }
  if (command === "roles") {
    return "Usage: antigravity-companion.mjs roles --json\n";
  }
  if (command === "doctor") {
    return "Usage: antigravity-companion.mjs doctor [--json] [--model-provider gemini|claude] [--model <label>]\n";
  }
  if (command === "report") {
    return "Usage: antigravity-companion.mjs report --latest\n";
  }
  if (command === "mailbox") {
    return "Usage: antigravity-companion.mjs mailbox <list|post|show> [--thread <id>] [--message <text>]\n";
  }
  if (command === "leases") {
    return "Usage: antigravity-companion.mjs leases <claim|list|release> [--role <role>] [--ttl-seconds <n>] [--id <lease-id>]\n";
  }
  if (command === "github-actions") {
    return "Usage: antigravity-companion.mjs github-actions <render|init|validate> [--force] [--model-provider gemini|claude] [--model <label>] [--ref <tag>] [--timeout-minutes <n>] [--path <workflow-path>]\n";
  }
  return "Usage: antigravity-companion.mjs <setup|capabilities|doctor|review|adversarial-review|multi-review|roles|plan|rescue|report|jobs|status|result|cancel|mailbox|leases|github-actions|reserve-job|run-reserved-job|review-gate|real-smoke|release-check> [args]\n";
}

function readOptionValue(tokens, index, option) {
  const value = tokens[index + 1];
  if (value === undefined || value.startsWith("--")) {
    throw new Error(`Missing value for ${option}.`);
  }
  return value;
}

function parseArgs(rawArgs) {
  const args = {
    positional: [],
    modelProvider: undefined,
    model: undefined,
    roles: undefined,
    rolePack: undefined,
    help: false,
    timeout: DEFAULT_TIMEOUT_MS,
    quick: false,
    full: false,
    structured: false,
    json: false,
    latest: false,
    background: false,
    useMailbox: false,
    advisoryLeases: false,
    role: "",
    ttlSeconds: undefined,
    thread: "",
    message: "",
    id: "",
    force: false,
    ref: "",
    timeoutMinutes: 0,
    workflowPath: ""
  };
  for (let index = 0; index < rawArgs.length; index += 1) {
    const token = rawArgs[index];
    if (token === "--model-provider") {
      args.modelProvider = readOptionValue(rawArgs, index, token);
      index += 1;
    } else if (token.startsWith("--model-provider=")) {
      args.modelProvider = token.slice("--model-provider=".length);
    } else if (token === "--model") {
      args.model = readOptionValue(rawArgs, index, token);
      index += 1;
    } else if (token.startsWith("--model=")) {
      args.model = token.slice("--model=".length);
    } else if (token === "--roles") {
      args.roles = readOptionValue(rawArgs, index, token)
        .split(",")
        .map((role) => role.trim())
        .filter(Boolean);
      index += 1;
    } else if (token.startsWith("--roles=")) {
      args.roles = token.slice("--roles=".length)
        .split(",")
        .map((role) => role.trim())
        .filter(Boolean);
    } else if (token === "--role-pack") {
      args.rolePack = readOptionValue(rawArgs, index, token);
      index += 1;
    } else if (token.startsWith("--role-pack=")) {
      args.rolePack = token.slice("--role-pack=".length);
    } else if (token === "--help" || token === "-h" || token === "help") {
      args.help = true;
    } else if (token === "--quick") {
      args.quick = true;
    } else if (token === "--full") {
      args.full = true;
    } else if (token === "--structured") {
      args.structured = true;
    } else if (token === "--json") {
      args.json = true;
      args.structured = true;
    } else if (token === "--latest") {
      args.latest = true;
    } else if (token === "--background") {
      args.background = true;
    } else if (token === "--use-mailbox") {
      args.useMailbox = true;
    } else if (token === "--advisory-leases") {
      args.advisoryLeases = true;
    } else if (token === "--role") {
      args.role = readOptionValue(rawArgs, index, token);
      index += 1;
    } else if (token.startsWith("--role=")) {
      args.role = token.slice("--role=".length);
    } else if (token === "--ttl-seconds") {
      args.ttlSeconds = Number(readOptionValue(rawArgs, index, token));
      index += 1;
    } else if (token.startsWith("--ttl-seconds=")) {
      args.ttlSeconds = Number(token.slice("--ttl-seconds=".length));
    } else if (token === "--thread") {
      args.thread = readOptionValue(rawArgs, index, token);
      index += 1;
    } else if (token.startsWith("--thread=")) {
      args.thread = token.slice("--thread=".length);
    } else if (token === "--message") {
      args.message = readOptionValue(rawArgs, index, token);
      index += 1;
    } else if (token.startsWith("--message=")) {
      args.message = token.slice("--message=".length);
    } else if (token === "--id") {
      args.id = readOptionValue(rawArgs, index, token);
      index += 1;
    } else if (token.startsWith("--id=")) {
      args.id = token.slice("--id=".length);
    } else if (token === "--force") {
      args.force = true;
    } else if (token === "--ref") {
      args.ref = readOptionValue(rawArgs, index, token);
      index += 1;
    } else if (token.startsWith("--ref=")) {
      args.ref = token.slice("--ref=".length);
    } else if (token === "--timeout-minutes") {
      args.timeoutMinutes = Number(readOptionValue(rawArgs, index, token));
      index += 1;
    } else if (token.startsWith("--timeout-minutes=")) {
      args.timeoutMinutes = Number(token.slice("--timeout-minutes=".length));
    } else if (token === "--path") {
      args.workflowPath = readOptionValue(rawArgs, index, token);
      index += 1;
    } else if (token.startsWith("--path=")) {
      args.workflowPath = token.slice("--path=".length);
    } else if (token === "--timeout-seconds") {
      const value = Number(readOptionValue(rawArgs, index, token));
      if (!Number.isFinite(value) || value < 1 || value > 3600) {
        throw new Error("--timeout-seconds must be between 1 and 3600.");
      }
      args.timeout = Math.ceil(value * 1000);
      index += 1;
    } else if (token.startsWith("--timeout-seconds=")) {
      const value = Number(token.slice("--timeout-seconds=".length));
      if (!Number.isFinite(value) || value < 1 || value > 3600) {
        throw new Error("--timeout-seconds must be between 1 and 3600.");
      }
      args.timeout = Math.ceil(value * 1000);
    } else {
      args.positional.push(token);
    }
  }

  if (args.modelProvider !== undefined) {
    args.modelProvider = String(args.modelProvider).trim().toLowerCase();
    if (!VALID_MODEL_PROVIDERS.has(args.modelProvider)) {
      throw new Error(`Invalid --model-provider "${args.modelProvider}". Valid values: gemini, claude.`);
    }
  }
  return args;
}

function readText(relativePath) {
  return fs.readFileSync(path.join(ROOT_DIR, relativePath), "utf8");
}

function readRepoText(relativePath) {
  return fs.readFileSync(path.join(REPO_ROOT_DIR, relativePath), "utf8");
}

function readDistributionText(relativePath) {
  const pluginPrefix = `plugins${path.sep}antigravity-for-codex${path.sep}`;
  const normalized = path.normalize(relativePath);
  if (normalized.startsWith(pluginPrefix)) {
    return readText(normalized.slice(pluginPrefix.length));
  }
  return readRepoText(relativePath);
}

function exists(relativePath) {
  return fs.existsSync(path.join(ROOT_DIR, relativePath));
}

function shippedPluginTexts(roots = [
  "scripts",
  "hooks",
  "skills",
  "prompts",
  "templates",
  "contracts",
  "schemas",
  "assets",
  "README.md",
  "CHANGELOG.md",
  ".codex-plugin/plugin.json"
]) {
  const results = [];
  const visit = (relativePath) => {
    const fullPath = path.join(ROOT_DIR, relativePath);
    if (!fs.existsSync(fullPath)) return;
    const stat = fs.statSync(fullPath);
    if (stat.isDirectory()) {
      for (const child of fs.readdirSync(fullPath)) visit(path.join(relativePath, child));
      return;
    }
    if (stat.isFile() && /\.(mjs|json|md|yml|yaml|svg)$/.test(relativePath)) {
      results.push({ relativePath, text: fs.readFileSync(fullPath, "utf8") });
    }
  };
  for (const root of roots) visit(root);
  return results;
}

function textScanPasses(items, forbidden) {
  return items.every(({ text }) => forbidden.every((pattern) => !pattern.test(text)));
}

function isSourceRepositoryLayout() {
  return path.resolve(REPO_ROOT_DIR, "plugins", "antigravity-for-codex") === ROOT_DIR;
}

function repositoryInstallDocsReleaseRefCheck() {
  if (!isSourceRepositoryLayout()) {
    return { ok: true, detail: "skipped outside source layout" };
  }
  const docs = ["README.md", "docs/README.en.md", "docs/README.zh-CN.md"];
  const staleRefs = [];
  const missingCurrentRef = [];
  for (const relativePath of docs) {
    const fullPath = path.join(REPO_ROOT_DIR, relativePath);
    if (!fs.existsSync(fullPath)) {
      missingCurrentRef.push(relativePath);
      continue;
    }
    const text = fs.readFileSync(fullPath, "utf8");
    if (!text.includes(`--ref ${RELEASE_REF}`)) {
      missingCurrentRef.push(relativePath);
    }
    for (const match of text.matchAll(/antigravity-for-codex-v\d+\.\d+\.\d+/g)) {
      if (match[0] !== RELEASE_REF) {
        staleRefs.push(`${relativePath}:${match[0]}`);
      }
    }
  }
  if (missingCurrentRef.length || staleRefs.length) {
    return {
      ok: false,
      detail: [
        missingCurrentRef.length ? `missing ${RELEASE_REF} in ${missingCurrentRef.join(", ")}` : "",
        staleRefs.length ? `stale refs ${staleRefs.join(", ")}` : ""
      ].filter(Boolean).join("; ")
    };
  }
  return { ok: true };
}

function parseArgsOrExit(rawArgs) {
  try {
    return parseArgs(rawArgs);
  } catch (error) {
    process.stderr.write(`${error.message || String(error)}\n`);
    process.exit(2);
  }
}

function gitMarker(args) {
  return `[git output truncated or timed out for: git ${args.join(" ")}]`;
}

function boundedGitOutput(stdout, args, forceMarker = false) {
  const text = String(stdout || "").trim();
  const truncated = Buffer.byteLength(text, "utf8") > GIT_OUTPUT_EXCERPT_BYTES;
  const excerpt = truncated ? text.slice(0, GIT_OUTPUT_EXCERPT_BYTES) : text;
  return [
    excerpt,
    forceMarker || truncated ? gitMarker(args) : ""
  ].filter(Boolean).join("\n");
}

function runGit(args, options = {}) {
  const result = spawnSync("git", args, {
    cwd: options.cwd || process.cwd(),
    encoding: "utf8",
    maxBuffer: GIT_MAX_BUFFER,
    timeout: GIT_TIMEOUT_MS,
    killSignal: "SIGKILL"
  });
  if (result.error) {
    return boundedGitOutput(result.stdout, args, true);
  }
  if (result.status !== 0) {
    return "";
  }
  return boundedGitOutput(result.stdout, args);
}

function runGitOk(args, options = {}) {
  const result = spawnSync("git", args, {
    cwd: options.cwd || process.cwd(),
    encoding: "utf8",
    maxBuffer: GIT_MAX_BUFFER,
    timeout: GIT_TIMEOUT_MS,
    killSignal: "SIGKILL"
  });
  const stdout = String(result.stdout || "").trim();
  const stdoutTruncated = Buffer.byteLength(stdout, "utf8") > GIT_OUTPUT_EXCERPT_BYTES;
  return {
    ok: result.status === 0 && !result.error,
    stdout: stdoutTruncated ? stdout.slice(0, GIT_OUTPUT_EXCERPT_BYTES) : stdout,
    marker: result.error || stdoutTruncated ? gitMarker(args) : ""
  };
}

function reviewGateTimeoutBudgetMs(env = process.env, cliTimeoutMs = undefined) {
  const raw = env[REVIEW_GATE_TIMEOUT_ENV];
  const requested = raw === undefined || String(raw).trim() === ""
    ? REVIEW_GATE_DEFAULT_TIMEOUT_MS
    : Number(raw);
  const normalized = Number.isFinite(requested)
    ? Math.max(1000, Math.ceil(requested))
    : REVIEW_GATE_DEFAULT_TIMEOUT_MS;
  const cliTimeout = Number.isFinite(cliTimeoutMs)
    ? Math.max(1000, Math.ceil(cliTimeoutMs))
    : normalized;
  const wrapperCap = Math.max(0, REVIEW_GATE_WRAPPER_TIMEOUT_MS - REVIEW_GATE_WRAPPER_GRACE_MS);
  return Math.min(normalized, cliTimeout, wrapperCap);
}

function reviewGateDeadline(timeoutMs) {
  return {
    expiresAt: Date.now() + Math.max(0, Math.ceil(timeoutMs || 0))
  };
}

function reviewGateRemainingMs(deadline) {
  return Math.max(0, Math.ceil((deadline?.expiresAt || 0) - Date.now()));
}

function reviewGateGitCommand(args, options = {}) {
  const remaining = reviewGateRemainingMs(options.deadline);
  if (remaining <= 0) {
    return {
      status: 1,
      stdout: "",
      stderr: `review gate timeout budget exhausted before: git ${args.join(" ")}`,
      error: "review gate timeout budget exhausted",
      errorCode: "ETIMEOUT",
      timedOut: true
    };
  }
  const result = spawnSync("git", args, {
    cwd: options.cwd || process.cwd(),
    encoding: "utf8",
    maxBuffer: GIT_MAX_BUFFER,
    timeout: Math.max(1, Math.min(GIT_TIMEOUT_MS, remaining)),
    killSignal: "SIGKILL"
  });
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message || result.error) : "",
    errorCode: result.error?.code ? String(result.error.code) : "",
    timedOut: result.error?.code === "ETIMEDOUT"
  };
}

function reviewGateGitResult(args, options = {}) {
  const result = reviewGateGitCommand(args, options);
  const stdout = String(result.stdout || "").trim();
  const stdoutTruncated = Buffer.byteLength(stdout, "utf8") > GIT_OUTPUT_EXCERPT_BYTES;
  const output = stdoutTruncated ? stdout.slice(0, GIT_OUTPUT_EXCERPT_BYTES) : stdout;
  const marker = result.error || stdoutTruncated ? gitMarker(args) : "";
  return {
    command: `git ${args.join(" ")}`,
    args,
    status: result.status,
    ok: result.status === 0 && !result.error && !stdoutTruncated,
    stdout: output,
    output: [output, marker].filter(Boolean).join("\n"),
    marker,
    truncated: stdoutTruncated,
    stderr: result.stderr,
    error: result.error,
    errorCode: result.errorCode,
    timedOut: result.timedOut
  };
}

function reviewGateGitOk(args, options = {}) {
  return reviewGateGitResult(args, options);
}

function reviewGateGitFailureReason(label, result) {
  const detail = [
    result.command,
    result.errorCode,
    result.timedOut ? "timed out" : "",
    result.truncated ? "output truncated" : "",
    result.error,
    result.stderr,
    result.marker,
    result.status !== 0 ? `exit ${result.status}` : ""
  ].filter(Boolean).join("; ").trim();
  return `${label}${detail ? `: ${detail}` : ""}`;
}

function isSafeRelativePath(relativePath) {
  return Boolean(relativePath)
    && !path.isAbsolute(relativePath)
    && !relativePath.split(/[\\/]+/).includes("..");
}

function textExcerptForFile(root, relativePath) {
  if (!isSafeRelativePath(relativePath)) {
    return `Untracked file: ${relativePath}\n[skipped unsafe path]`;
  }
  const filePath = path.join(root, relativePath);
  let stat;
  try {
    stat = fs.statSync(filePath);
  } catch {
    return `Untracked file: ${relativePath}\n[skipped unreadable file]`;
  }
  if (!stat.isFile()) {
    return `Untracked file: ${relativePath}\n[skipped non-file]`;
  }
  let fd;
  let buffer;
  try {
    fd = fs.openSync(filePath, "r");
    buffer = Buffer.alloc(Math.min(stat.size, UNTRACKED_BYTES_PER_FILE + 1));
    const bytesRead = fs.readSync(fd, buffer, 0, buffer.length, 0);
    buffer = buffer.subarray(0, bytesRead);
  } catch {
    return `Untracked file: ${relativePath}\n[skipped unreadable file]`;
  } finally {
    try {
      if (fd !== undefined) fs.closeSync(fd);
    } catch {
      // Ignore close errors after a best-effort bounded read.
    }
  }
  if (buffer.includes(0)) {
    return `Untracked file: ${relativePath}\n[skipped binary file]`;
  }
  const excerpt = buffer.subarray(0, UNTRACKED_BYTES_PER_FILE).toString("utf8");
  const truncated = stat.size > UNTRACKED_BYTES_PER_FILE ? "\n[untracked file excerpt truncated]" : "";
  return `Untracked file: ${relativePath}\n${excerpt}${truncated}`;
}

function untrackedContext(root) {
  const listing = runGitOk(["ls-files", "--others", "--exclude-standard"], { cwd: root });
  const files = listing.stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const excerpts = files.slice(0, UNTRACKED_FILE_LIMIT).map((file) => textExcerptForFile(root, file));
  if (files.length > UNTRACKED_FILE_LIMIT) {
    excerpts.push(`[untracked file list truncated after ${UNTRACKED_FILE_LIMIT} files]`);
  }
  if (listing.marker) {
    excerpts.push(listing.marker);
  }
  return excerpts.join("\n\n");
}

function reviewGateUntrackedContext(root, deadline) {
  const listing = reviewGateGitOk(["ls-files", "--others", "--exclude-standard"], { cwd: root, deadline });
  if (!listing.ok) {
    return {
      trusted: false,
      reason: reviewGateGitFailureReason("git untracked file discovery failed", listing),
      context: ""
    };
  }
  const files = listing.stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const excerpts = files.slice(0, UNTRACKED_FILE_LIMIT).map((file) => textExcerptForFile(root, file));
  if (files.length > UNTRACKED_FILE_LIMIT) {
    excerpts.push(`[untracked file list truncated after ${UNTRACKED_FILE_LIMIT} files]`);
  }
  if (listing.marker) {
    excerpts.push(listing.marker);
  }
  return {
    trusted: true,
    reason: "",
    context: excerpts.join("\n\n")
  };
}

function gitContext() {
  const rootResult = runGitOk(["rev-parse", "--show-toplevel"]);
  const root = rootResult.ok ? rootResult.stdout : "";
  if (!root) {
    return "No git repository was detected. Review only the user's explicit focus text.";
  }
  const status = runGit(["status", "--short"], { cwd: root });
  const stagedStat = runGit(["diff", "--cached", "--stat"], { cwd: root });
  const workingStat = runGit(["diff", "--stat"], { cwd: root });
  const stagedDiff = runGit(["diff", "--cached", "--", "."], { cwd: root });
  const workingDiff = runGit(["diff", "--", "."], { cwd: root });
  const untracked = untrackedContext(root);
  return [
    `Repository: ${root}`,
    status ? `Status:\n${status}` : "Status: clean",
    stagedStat ? `Staged diff stat:\n${stagedStat}` : "",
    workingStat ? `Working diff stat:\n${workingStat}` : "",
    stagedDiff ? `Staged diff:\n${stagedDiff}` : "",
    workingDiff ? `Working diff:\n${workingDiff}` : "",
    untracked ? `Untracked files:\n${untracked}` : ""
  ].filter(Boolean).join("\n\n");
}

function reviewGateGitContext({ deadline } = {}) {
  const rootResult = reviewGateGitOk(["rev-parse", "--show-toplevel"], { deadline });
  const root = rootResult.ok ? rootResult.stdout : "";
  if (!root) {
    if (
      !rootResult.ok
      && !rootResult.error
      && !rootResult.errorCode
      && !rootResult.marker
      && /not a git repository|not a git repo|outside repository/i.test(rootResult.stderr || "")
    ) {
      return {
        trusted: false,
        reason: "no git repository was detected",
        context: "No git repository was detected. Review only the user's explicit focus text."
      };
    }
    const detail = [
      rootResult.errorCode,
      rootResult.error,
      rootResult.stderr,
      rootResult.marker
    ].filter(Boolean).join("; ").trim();
    return {
      trusted: false,
      reason: `git root discovery failed${detail ? `: ${detail}` : ""}`,
      context: ""
    };
  }
  const status = reviewGateGitResult(["status", "--short"], { cwd: root, deadline });
  if (!status.ok) {
    return {
      trusted: false,
      reason: reviewGateGitFailureReason("git status failed", status),
      context: ""
    };
  }
  const stagedStat = reviewGateGitResult(["diff", "--no-ext-diff", "--cached", "--stat"], { cwd: root, deadline });
  if (!stagedStat.ok) {
    return {
      trusted: false,
      reason: reviewGateGitFailureReason("git staged diff stat failed", stagedStat),
      context: ""
    };
  }
  const workingStat = reviewGateGitResult(["diff", "--no-ext-diff", "--stat"], { cwd: root, deadline });
  if (!workingStat.ok) {
    return {
      trusted: false,
      reason: reviewGateGitFailureReason("git working diff stat failed", workingStat),
      context: ""
    };
  }
  const stagedDiff = reviewGateGitResult(["diff", "--no-ext-diff", "--cached", "--", "."], { cwd: root, deadline });
  if (!stagedDiff.ok) {
    return {
      trusted: false,
      reason: reviewGateGitFailureReason("git staged diff failed", stagedDiff),
      context: ""
    };
  }
  const workingDiff = reviewGateGitResult(["diff", "--no-ext-diff", "--", "."], { cwd: root, deadline });
  if (!workingDiff.ok) {
    return {
      trusted: false,
      reason: reviewGateGitFailureReason("git working diff failed", workingDiff),
      context: ""
    };
  }
  const untracked = reviewGateUntrackedContext(root, deadline);
  if (!untracked.trusted) {
    return untracked;
  }
  if (reviewGateRemainingMs(deadline) <= 0) {
    return {
      trusted: false,
      reason: "review gate timeout budget exhausted after git context",
      context: ""
    };
  }
  const context = [
    `Repository: ${root}`,
    status.output ? `Status:\n${status.output}` : "Status: clean",
    stagedStat.output ? `Staged diff stat:\n${stagedStat.output}` : "",
    workingStat.output ? `Working diff stat:\n${workingStat.output}` : "",
    stagedDiff.output ? `Staged diff:\n${stagedDiff.output}` : "",
    workingDiff.output ? `Working diff:\n${workingDiff.output}` : "",
    untracked.context ? `Untracked files:\n${untracked.context}` : ""
  ].filter(Boolean).join("\n\n");
  return { trusted: true, reason: "", context };
}

function focusBlock(args) {
  const focus = args.positional.join(" ").trim();
  return focus ? `<focus>${focus}</focus>` : "";
}

function templatePromptFor(kind, args, preflight) {
  const template = readPromptTemplate(ROOT_DIR, kind);
  return renderTemplate(template, {
    MODEL_PROVIDER: preflight?.modelProvider || args.modelProvider || process.env.ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER || "gemini",
    MODEL: preflight?.model || args.model || "",
    GIT_CONTEXT: gitContext(),
    FOCUS_BLOCK: focusBlock(args)
  });
}

function renderCommandPrompt(command, values) {
  const templateName = command === "multi-review" || command === "review-gate" ? `${command}-role` : command;
  const template = readPromptTemplate(ROOT_DIR, templateName);
  return renderTemplate(template, {
    ROLE_NAME: values.ROLE ?? "",
    ROLE_DIRECTIVE: values.ROLE_BRIEF ?? "",
    MODEL_PROVIDER: values.MODEL_PROVIDER ?? "",
    MODEL: values.MODEL ?? "",
    GIT_CONTEXT: values.GIT_CONTEXT ?? "",
    FOCUS_BLOCK: values.TASK ?? ""
  });
}

function reviewGateEnabled() {
  const value = String(process.env.ANTIGRAVITY_FOR_CODEX_REVIEW_GATE ?? "").trim().toLowerCase();
  return value !== "" && value !== "off" && value !== "false" && value !== "0";
}

function warnReviewGate(message) {
  process.stderr.write(`[antigravity-for-codex review-gate] ${message}\n`);
}

function parseReviewGateOutput(output) {
  const text = String(output || "").trim();
  const firstLine = text.split(/\r?\n/, 1)[0] || "";
  if (firstLine.startsWith("BLOCK:")) {
    return { kind: "block", reason: firstLine.slice("BLOCK:".length).trim() || "blocked" };
  }
  if (firstLine.startsWith("ALLOW:")) {
    return { kind: "allow" };
  }
  return { kind: "invalid", firstLine };
}

function rawArgsWithoutBackground(rawArgs) {
  return rawArgs.filter((arg) => arg !== "--background");
}

function sanitizeBackgroundArgs(rawArgs) {
  return rawArgsWithoutBackground(rawArgs);
}

function jobSummary(job) {
  if (!job) return null;
  const liveness = classifyJobLiveness(job, { now: Date.now(), env: process.env });
  return {
    id: job.id,
    command: job.command,
    args: job.args,
    cwd: job.cwd,
    status: job.status,
    liveness,
    viewed: Boolean(job.viewed),
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    startedAt: job.startedAt,
    endedAt: job.endedAt,
    worker: job.worker,
    submissionState: job.submissionState || "",
    timeout: Number.isFinite(job.timeout) ? job.timeout : null,
    workerPid: job.workerPid ?? job.worker?.pid ?? null,
    lastHeartbeatAt: job.lastHeartbeatAt || "",
    lastProgressAt: job.lastProgressAt || "",
    resultViewedAt: job.resultViewedAt || "",
    idempotencyKey: job.idempotencyKey || "",
    error: job.error || "",
    cancel: job.cancel || null
  };
}

function jobResultPayload(job) {
  return {
    ...jobSummary(job),
    stdout: job.stdout || "",
    stderr: job.stderr || "",
    error: job.error || ""
  };
}

function writeJson(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function spawnStoredJobWorker(job) {
  const child = spawn(process.execPath, [COMPANION_PATH, "__run-job", job.id], {
    cwd: job.cwd,
    env: {
      ...process.env,
      ANTIGRAVITY_INTERNAL_DISPATCH: "1"
    },
    detached: process.platform !== "win32",
    stdio: "ignore"
  });
  child.unref();
  return child;
}

function queueBackgroundJob(command, rawArgs) {
  const cwd = process.cwd();
  const outcome = withWorkspaceJobLock(cwd, process.env, () => {
    let args;
    try {
      args = parseArgs(rawArgs);
    } catch (error) {
      return { exitCode: 2, stderr: `${error.message || String(error)}\n` };
    }
    const cleanArgs = sanitizeBackgroundArgs(rawArgs);
    const fingerprint = worktreeFingerprint(cwd, { env: process.env });
    const preflight = antigravityPreflight(process.env, args);
    if (!preflight.ok) {
      return {
        exitCode: 2,
        stderr: `${preflight.error || `Antigravity CLI is unavailable; missing ${preflight.missing.join(", ")}.`}\n`
      };
    }
    const executionControls = {
      provider: preflight.modelProvider,
      model: preflight.model,
      sandbox: process.env.ANTIGRAVITY_FOR_CODEX_SANDBOX || "",
      timeout: String(args.timeout || "")
    };
    const idempotencyKey = deriveJobIdempotencyKey({
      command,
      args: cleanArgs,
      cwd,
      workspaceFingerprint: fingerprint.fingerprint,
      executionControls
    });
    if (fingerprint.status === "trusted") {
      const reusable = findReusableJob({ command, args: cleanArgs, cwd, idempotencyKey }, process.env);
      if (reusable) {
        return { payload: { status: reusable.status, jobId: reusable.id, reused: true } };
      }
    }

    const activeJobs = listJobs(cwd, process.env)
      .map((job) => ({ job, liveness: classifyJobLiveness(job, { now: Date.now(), env: process.env }) }))
      .filter(({ liveness }) => ["queued", "healthy", "suspect"].includes(liveness.state));
    const cap = maxActiveJobs(process.env);
    if (activeJobs.length >= cap) {
      return {
        exitCode: 2,
        stderr: `Refusing to queue background job: maximum active background jobs (${cap}) reached.\n`
      };
    }

    const job = createJob({ command, args: cleanArgs, cwd }, process.env);
    const stamped = updateJob(job.id, (draft) => {
      const updatedAt = new Date().toISOString();
      draft.idempotencyKey = idempotencyKey;
      draft.workspaceFingerprint = fingerprint.fingerprint;
      draft.executionControls = executionControls;
      draft.timeout = args.timeout;
      draft.submissionState = "queued";
      draft.updatedAt = updatedAt;
      return draft;
    }, cwd, process.env);
    if (!stamped) {
      const message = "Metadata persistence failed before worker start.";
      markJobMetadataPersistenceFailed(job.id, message, cwd, process.env);
      return {
        exitCode: 2,
        stderr: `Failed to queue background job: ${message}\n`
      };
    }
    spawnStoredJobWorker(stamped);
    return { payload: { status: "queued", jobId: stamped.id, reused: false } };
  });

  if (!outcome) {
    process.stderr.write("Failed to queue background job: workspace job lock is busy.\n");
    process.exit(2);
  }
  if (outcome.stderr) {
    process.stderr.write(outcome.stderr);
    process.exit(outcome.exitCode || 2);
  }
  writeJson(outcome.payload);
}

function writeReviewOperationReport(kind, args, result, startedAt, endedAt, parsed) {
  try {
    writeOperationReport(process.cwd(), operationReport({ command: kind, args, result, startedAt, endedAt, parsed }), process.env);
  } catch {
    // Report writes are best-effort and must not mask review output or failures.
  }
}

function runSetupOrCapabilities(command, rawArgs) {
  const args = parseArgsOrExit(rawArgs);
  if (args.help) {
    process.stdout.write(usage());
    return;
  }
  const preflight = antigravityPreflight(process.env, args);
  const diagnostics = antigravityModelDiagnostics(process.env, args);
  const payload = { ok: preflight.ok, provider: preflight, modelCatalog: diagnostics.modelCatalog };
  if (command === "capabilities") {
    payload.commands = USER_VISIBLE_COMMANDS;
  }
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(command === "setup" ? 0 : (preflight.ok ? 0 : 1));
}

function runReview(kind, rawArgs) {
  const startedAt = new Date().toISOString();
  let args;
  try {
    args = parseArgs(rawArgs);
  } catch (error) {
    const stderr = error.message || String(error);
    const result = { status: 2, stdout: "", stderr, error: "", errorCode: "" };
    writeReviewOperationReport(kind, {}, { ...result, outcome: classifyAgyOutcome(result) }, startedAt, new Date().toISOString());
    process.stderr.write(`${stderr}\n`);
    process.exit(2);
  }
  if (args.help) {
    process.stdout.write(usage(kind));
    return;
  }
  if (args.background) {
    return queueBackgroundJob(kind, rawArgs);
  }
  const preflight = antigravityPreflight(process.env, args);
  if (!preflight.ok) {
    const stderr = preflight.error || `Antigravity CLI is unavailable; missing ${preflight.missing.join(", ")}.`;
    const result = { status: 2, stdout: "", stderr, error: "", errorCode: "" };
    const endedAt = new Date().toISOString();
    writeReviewOperationReport(kind, args, { ...result, outcome: classifyAgyOutcome(result) }, startedAt, endedAt);
    process.stderr.write(`${stderr}\n`);
    process.exit(2);
  }
  let prompt = templatePromptFor(kind, args, preflight);
  if (args.structured || args.json) {
    prompt = appendStructuredReviewInstructions(prompt);
  }
  const result = antigravityPrint(prompt, { ...args, preflight }, process.env);
  const endedAt = new Date().toISOString();
  if (result.status !== 0) {
    writeReviewOperationReport(kind, args, result, startedAt, endedAt);
    process.stderr.write(`${result.stderr || result.error || "Antigravity review failed."}\n`);
    process.exit(result.status);
  }
  let parsed;
  if (args.structured || args.json) {
    try {
      parsed = validateStructuredReview(extractJsonObject(result.stdout));
    } catch (error) {
      const stderr = `Structured review output invalid: ${error.message || String(error)}`;
      const failedResult = {
        ...result,
        status: 1,
        stderr,
        errorCode: "ESTRUCTUREDOUTPUT",
        outcome: { kind: "malformed-output", ok: false, retryable: true, message: stderr }
      };
      writeReviewOperationReport(kind, args, failedResult, startedAt, new Date().toISOString());
      process.stderr.write(`${stderr}\n`);
      process.exit(1);
    }
  }
  writeReviewOperationReport(kind, args, result, startedAt, endedAt, parsed);
  if (args.json) {
    process.stdout.write(`${JSON.stringify(parsed)}\n`);
    return;
  }
  if (args.structured) {
    process.stdout.write(`${JSON.stringify(parsed, null, 2)}\n`);
    return;
  }
  process.stdout.write(result.stdout);
  if (!result.stdout.endsWith("\n")) {
    process.stdout.write("\n");
  }
}

function runReport(rawArgs) {
  const args = parseArgsOrExit(rawArgs);
  if (args.help) {
    process.stdout.write(usage("report"));
    return;
  }
  if (!args.latest) {
    process.stderr.write("report requires --latest.\n");
    process.exit(2);
  }
  process.stdout.write(`${JSON.stringify(latestReport(process.cwd(), process.env))}\n`);
}

function runRoles(rawArgs) {
  const args = parseArgsOrExit(rawArgs);
  if (args.help) {
    process.stdout.write(usage("roles"));
    return;
  }
  process.stdout.write(`${JSON.stringify({ roles: REVIEW_ROLES, packs: BUILT_IN_ROLE_PACKS })}\n`);
}

function parseDoctorArgs(rawArgs) {
  const args = {
    help: false,
    json: false,
    modelProvider: undefined,
    model: undefined
  };
  const readDoctorOptionValue = (tokens, index, option) => {
    const value = readOptionValue(tokens, index, option);
    if (value.trim() === "") {
      throw new Error(`Missing value for ${option}.`);
    }
    return value;
  };
  const readDoctorEqualsValue = (token, option) => {
    const value = token.slice(`${option}=`.length);
    if (value.trim() === "") {
      throw new Error(`Missing value for ${option}.`);
    }
    return value;
  };
  for (let index = 0; index < rawArgs.length; index += 1) {
    const token = rawArgs[index];
    if (token === "--help" || token === "-h" || token === "help") {
      args.help = true;
    } else if (token === "--json") {
      args.json = true;
    } else if (token === "--model-provider") {
      args.modelProvider = readDoctorOptionValue(rawArgs, index, token);
      index += 1;
    } else if (token.startsWith("--model-provider=")) {
      args.modelProvider = readDoctorEqualsValue(token, "--model-provider");
    } else if (token === "--model") {
      args.model = readDoctorOptionValue(rawArgs, index, token);
      index += 1;
    } else if (token.startsWith("--model=")) {
      args.model = readDoctorEqualsValue(token, "--model");
    } else {
      throw new Error(`Unknown doctor argument: ${token}.`);
    }
  }
  return args;
}

function doctorWantsJson(rawArgs) {
  return rawArgs.includes("--json");
}

function conciseDiagnostic(value) {
  return String(value || "")
    .trim()
    .split(/\r?\n/, 1)[0]
    .trim();
}

function runDoctor(rawArgs) {
  let args;
  try {
    args = parseDoctorArgs(rawArgs);
  } catch (error) {
    const message = error.message || String(error);
    if (doctorWantsJson(rawArgs)) {
      writeJson({ ok: false, error: message });
    } else {
      process.stderr.write(`${message}\n`);
    }
    process.exit(2);
  }
  if (args.help) {
    process.stdout.write(usage("doctor"));
    return;
  }
  const payload = antigravityDoctor(process.env, {
    modelProvider: args.modelProvider,
    model: args.model
  });
  if (args.json) {
    writeJson(payload);
    return;
  }
  const lines = [
    `Antigravity command: ${payload.agy.command}`,
    `Ready: ${payload.ok ? "yes" : "no"}`,
    `Available: ${payload.agy.available ? "yes" : "no"}`,
    `Prompt support: ${payload.agy.capabilities.prompt ? "yes" : "no"}`,
    `Log diagnostics: ${payload.agy.capabilities.logFile ? "yes" : "no"}`,
    `Current selection: ${payload.selected.current.ok ? `${payload.selected.current.modelProvider} ${payload.selected.current.model}` : `error - ${payload.selected.current.error}`}`,
    `Gemini models: ${payload.models.gemini.length}`,
    `Claude models: ${payload.models.claude.length}`,
    `Unsupported models rejected: ${payload.models.unsupported.length}`,
    `Hooks supported: ${payload.hooks.supportedEvents.join(", ")}`,
    `Hooks unsupported: ${payload.hooks.unsupportedEvents.join(", ")}`
  ];
  if (!payload.agy.available) {
    lines.push(`Antigravity error: ${conciseDiagnostic(payload.agy.helpError) || `help exited ${payload.agy.helpStatus}`}`);
  }
  if (!payload.models.available) {
    lines.push(`Models error: ${conciseDiagnostic(payload.models.error) || `models exited ${payload.models.status}`}`);
  }
  for (const [provider, selection] of Object.entries(payload.selected.providers)) {
    if (!selection.ok) {
      lines.push(`${provider === "gemini" ? "Gemini" : "Claude"} selection error: ${selection.error}`);
    }
  }
  process.stdout.write(`${lines.join("\n")}\n`);
}

function requireJobId(rawArgs, command) {
  const args = parseArgsOrExit(rawArgs);
  if (args.help || args.positional.length !== 1) {
    process.stdout.write(`Usage: antigravity-companion.mjs ${command} <job-id>\n`);
    process.exit(args.help ? 0 : 2);
  }
  return args.positional[0];
}

function runJobs(rawArgs) {
  const args = parseArgsOrExit(rawArgs);
  if (args.help) {
    process.stdout.write("Usage: antigravity-companion.mjs jobs\n");
    return;
  }
  writeJson({ jobs: listJobs(process.cwd(), process.env).map(jobSummary) });
}

function runStatus(rawArgs) {
  const jobId = requireJobId(rawArgs, "status");
  const job = readJob(jobId, process.cwd(), process.env);
  if (!job) {
    process.stderr.write(`Unknown job "${jobId}".\n`);
    process.exit(1);
  }
  writeJson(jobSummary(job));
}

function runResult(rawArgs) {
  const jobId = requireJobId(rawArgs, "result");
  const job = markJobViewed(jobId, process.cwd(), process.env);
  if (!job) {
    process.stderr.write(`Unknown job "${jobId}".\n`);
    process.exit(1);
  }
  writeJson(jobResultPayload(job));
}

function runCancel(rawArgs) {
  const jobId = requireJobId(rawArgs, "cancel");
  const job = cancelJob(jobId, process.cwd(), process.env);
  if (!job) {
    process.stderr.write(`Unknown job "${jobId}".\n`);
    process.exit(1);
  }
  writeJson(jobSummary(job));
}

function runMailbox(rawArgs) {
  const [action = "", ...rest] = rawArgs;
  const args = parseArgsOrExit(rest);
  if (args.help || !action) {
    process.stdout.write(usage("mailbox"));
    process.exit(action ? 0 : 2);
  }
  try {
    if (action === "list") {
      return writeJson(listMailboxThreads(process.cwd(), process.env));
    }
    if (action === "post") {
      return writeJson(postMailboxMessage({
        thread: args.thread,
        message: args.message,
        cwd: process.cwd()
      }, process.env));
    }
    if (action === "show") {
      return writeJson(showMailboxThread(args.thread, process.cwd(), process.env));
    }
  } catch (error) {
    process.stderr.write(`${error.message || String(error)}\n`);
    process.exit(2);
  }
  process.stderr.write(`Unknown mailbox action "${action}".\n`);
  process.exit(2);
}

function runLeases(rawArgs) {
  const [action = "", ...rest] = rawArgs;
  const args = parseArgsOrExit(rest);
  if (args.help || !action) {
    process.stdout.write(usage("leases"));
    process.exit(action ? 0 : 2);
  }
  try {
    if (action === "claim") {
      return writeJson(claimLease({
        role: args.role,
        ttlSeconds: args.ttlSeconds,
        cwd: process.cwd()
      }, process.env));
    }
    if (action === "list") {
      return writeJson(listLeases(process.cwd(), process.env));
    }
    if (action === "release") {
      return writeJson(releaseLease(args.id, process.cwd(), process.env));
    }
  } catch (error) {
    process.stderr.write(`${error.message || String(error)}\n`);
    process.exit(2);
  }
  process.stderr.write(`Unknown leases action "${action}".\n`);
  process.exit(2);
}

function githubActionOptions(args, env = process.env) {
  const modelProvider = normalizedModelProvider(args.modelProvider || env[MODEL_PROVIDER_ENV]);
  const explicitModel =
    args.model
    || env[MODEL_ENV]
    || "";
  const selected = explicitModel ? selectedModel(env, { modelProvider, model: explicitModel }) : { modelProvider, model: "" };
  return {
    modelProvider: selected.modelProvider,
    model: explicitModel ? selected.model : "",
    releaseRef: args.ref || RELEASE_REF,
    timeoutMinutes: args.timeoutMinutes === 0 ? undefined : args.timeoutMinutes
  };
}

function runGithubActions(rawArgs) {
  const [action = "", ...rest] = rawArgs;
  const args = parseArgsOrExit(rest);
  if (args.help || !action) {
    process.stdout.write(usage("github-actions"));
    process.exit(action ? 0 : 2);
  }
  try {
    if (action === "render") {
      process.stdout.write(renderWorkflow(ROOT_DIR, githubActionOptions(args)));
      return;
    }
    if (action === "init") {
      const workflow = renderWorkflow(ROOT_DIR, githubActionOptions(args));
      const target = writeWorkflow(process.cwd(), workflow, { force: args.force });
      return writeJson({ status: "written", path: target });
    }
    if (action === "validate") {
      const target = args.workflowPath || path.join(process.cwd(), ".github", "workflows", "antigravity-for-codex-review.yml");
      const text = fs.readFileSync(target, "utf8");
      const validation = validateWorkflow(text);
      writeJson(validation);
      if (!validation.ok) {
        process.exit(1);
      }
      return;
    }
  } catch (error) {
    process.stderr.write(`${error.message || String(error)}\n`);
    process.exit(2);
  }
  process.stderr.write(`Unknown github-actions action "${action}".\n`);
  process.exit(2);
}

function runReserveJob(rawArgs) {
  if (rawArgs.includes("--help") || rawArgs.includes("-h") || rawArgs[0] === "help" || rawArgs.length < 1) {
    process.stdout.write("Usage: antigravity-companion.mjs reserve-job <review|adversarial-review|multi-review|plan|rescue> [args]\n");
    process.exit(rawArgs.length < 1 ? 2 : 0);
  }
  const [command, ...commandArgs] = rawArgs;
  try {
    const job = reserveJob({ command, args: commandArgs, cwd: process.cwd() }, process.env);
    writeJson({ status: "reserved", jobId: job.id });
  } catch (error) {
    process.stderr.write(`${error.message || String(error)}\n`);
    process.exit(2);
  }
}

function runReservedJob(rawArgs) {
  const jobId = requireJobId(rawArgs, "run-reserved-job");
  const job = claimReservedJob(process.cwd(), jobId, process.env);
  if (!job) {
    process.stderr.write(`Job "${jobId}" is not reserved.\n`);
    process.exit(2);
  }
  spawnStoredJobWorker(job);
  writeJson({ status: "queued", jobId: job.id });
}

function appendCapped(chunks, state, chunk) {
  const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
  state.total += buffer.length;
  if (state.kept >= CHILD_OUTPUT_MAX_BUFFER) {
    return;
  }
  const available = CHILD_OUTPUT_MAX_BUFFER - state.kept;
  const next = buffer.length > available ? buffer.subarray(0, available) : buffer;
  chunks.push(next);
  state.kept += next.length;
}

function runChildProcessAsync(command, args, options = {}) {
  return new Promise((resolve) => {
    const stdoutChunks = [];
    const stderrChunks = [];
    const stdoutState = { kept: 0, total: 0 };
    const stderrState = { kept: 0, total: 0 };
    let spawnError = null;
    let timedOut = false;
    let settled = false;
    let killTimer = null;
    const child = spawn(command, args, {
      cwd: options.cwd || process.cwd(),
      env: options.env || process.env,
      stdio: ["ignore", "pipe", "pipe"]
    });

    const timeoutTimer = Number.isFinite(options.timeout) && options.timeout > 0
      ? setTimeout(() => {
        timedOut = true;
        child.kill("SIGTERM");
        killTimer = setTimeout(() => {
          child.kill("SIGKILL");
        }, 2000);
        killTimer.unref?.();
      }, options.timeout)
      : null;
    timeoutTimer?.unref?.();

    child.stdout.on("data", (chunk) => appendCapped(stdoutChunks, stdoutState, chunk));
    child.stderr.on("data", (chunk) => appendCapped(stderrChunks, stderrState, chunk));
    child.on("error", (error) => {
      spawnError = error;
    });
    child.on("close", (code, signal) => {
      if (settled) return;
      settled = true;
      if (timeoutTimer) clearTimeout(timeoutTimer);
      if (killTimer) clearTimeout(killTimer);
      const stdout = Buffer.concat(stdoutChunks).toString("utf8");
      const stderr = Buffer.concat(stderrChunks).toString("utf8");
      resolve({
        status: code ?? (spawnError ? 1 : (signal ? 1 : 0)),
        stdout,
        stderr,
        error: spawnError
          ? String(spawnError.message || spawnError)
          : (timedOut ? `Command timed out after ${options.timeout} ms.` : ""),
        errorCode: spawnError?.code ? String(spawnError.code) : (timedOut ? "ETIMEDOUT" : ""),
        signal: signal || "",
        timedOut,
        stdoutBytes: stdoutState.total,
        stderrBytes: stderrState.total
      });
    });
  });
}

function backgroundSupervisorTimeoutMs(job) {
  const timeout = Number(job?.timeout);
  if (!Number.isFinite(timeout) || timeout <= 0) {
    return DEFAULT_TIMEOUT_MS;
  }
  return timeout + BACKGROUND_SUPERVISOR_TIMEOUT_GRACE_MS;
}

async function runStoredJob(rawArgs) {
  if (process.env.ANTIGRAVITY_INTERNAL_DISPATCH !== "1") {
    process.stderr.write("__run-job requires internal dispatch.\n");
    process.exit(2);
  }
  const jobId = requireJobId(rawArgs, "__run-job");
  const job = readJob(jobId, process.cwd(), process.env);
  if (!job) {
    process.stderr.write(`Unknown job "${jobId}".\n`);
    process.exit(1);
  }
  const running = markJobRunning(jobId, {
    pid: process.pid,
    identity: captureProcessIdentity(process.pid)
  }, job.cwd, process.env);
  if (!running) {
    process.stderr.write(`Unknown job "${jobId}".\n`);
    process.exit(1);
  }
  if (isTerminalJobStatus(running.status)) {
    process.exit(0);
  }

  const heartbeat = setInterval(() => {
    updateJob(jobId, (job) => {
      if (isTerminalJobStatus(job.status)) {
        return job;
      }
      const timestamp = new Date().toISOString();
      job.lastHeartbeatAt = timestamp;
      job.updatedAt = timestamp;
      return job;
    }, running.cwd, process.env);
  }, jobHeartbeatIntervalMs(process.env));
  heartbeat.unref?.();

  try {
    const result = await runChildProcessAsync(process.execPath, [COMPANION_PATH, running.command, ...running.args], {
      cwd: running.cwd,
      env: process.env,
      timeout: backgroundSupervisorTimeoutMs(running)
    });
    finishJob(jobId, {
      status: result.status ?? 1,
      stdout: result.stdout || "",
      stderr: result.stderr || "",
      error: result.error || ""
    }, running.cwd, process.env);
  } finally {
    clearInterval(heartbeat);
  }
}

function runReviewGate(rawArgs) {
  if (!reviewGateEnabled()) {
    return;
  }
  const args = parseArgsOrExit(rawArgs);
  const deadline = reviewGateDeadline(reviewGateTimeoutBudgetMs(process.env, args.timeout));
  if (args.help) {
    process.stdout.write("Usage: antigravity-companion.mjs review-gate [--model-provider gemini|claude] [--model <label>] [focus]\n");
    return;
  }

  let result;
  try {
    const preflightBudget = reviewGateRemainingMs(deadline);
    if (preflightBudget <= 0) {
      warnReviewGate("timeout budget exhausted before preflight; allowing stop");
      return;
    }
    const preflight = antigravityPreflight(process.env, { ...args, timeout: preflightBudget });
    if (reviewGateRemainingMs(deadline) <= 0) {
      warnReviewGate("timeout budget exhausted after preflight; allowing stop");
      return;
    }
    if (!preflight.ok) {
      warnReviewGate(`runtime unavailable; allowing stop: ${preflight.error || `missing ${preflight.missing.join(", ")}`}`);
      return;
    }
    const context = reviewGateGitContext({ deadline });
    if (!context.trusted) {
      warnReviewGate(`${context.reason || "repository context unavailable"}; allowing stop`);
      return;
    }
    const remaining = reviewGateRemainingMs(deadline);
    if (remaining <= 0) {
      warnReviewGate("timeout budget exhausted before model call; allowing stop");
      return;
    }
    const prompt = renderCommandPrompt("review-gate", {
      ROLE: "stop-gate",
      ROLE_BRIEF: "Block only concrete issues that should prevent Codex from stopping.",
      TASK: focusBlock(args),
      GIT_CONTEXT: context.context,
      MODEL_PROVIDER: preflight.modelProvider,
      MODEL: preflight.model
    });
    result = antigravityPrint(prompt, { ...args, timeout: remaining, preflight }, process.env);
  } catch (error) {
    warnReviewGate(`runtime error; allowing stop: ${error.message || String(error)}`);
    return;
  }

  if (result.status !== 0) {
    warnReviewGate(`runtime failed; allowing stop: ${result.stderr || result.error || `exit ${result.status}`}`);
    return;
  }

  const verdict = parseReviewGateOutput(result.stdout);
  if (verdict.kind === "block") {
    process.stdout.write(`${JSON.stringify({ decision: "block", reason: verdict.reason })}\n`);
    return;
  }
  if (verdict.kind === "allow") {
    return;
  }
  warnReviewGate("invalid output; allowing stop");
}

async function runMultiReview(rawArgs) {
  const args = parseArgsOrExit(rawArgs);
  if (args.help) {
    process.stdout.write(usage("multi-review"));
    return;
  }
  if (args.background) {
    return queueBackgroundJob("multi-review", rawArgs);
  }
  const preflight = antigravityPreflight(process.env, args);
  if (!preflight.ok) {
    process.stderr.write(`${preflight.error || `Antigravity CLI is unavailable; missing ${preflight.missing.join(", ")}.`}\n`);
    process.exit(2);
  }
  let roles;
  try {
    roles = resolveRoles({ roles: args.roles, rolePack: args.rolePack });
  } catch (error) {
    process.stderr.write(`${error.message || String(error)}\n`);
    process.exit(2);
  }
  const sharedGitContext = gitContext();
  const task = focusBlock(args);
  const advisoryLeaseIds = [];
  if (args.useMailbox) {
    try {
      postMailboxMessage({
        thread: "multi-review",
        message: `Started Antigravity multi-review with roles: ${roles.map((role) => role.name).join(", ")}`,
        cwd: process.cwd()
      }, process.env);
    } catch (error) {
      process.stderr.write(`[antigravity-for-codex advisory] mailbox unavailable: ${error.message || String(error)}\n`);
    }
  }
  if (args.advisoryLeases) {
    for (const role of roles) {
      try {
        const claim = claimLease({ role: role.name, ttlSeconds: 900, cwd: process.cwd() }, process.env);
        advisoryLeaseIds.push(claim.leaseId);
      } catch (error) {
        process.stderr.write(`[antigravity-for-codex advisory] lease unavailable: ${error.message || String(error)}\n`);
        break;
      }
    }
  }
  const results = await Promise.all(roles.map(async (role) => {
    const prompt = renderCommandPrompt("multi-review", {
      ROLE: role.name,
      ROLE_BRIEF: role.brief,
      TASK: task,
      GIT_CONTEXT: sharedGitContext,
      MODEL_PROVIDER: preflight?.modelProvider || args.modelProvider || process.env.ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER || "gemini",
      MODEL: preflight?.model || args.model || ""
    });
    const result = await antigravityPrintAsync(prompt, { ...args, preflight }, process.env);
    return { role: role.name, result };
  }));

  let failed = false;
  for (const { role, result } of results) {
    const body = result.stdout || result.stderr || result.error;
    const timeoutNote = result.timedOut ? `${body ? "\n" : ""}[timed out]` : "";
    process.stdout.write(`## ${role}\n${body || "No output."}${timeoutNote}\n\n`);
    if (result.status !== 0) {
      failed = true;
    }
  }
  for (const leaseId of advisoryLeaseIds) {
    try {
      releaseLease(leaseId, process.cwd(), process.env);
    } catch {
      // Advisory leases must not affect review outcome.
    }
  }
  process.exit(failed ? 1 : 0);
}

function releaseCheckResult(ok, name, detail = "") {
  return { ok, name, detail };
}

function expectedSkills() {
  return [
    "antigravity-review",
    "antigravity-adversarial-review",
    "antigravity-multi-review",
    "antigravity-plan",
    "antigravity-rescue",
    "antigravity-review-gate",
    "antigravity-github-actions-review",
    "antigravity-role-packs",
    "antigravity-status",
    "antigravity-result",
    "antigravity-cancel",
    "antigravity-collaboration-loop",
    "antigravity-mailbox",
    "antigravity-leases"
  ];
}

function expectedPublicCommands() {
  return [
    "review",
    "adversarial-review",
    "multi-review",
    "doctor",
    "plan",
    "rescue",
    "review-gate",
    "real-smoke",
    "release-check",
    "report",
    "roles",
    "jobs",
    "status",
    "result",
    "cancel",
    "reserve-job",
    "run-reserved-job",
    "mailbox",
    "leases",
    "github-actions"
  ];
}

function runReleaseCheck(rawArgs) {
  const args = parseArgsOrExit(rawArgs);
  if (args.help) {
    process.stdout.write("Usage: antigravity-companion.mjs release-check\n");
    return;
  }

  let manifest = {};
  try {
    manifest = JSON.parse(readText(".codex-plugin/plugin.json"));
  } catch (error) {
    process.stderr.write(`release-check failed: cannot read manifest: ${error.message || String(error)}\n`);
    process.exit(1);
  }

  const companion = readText("scripts/antigravity-companion.mjs");
  const runtime = readText("scripts/lib/antigravity-runtime.mjs");
  const versionHelper = readText("scripts/lib/version.mjs");
  const githubActions = readText("scripts/lib/github-actions.mjs");
  const jobs = readText("scripts/lib/jobs.mjs");
  const reports = readText("scripts/lib/reports.mjs");
  const mailbox = readText("scripts/lib/mailbox.mjs");
  const leases = readText("scripts/lib/leases.mjs");
  const readme = readText("README.md");
  const changelog = readText("CHANGELOG.md");
  const antigravityDocs = `${readme}\n${changelog}`;
  let naturalLanguageRoutingContract = {
    routedModelSkills: [],
    requiredAnchors: [],
    requiredPolicyPhrases: [],
    requiredMarkers: [],
    skillMarkers: {},
    userExamplesStart: "",
    userExamplesEnd: "",
    forbiddenUserExampleSubstrings: [],
    githubActionsInitForbiddenSubstrings: [],
    githubActionsInitForbiddenPaths: []
  };
  let naturalLanguageRoutingContractLoaded = false;
  try {
    naturalLanguageRoutingContract = JSON.parse(readText(path.join("contracts", "natural-language-routing.json")));
    naturalLanguageRoutingContractLoaded = true;
  } catch {
    naturalLanguageRoutingContractLoaded = false;
  }
  const routedSkillDocs = (naturalLanguageRoutingContract.routedModelSkills || []).map((skillName) => ({
    skillName,
    text: exists(path.join("skills", skillName, "SKILL.md")) ? readText(path.join("skills", skillName, "SKILL.md")) : ""
  }));
  const routedSkillsHaveNaturalLanguageRouting = routedSkillDocs.every(({ text }) =>
    (naturalLanguageRoutingContract.requiredAnchors || []).every((anchor) => text.includes(anchor))
      && (naturalLanguageRoutingContract.requiredPolicyPhrases || []).every((phrase) => text.includes(phrase))
      && (naturalLanguageRoutingContract.requiredMarkers || []).every((marker) => text.includes(marker))
  );
  const routedSkillsHaveExpectedMarkers = routedSkillDocs.every(({ skillName, text }) =>
    ((naturalLanguageRoutingContract.skillMarkers || {})[skillName] || []).every((marker) => text.includes(marker))
  );
  const extractMarkdownSection = (text, startMarker, endMarker) => {
    const start = text.indexOf(startMarker);
    if (start === -1) {
      return "";
    }
    const bodyStart = start + startMarker.length;
    const end = text.indexOf(endMarker, bodyStart);
    if (end === -1) {
      return "";
    }
    return text.slice(bodyStart, end);
  };
  const routedSkillUserExamplesHideInternalFlags = routedSkillDocs.every(({ text }) => {
    const routingStart = text.indexOf("## Natural-Language Model Routing");
    if (routingStart === -1) {
      return false;
    }
    const userExamples = extractMarkdownSection(
      text.slice(routingStart),
      naturalLanguageRoutingContract.userExamplesStart,
      naturalLanguageRoutingContract.userExamplesEnd
    );
    return userExamples
      && (naturalLanguageRoutingContract.forbiddenUserExampleSubstrings || []).every((forbidden) => !userExamples.includes(forbidden));
  });
  const repoTextIfExists = (relativePath) => {
    try {
      return readDistributionText(relativePath);
    } catch {
      return "";
    }
  };
  const githubActionsForbiddenPathDocs = (naturalLanguageRoutingContract.githubActionsInitForbiddenPaths || [])
    .map((relativePath) => ({ relativePath, text: repoTextIfExists(relativePath) }));
  const pluginPathPrefix = `plugins${path.sep}antigravity-for-codex${path.sep}`;
  const githubActionsForbiddenPluginPathsExist = githubActionsForbiddenPathDocs
    .filter(({ relativePath }) => path.normalize(relativePath).startsWith(pluginPathPrefix))
    .every(({ text }) => Boolean(text));
  const githubActionsInitHidesInternalFlags = githubActionsForbiddenPathDocs
    .filter(({ text }) => Boolean(text))
    .every(({ text }) => {
      return text
        && (naturalLanguageRoutingContract.githubActionsInitForbiddenSubstrings || []).every((forbidden) => !text.includes(forbidden));
    });
  const hooks = exists("hooks/hooks.json") ? readText("hooks/hooks.json") : "";
  const renderedWorkflow = renderWorkflow(ROOT_DIR, { releaseRef: RELEASE_REF });
  const workflowValidation = validateWorkflow(renderedWorkflow);
  const repositoryInstallDocs = repositoryInstallDocsReleaseRefCheck();
  const matureCommands = expectedPublicCommands();
  const manifestCapabilities = Array.isArray(manifest.interface?.capabilities) ? manifest.interface.capabilities : [];
  const printHotPath = runtime.match(/export function antigravityPrintArgs[\s\S]*?export function antigravityPrint\(/)?.[0] || "";
  const shippedTexts = shippedPluginTexts();
  const executableTexts = shippedPluginTexts([
    "scripts",
    "hooks",
    "prompts",
    "templates",
    "contracts",
    "schemas",
    ".codex-plugin/plugin.json"
  ]);
  const docsTexts = shippedPluginTexts(["README.md", "CHANGELOG.md", "skills"]);
  const doctorText = shippedTexts.find(({ relativePath }) => relativePath === "scripts/lib/doctor.mjs")?.text || "";
  const claudeWord = "cla" + "ude";
  const fableWord = "Fa" + "ble";
  const fallbackModelFlag = "--fallback-" + "model";
  const ultrareviewWord = "ultra" + "review";
  const ucWord = "ultra" + "code";
  const forbiddenClaudeNativePatterns = [
    new RegExp(`${claudeWord}-${fableWord}`, "i"),
    new RegExp(`${fableWord} 5`, "i"),
    new RegExp(`@anthropic-ai/${claudeWord}-agent-sdk`, "i"),
    new RegExp(fallbackModelFlag, "i"),
    new RegExp(`${claudeWord} ${ultrareviewWord}`, "i"),
    new RegExp(ucWord, "i")
  ];
  const forbiddenRawClaudeExecutionPatterns = [
    new RegExp(`\\b(?:spawn|spawnSync|execFile|execFileSync)\\(\\s*["']${claudeWord}["']`, "i"),
    new RegExp(`\\b(?:exec|execSync)\\(\\s*["'][^"']*\\b${claudeWord}\\b`, "i"),
    new RegExp(`\\b(?:command|cmd|binary|executable)\\s*[:=]\\s*["']${claudeWord}["']`, "i"),
    new RegExp(`\\b${claudeWord}\\s+-p\\b`, "i")
  ];
  const forbiddenLocalPathPatterns = [
    /\/Users\/[A-Za-z0-9._/-]+/,
    /\/home\/[A-Za-z0-9._/-]+/,
    /\/private\/var\/folders\//,
    /[A-Za-z]:\\Users\\/
  ];
  const checks = [
    releaseCheckResult(manifest.name === "antigravity-for-codex", "manifest-name"),
    releaseCheckResult(manifest.version === PLUGIN_VERSION, "manifest-version"),
    releaseCheckResult(versionHelper.includes(`PLUGIN_VERSION = "${manifest.version}"`), "version-helper"),
    releaseCheckResult(readme.includes(`Version: ${PLUGIN_VERSION}`) && changelog.includes(`## ${PLUGIN_VERSION} `), "docs-version-aligned"),
    releaseCheckResult(RELEASE_REF === `antigravity-for-codex-v${PLUGIN_VERSION}`, "release-ref-derived"),
    releaseCheckResult(repositoryInstallDocs.ok, "repository-install-docs-release-ref", repositoryInstallDocs.detail),
    releaseCheckResult(manifest.skills === "./skills/", "manifest-skills"),
    releaseCheckResult(manifestCapabilities.includes("Explicit Gemini or Claude model selection"), "manifest-model-policy"),
    releaseCheckResult(exists("scripts/lib/agy-capabilities.mjs"), "agy-capabilities-module"),
    releaseCheckResult(exists("scripts/lib/agy-outcome.mjs"), "agy-outcome-module"),
    releaseCheckResult(
      exists("scripts/lib/doctor.mjs")
        && companion.includes('"doctor"')
        && doctorText.includes("runDoctorCommand")
        && doctorText.includes("typeof result.then"),
      "doctor-command"
    ),
    releaseCheckResult(exists("scripts/lib/job-lifecycle.mjs") && exists("scripts/lib/worktree-fingerprint.mjs"), "job-lifecycle-fingerprint"),
    releaseCheckResult(exists("scripts/lib/hook-compat.mjs"), "hook-compat-module"),
    releaseCheckResult(textScanPasses(executableTexts, forbiddenClaudeNativePatterns), "no-claude-native-executable-leakage"),
    releaseCheckResult(textScanPasses(executableTexts, forbiddenRawClaudeExecutionPatterns), "no-raw-claude-executable-invocation"),
    releaseCheckResult(docsTexts.some(({ text }) => text.includes("not Antigravity features") || text.includes("does not claim Claude SDK")), "docs-negative-claude-boundary"),
    releaseCheckResult(textScanPasses(shippedTexts, forbiddenLocalPathPatterns), "no-local-absolute-paths"),
    releaseCheckResult(Array.isArray(manifest.interface?.defaultPrompt) && manifest.interface.defaultPrompt.length <= 3, "manifest-default-prompts-limit"),
    releaseCheckResult(String(manifest.interface?.composerIcon || "").startsWith("./assets/"), "manifest-composer-icon-relative"),
    releaseCheckResult(String(manifest.interface?.logo || "").startsWith("./assets/"), "manifest-logo-relative"),
    releaseCheckResult((manifest.interface?.screenshots || []).every((item) => String(item).startsWith("./assets/")), "manifest-screenshots-relative"),
    releaseCheckResult(companion.includes("ANTIGRAVITY_FOR_CODEX_REVIEW_GATE_TIMEOUT_MS"), "review-gate-timeout-env"),
    releaseCheckResult(companion.includes("deriveJobIdempotencyKey") && companion.includes("worktreeFingerprint"), "background-idempotency-fingerprint"),
    releaseCheckResult(githubActionsForbiddenPluginPathsExist, "skills-natural-language-routing-paths"),
    releaseCheckResult(
      naturalLanguageRoutingContractLoaded
        && routedSkillsHaveNaturalLanguageRouting
        && routedSkillsHaveExpectedMarkers
        && routedSkillUserExamplesHideInternalFlags
        && githubActionsForbiddenPluginPathsExist
        && githubActionsInitHidesInternalFlags,
      "skills-natural-language-routing"
    ),
    releaseCheckResult(antigravityDocs.includes("operational maturity for plugin-managed workflows"), "docs-maturity-boundary"),
    releaseCheckResult(antigravityDocs.includes("does not claim Claude SDK, Gemini native-agent, or ultrareview parity"), "docs-no-unsupported-parity"),
    releaseCheckResult(antigravityDocs.includes("Claude-through-Antigravity is an explicit Antigravity model-provider choice"), "docs-claude-through-antigravity-boundary"),
    releaseCheckResult(antigravityDocs.includes("real smoke remains opt-in") || antigravityDocs.includes("real-smoke` is opt-in"), "docs-real-smoke-opt-in"),
    releaseCheckResult(antigravityDocs.includes("CI runs require an authenticated `agy`"), "docs-ci-authenticated-agy"),
    releaseCheckResult(companion.includes('"real-smoke"') && companion.includes('"release-check"'), "valid-commands"),
    releaseCheckResult(matureCommands.every((commandName) => USER_VISIBLE_COMMANDS.includes(commandName)), "all-mature-commands"),
    releaseCheckResult(!USER_VISIBLE_COMMANDS.includes("__run-job"), "capabilities-hide-internal"),
    releaseCheckResult(runtime.includes("GPT/OpenAI") && runtime.includes("/\\b(gpt|openai)\\b/i"), "model-policy-rejects-openai"),
    releaseCheckResult(runtime.includes("--prompt") && runtime.includes("--print-timeout"), "agy-prompt-timeout-argv"),
    releaseCheckResult(runtime.includes("--log-file") && runtime.includes("antigravityLogDiagnostic") && runtime.includes("EEMPTYOUTPUT"), "agy-empty-output-log-diagnostics"),
    releaseCheckResult(!/args\.push\(\s*["']--print["']/.test(runtime), "no-print-argv"),
    releaseCheckResult(!runtime.includes("--dangerously-skip-permissions") || runtime.includes("forbidden"), "dangerous-permission-guard"),
    releaseCheckResult(hooks.includes("ANTIGRAVITY_PLUGIN_ROOT") && hooks.includes("antigravity-review-gate.mjs"), "hooks-discovery"),
    releaseCheckResult(exists("hooks/antigravity-review-gate.mjs"), "hook-wrapper"),
    releaseCheckResult(exists("hooks/session-lifecycle.mjs") && exists("hooks/unread-result.mjs"), "lifecycle-hooks"),
    releaseCheckResult(exists("templates/github-actions/antigravity-for-codex-review.yml"), "github-actions-template"),
    releaseCheckResult(renderedWorkflow.includes(RELEASE_REF), "github-actions-release-ref"),
    releaseCheckResult(renderWorkflow(ROOT_DIR).includes(`--ref ${RELEASE_REF}`), "github-actions-default-ref-derived"),
    releaseCheckResult(workflowValidation.checks.some((check) => check.name === "plugin-root-resolved" && check.ok), "github-actions-plugin-root-resolved"),
    releaseCheckResult(workflowValidation.checks.some((check) => check.name === "no-repo-relative-runtime-path" && check.ok), "github-actions-no-repo-relative-runtime-path"),
    releaseCheckResult(!printHotPath.includes("antigravityModelCatalog") && !printHotPath.includes("antigravityModelDiagnostics"), "model-catalog-not-in-hot-path"),
    releaseCheckResult(companion.includes("antigravityModelDiagnostics") && companion.includes("modelCatalog"), "model-catalog-diagnostics"),
    releaseCheckResult(jobs.includes("stateDirForCwd") && mailbox.includes("stateDirForCwd") && leases.includes("stateDirForCwd"), "repo-external-state"),
    releaseCheckResult(reports.includes("stdoutBytes") && !reports.includes("stdout:"), "reports-omit-raw-output"),
    ...expectedSkills().map((skill) => releaseCheckResult(exists(path.join("skills", skill, "SKILL.md")), `skill-${skill}`)),
    ...workflowValidation.checks.map((check) => releaseCheckResult(check.ok, `workflow-${check.name}`, check.detail))
  ];

  const failures = checks.filter((check) => !check.ok);
  for (const check of checks) {
    process.stdout.write(`${check.ok ? "PASS" : "FAIL"} ${check.name}${check.detail ? ` - ${check.detail}` : ""}\n`);
  }
  if (failures.length) {
    process.stderr.write(`release-check failed: ${failures.map((failure) => failure.name).join(", ")}\n`);
    process.exit(1);
  }
}

async function runRealSmoke(rawArgs) {
  if (process.env.ANTIGRAVITY_FOR_CODEX_REAL_SMOKE !== "1") {
    process.stderr.write("real-smoke is opt-in. Set ANTIGRAVITY_FOR_CODEX_REAL_SMOKE=1 to run Antigravity CLI smoke checks.\n");
    process.exit(2);
  }
  const args = parseArgsOrExit(rawArgs);
  if (args.help) {
    process.stdout.write("Usage: antigravity-companion.mjs real-smoke [--quick|--full] [--model-provider gemini|claude] [--model <label>] [--timeout-seconds <n>]\n");
    return;
  }

  const providers = args.modelProvider ? [args.modelProvider] : ["gemini", "claude"];
  const results = [];
  const modelArgs = args.model ? ["--model", args.model] : [];
  const runCommandSmoke = (provider, label, commandArgs, extraEnv = {}) => {
    const result = spawnSync(process.execPath, [COMPANION_PATH, ...commandArgs], {
      cwd: process.cwd(),
      env: {
        ...process.env,
        ...extraEnv,
        ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER: provider
      },
      encoding: "utf8",
      maxBuffer: 20 * 1024 * 1024,
      timeout: args.timeout || DEFAULT_TIMEOUT_MS,
      killSignal: "SIGKILL"
    });
    const output = `${result.stdout || ""}\n${result.stderr || ""}`;
    const ok = label === "review-gate"
      ? (result.status ?? 1) === 0 && !result.stderr
      : (result.status ?? 1) === 0 && output.includes("ANTIGRAVITY_FOR_CODEX_SMOKE_OK");
    return {
      provider,
      label,
      ok,
      result: {
        status: result.status ?? 1,
        stdout: result.stdout || "",
        stderr: result.stderr || "",
        error: result.error ? String(result.error.message || result.error) : ""
      }
    };
  };
  for (const provider of providers) {
    const diagnostics = antigravityModelDiagnostics(process.env, { ...args, modelProvider: provider });
    process.stdout.write(`INFO real-smoke ${provider} modelCatalog ${JSON.stringify(diagnostics.modelCatalog)}\n`);
    const smokeArgs = {
      ...args,
      modelProvider: provider,
      timeout: args.timeout || (args.quick ? 60 * 1000 : DEFAULT_TIMEOUT_MS)
    };
    if (args.full) {
      const commandChecks = [
        ["direct", ["review", "--model-provider", provider, ...modelArgs, "Return exactly ANTIGRAVITY_FOR_CODEX_SMOKE_OK."]],
        ["structured", ["review", "--model-provider", provider, ...modelArgs, "--structured", "Return a valid structured review JSON and include ANTIGRAVITY_FOR_CODEX_SMOKE_OK."], {}],
        ["multi-review", ["multi-review", "--model-provider", provider, ...modelArgs, "--roles", "correctness,security", "Include ANTIGRAVITY_FOR_CODEX_SMOKE_OK."], {}],
        ["review-gate", ["review-gate", "--model-provider", provider, ...modelArgs], { ANTIGRAVITY_FOR_CODEX_REVIEW_GATE: "on" }]
      ];
      for (const [label, commandArgs, extraEnv] of commandChecks) {
        const item = runCommandSmoke(provider, label, commandArgs, extraEnv);
        results.push({ ...item, diagnostics });
        process.stdout.write(`${item.ok ? "PASS" : "FAIL"} real-smoke ${provider} ${label}\n`);
        if (!item.ok) {
          process.stdout.write(`${item.result.stdout || item.result.stderr || item.result.error || "no output"}\n`);
        }
      }
      const startedAt = new Date().toISOString();
      const reportResult = results.find((item) => item.provider === provider && item.label === "direct")?.result || { status: 1, stdout: "", stderr: "", error: "missing direct smoke result" };
      try {
        writeOperationReport(process.cwd(), operationReport({
          command: "real-smoke",
          args: { ...smokeArgs, modelProvider: provider },
          result: reportResult,
          startedAt,
          endedAt: new Date().toISOString()
        }), process.env);
        process.stdout.write(`PASS real-smoke ${provider} report\n`);
      } catch (error) {
        results.push({ provider, label: "report", ok: false, result: { status: 1, stdout: "", stderr: "", error: error.message || String(error) }, diagnostics });
        process.stdout.write(`FAIL real-smoke ${provider} report\n${error.message || String(error)}\n`);
      }
    } else {
      const result = await antigravityPrintAsync("Return exactly ANTIGRAVITY_FOR_CODEX_SMOKE_OK.", smokeArgs, process.env);
      const ok = result.status === 0 && result.stdout.includes("ANTIGRAVITY_FOR_CODEX_SMOKE_OK");
      results.push({ provider, label: provider, ok, result, diagnostics });
      process.stdout.write(`${ok ? "PASS" : "FAIL"} real-smoke ${provider}\n`);
      if (!ok) {
        process.stdout.write(`${result.stdout || result.stderr || result.error || "no output"}\n`);
      }
    }
  }
  if (results.some((item) => !item.ok)) {
    process.exit(1);
  }
}

async function main() {
  const [command = "", ...rest] = process.argv.slice(2);
  if (!command || command === "--help" || command === "-h" || command === "help") {
    process.stdout.write(usage());
    return;
  }
  if (!VALID_COMMANDS.has(command)) {
    process.stderr.write(`Unknown command "${command}".\n`);
    process.exit(2);
  }
  if (command === "setup" || command === "capabilities") {
    return runSetupOrCapabilities(command, rest);
  }
  if (command === "report") {
    return runReport(rest);
  }
  if (command === "roles") {
    return runRoles(rest);
  }
  if (command === "doctor") {
    return runDoctor(rest);
  }
  if (command === "jobs") {
    return runJobs(rest);
  }
  if (command === "status") {
    return runStatus(rest);
  }
  if (command === "result") {
    return runResult(rest);
  }
  if (command === "cancel") {
    return runCancel(rest);
  }
  if (command === "mailbox") {
    return runMailbox(rest);
  }
  if (command === "leases") {
    return runLeases(rest);
  }
  if (command === "github-actions") {
    return runGithubActions(rest);
  }
  if (command === "reserve-job") {
    return runReserveJob(rest);
  }
  if (command === "run-reserved-job") {
    return runReservedJob(rest);
  }
  if (command === "__run-job") {
    return runStoredJob(rest);
  }
  if (command === "multi-review") {
    return runMultiReview(rest);
  }
  if (command === "review-gate") {
    return runReviewGate(rest);
  }
  if (command === "real-smoke") {
    return runRealSmoke(rest);
  }
  if (command === "release-check") {
    return runReleaseCheck(rest);
  }
  if (REVIEW_COMMANDS.has(command)) {
    return runReview(command, rest);
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message || String(error)}\n`);
  process.exit(1);
});
