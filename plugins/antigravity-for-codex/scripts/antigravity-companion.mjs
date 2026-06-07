#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import {
  antigravityPreflight,
  antigravityPrint,
  antigravityPrintAsync
} from "./lib/antigravity-runtime.mjs";
import {
  renderWorkflow,
  validateWorkflow
} from "./lib/github-actions.mjs";
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
  appendStructuredReviewInstructions,
  extractJsonObject,
  validateStructuredReview
} from "./lib/structured-output.mjs";

const VALID_COMMANDS = new Set([
  "setup",
  "capabilities",
  "review",
  "adversarial-review",
  "multi-review",
  "plan",
  "rescue",
  "report",
  "review-gate",
  "real-smoke",
  "release-check"
]);
const REVIEW_COMMANDS = new Set(["review", "adversarial-review", "plan", "rescue"]);
const VALID_MODEL_PROVIDERS = new Set(["gemini", "claude"]);
const ROOT_DIR = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const PLUGIN_VERSION = "0.1.0";
const RELEASE_REF = `antigravity-for-codex-v${PLUGIN_VERSION}`;
const DEFAULT_TIMEOUT_MS = 15 * 60 * 1000;
const REVIEW_GATE_INNER_TIMEOUT_MS = 14 * 60 * 1000;
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
    return "Usage: antigravity-companion.mjs multi-review [--model-provider gemini|claude] [--model <label>] [--roles correctness,security,tests,release,adversarial] [focus]\n";
  }
  if (command === "report") {
    return "Usage: antigravity-companion.mjs report --latest\n";
  }
  return "Usage: antigravity-companion.mjs <setup|capabilities|review|adversarial-review|multi-review|plan|rescue|report|review-gate|real-smoke|release-check> [args]\n";
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
    help: false,
    timeout: DEFAULT_TIMEOUT_MS,
    quick: false,
    structured: false,
    json: false,
    latest: false
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
    } else if (token === "--help" || token === "-h" || token === "help") {
      args.help = true;
    } else if (token === "--quick") {
      args.quick = true;
    } else if (token === "--structured") {
      args.structured = true;
    } else if (token === "--json") {
      args.json = true;
      args.structured = true;
    } else if (token === "--latest") {
      args.latest = true;
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

function exists(relativePath) {
  return fs.existsSync(path.join(ROOT_DIR, relativePath));
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

function gitContext() {
  const rootResult = runGitOk(["rev-parse", "--show-toplevel"]);
  const root = rootResult.ok ? rootResult.stdout : "";
  if (!root) {
    return "No git repository was detected. Review only the user's explicit focus text.";
  }
  const status = runGit(["status", "--short"]);
  const stagedStat = runGit(["diff", "--cached", "--stat"]);
  const workingStat = runGit(["diff", "--stat"]);
  const stagedDiff = runGit(["diff", "--cached", "--", "."]);
  const workingDiff = runGit(["diff", "--", "."]);
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

function focusBlock(args) {
  const focus = args.positional.join(" ").trim();
  return focus ? `<focus>${focus}</focus>` : "";
}

function promptFor(kind, args) {
  return [
    `<task>${kind}</task>`,
    `Model provider: ${args.modelProvider || process.env.ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER || "gemini"}.`,
    `<git_context>${gitContext()}</git_context>`,
    focusBlock(args),
    "Return concise findings first. Do not edit files."
  ].filter(Boolean).join("\n");
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

function reviewGateEnabled() {
  const value = String(process.env.ANTIGRAVITY_FOR_CODEX_REVIEW_GATE ?? "").trim().toLowerCase();
  return value !== "" && value !== "off" && value !== "false" && value !== "0";
}

function reviewGatePrompt(args) {
  const focus = args.positional.join(" ").trim();
  return [
    "<task>Run a stop-gate review of the current git changes.</task>",
    `Model provider: ${args.modelProvider || process.env.ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER || "gemini"}.`,
    `<git_context>${gitContext()}</git_context>`,
    focus ? `<focus>${focus}</focus>` : "",
    "<rules>",
    "- Do not edit files.",
    "- Review only the current git working-tree changes shown in the git context.",
    "- Use BLOCK only for concrete issues that should prevent Codex from stopping now.",
    "- Use ALLOW if there is no blocking issue.",
    "- Ground every BLOCK claim in concrete changed-file evidence when possible.",
    "</rules>",
    "<output_contract>",
    "Your first line must be exactly one of:",
    "ALLOW: <short reason>",
    "BLOCK: <short reason>",
    "Do not put anything before that first line.",
    "After the first line, include concise evidence for BLOCK results.",
    "</output_contract>"
  ].filter(Boolean).join("\n");
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

function runSetupOrCapabilities(command, rawArgs) {
  const args = parseArgsOrExit(rawArgs);
  if (args.help) {
    process.stdout.write(usage());
    return;
  }
  const preflight = antigravityPreflight(process.env, args);
  process.stdout.write(`${JSON.stringify({ ok: preflight.ok, provider: preflight }, null, 2)}\n`);
  process.exit(command === "setup" ? 0 : (preflight.ok ? 0 : 1));
}

function runReview(kind, rawArgs) {
  const args = parseArgsOrExit(rawArgs);
  if (args.help) {
    process.stdout.write(usage(kind));
    return;
  }
  const preflight = antigravityPreflight(process.env, args);
  if (!preflight.ok) {
    process.stderr.write(`${preflight.error || `Antigravity CLI is unavailable; missing ${preflight.missing.join(", ")}.`}\n`);
    process.exit(2);
  }
  const startedAt = new Date().toISOString();
  let prompt = templatePromptFor(kind, args, preflight);
  if (args.structured || args.json) {
    prompt = appendStructuredReviewInstructions(prompt);
  }
  const result = antigravityPrint(prompt, { ...args, preflight }, process.env);
  const endedAt = new Date().toISOString();
  if (result.status !== 0) {
    process.stderr.write(`${result.stderr || result.error || "Antigravity review failed."}\n`);
    process.exit(result.status);
  }
  let parsed;
  if (args.structured || args.json) {
    try {
      parsed = validateStructuredReview(extractJsonObject(result.stdout));
    } catch (error) {
      process.stderr.write(`Structured review output invalid: ${error.message || String(error)}\n`);
      process.exit(1);
    }
  }
  try {
    writeOperationReport(process.cwd(), operationReport({ command: kind, args, result, startedAt, endedAt, parsed }), process.env);
  } catch {
    // Report writes are best-effort and must not mask successful CLI output.
  }
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

function runReviewGate(rawArgs) {
  if (!reviewGateEnabled()) {
    return;
  }
  const args = parseArgsOrExit(rawArgs);
  args.timeout = Math.min(args.timeout || DEFAULT_TIMEOUT_MS, REVIEW_GATE_INNER_TIMEOUT_MS);
  if (args.help) {
    process.stdout.write("Usage: antigravity-companion.mjs review-gate [--model-provider gemini|claude] [--model <label>] [focus]\n");
    return;
  }

  let result;
  try {
    result = antigravityPrint(reviewGatePrompt(args), args, process.env);
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
  const preflight = antigravityPreflight(process.env, args);
  if (!preflight.ok) {
    process.stderr.write(`${preflight.error || `Antigravity CLI is unavailable; missing ${preflight.missing.join(", ")}.`}\n`);
    process.exit(2);
  }
  const roles = args.roles?.length ? args.roles : ["correctness", "security", "tests", "release", "adversarial"];
  const results = await Promise.all(roles.map(async (role) => {
    const roleArgs = { ...args, positional: [`Role: ${role}`, ...args.positional] };
    const result = await antigravityPrintAsync(promptFor("multi-review", roleArgs), { ...roleArgs, preflight }, process.env);
    return { role, result };
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
    "antigravity-github-actions-review"
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
  const hooks = exists("hooks/hooks.json") ? readText("hooks/hooks.json") : "";
  const renderedWorkflow = renderWorkflow(ROOT_DIR, { releaseRef: RELEASE_REF });
  const workflowValidation = validateWorkflow(renderedWorkflow);
  const checks = [
    releaseCheckResult(manifest.name === "antigravity-for-codex", "manifest-name"),
    releaseCheckResult(manifest.version === PLUGIN_VERSION, "manifest-version"),
    releaseCheckResult(manifest.skills === "./skills/", "manifest-skills"),
    releaseCheckResult(JSON.stringify(manifest).includes("Explicit Gemini or Claude model selection"), "manifest-model-policy"),
    releaseCheckResult(companion.includes('"real-smoke"') && companion.includes('"release-check"'), "valid-commands"),
    releaseCheckResult(runtime.includes("--prompt") && runtime.includes("--print-timeout"), "agy-prompt-timeout-argv"),
    releaseCheckResult(!/args\.push\(\s*["']--print["']/.test(runtime), "no-print-argv"),
    releaseCheckResult(!runtime.includes("--dangerously-skip-permissions") || runtime.includes("forbidden"), "dangerous-permission-guard"),
    releaseCheckResult(hooks.includes("ANTIGRAVITY_PLUGIN_ROOT") && hooks.includes("antigravity-review-gate.mjs"), "hooks-discovery"),
    releaseCheckResult(exists("hooks/antigravity-review-gate.mjs"), "hook-wrapper"),
    releaseCheckResult(exists("templates/github-actions/antigravity-for-codex-review.yml"), "github-actions-template"),
    releaseCheckResult(renderedWorkflow.includes(RELEASE_REF), "github-actions-release-ref"),
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
    process.stdout.write("Usage: antigravity-companion.mjs real-smoke [--quick] [--model-provider gemini|claude] [--model <label>] [--timeout-seconds <n>]\n");
    return;
  }

  const providers = args.modelProvider ? [args.modelProvider] : ["gemini", "claude"];
  const results = [];
  for (const provider of providers) {
    const smokeArgs = {
      ...args,
      modelProvider: provider,
      timeout: args.timeout || (args.quick ? 60 * 1000 : DEFAULT_TIMEOUT_MS)
    };
    const result = await antigravityPrintAsync("Return exactly ANTIGRAVITY_FOR_CODEX_SMOKE_OK.", smokeArgs, process.env);
    const ok = result.status === 0 && result.stdout.includes("ANTIGRAVITY_FOR_CODEX_SMOKE_OK");
    results.push({ provider, ok, result });
    process.stdout.write(`${ok ? "PASS" : "FAIL"} real-smoke ${provider}\n`);
    if (!ok) {
      process.stdout.write(`${result.stdout || result.stderr || result.error || "no output"}\n`);
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
