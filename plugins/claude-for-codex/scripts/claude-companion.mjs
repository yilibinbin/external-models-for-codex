#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import process from "node:process";

const VALID_COMMANDS = new Set(["setup", "review", "adversarial-review", "plan", "status"]);
const VALID_SCOPES = new Set(["auto", "working-tree", "branch"]);
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
  }
});
const DEFAULT_MULTI_REVIEW_ROLES = Object.freeze([
  "correctness",
  "security",
  "tests",
  "release",
  "adversarial"
]);

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? process.cwd(),
    env: process.env,
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024
  });

  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message ?? result.error) : ""
  };
}

function git(args) {
  return run("git", args);
}

function hasBinary(name) {
  return run(name, ["--version"]).status === 0;
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
        .map((role) => role.trim())
        .filter(Boolean);
      parsed.roles = [...(parsed.roles ?? []), ...roles];
      index += 1;
    } else if (arg === "--role") {
      const role = readOptionValue(tokens, index, arg).trim();
      if (!role) {
        throw new Error("Missing value for --role.");
      }
      parsed.roles = [...(parsed.roles ?? []), role];
      index += 1;
    } else {
      parsed._.push(arg);
    }
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
  for (const role of args.roles) {
    if (!Object.hasOwn(REVIEW_ROLES, role)) {
      throw new Error(`Unknown review role "${role}". Valid roles: ${validRoles.join(", ")}.`);
    }
  }
  return args.roles.map((name) => ({
    name,
    directive: REVIEW_ROLES[name].directive
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

function claudePrint(prompt, options) {
  const args = [
    "--print",
    "--permission-mode",
    "dontAsk",
    "--tools",
    "Read,Grep,Glob",
    "--disallowedTools",
    "Edit,Write,MultiEdit,Bash",
    "--output-format",
    "text"
  ];

  if (options.model) {
    args.push("--model", options.model);
  }
  if (options.effort) {
    args.push("--effort", options.effort);
  }

  args.push(prompt);
  return run("claude", args);
}

function reviewPrompt(kind, args) {
  const focus = args._.join(" ").trim();
  const gitContext = collectGitContext(args);
  const adversarial = kind === "adversarial-review";
  const reviewRoles = args.reviewRoles?.length
    ? args.reviewRoles.map((role) => role.name).join(", ")
    : "";

  return [
    adversarial
      ? "<task>Run an adversarial read-only code and design review.</task>"
      : "<task>Run a read-only code review.</task>",
    gitContext,
    reviewRoles ? `<review_roles>${reviewRoles}</review_roles>` : "",
    "<rules>",
    "- Do not edit files.",
    "- Do not suggest that you are about to apply fixes.",
    "- Put findings first, ordered by severity.",
    "- Ground every finding in concrete evidence from changed files or explicit git context.",
    "- Include exact file paths and line numbers when available.",
    "- If there are no findings, say so and list residual risks briefly.",
    adversarial
      ? "- Challenge assumptions, tradeoffs, failure modes, hidden costs, and simpler alternatives."
      : "- Focus on concrete bugs, regressions, missing tests, and maintainability risks.",
    "</rules>",
    focus ? `<focus>${focus}</focus>` : "",
    "<output_contract>",
    "## Findings",
    "- [Severity] file:line - issue, evidence, impact, suggested direction",
    "## Open Questions",
    "## Residual Risk",
    "</output_contract>"
  ].filter(Boolean).join("\n");
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

function printSetup() {
  const report = {
    node: process.version,
    claudeAvailable: hasBinary("claude"),
    gitAvailable: hasBinary("git"),
    cwd: process.cwd()
  };

  console.log(JSON.stringify(report, null, 2));
  process.exit(report.claudeAvailable && report.gitAvailable ? 0 : 1);
}

function printStatus() {
  const result = run("claude", ["agents", "--json", "--cwd", process.cwd()]);
  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.error || "claude agents --json failed\n");
    process.exit(result.status);
  }
  process.stdout.write(result.stdout);
}

function runClaudeTask(kind, rawArgs) {
  let args;
  try {
    args = parseArgs(rawArgs);
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
  const prompt = kind === "plan" ? planPrompt(args) : reviewPrompt(kind, args);
  const result = claudePrint(prompt, args);

  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.error || "claude --print failed\n");
    process.exit(result.status);
  }
  process.stdout.write(result.stdout);
}

const [command, ...rawArgs] = process.argv.slice(2);

if (!VALID_COMMANDS.has(command)) {
  console.error(`Usage: claude-companion.mjs ${Array.from(VALID_COMMANDS).join("|")} [args]`);
  process.exit(2);
}

switch (command) {
  case "setup":
    printSetup();
    break;
  case "review":
    runClaudeTask("review", rawArgs);
    break;
  case "adversarial-review":
    runClaudeTask("adversarial-review", rawArgs);
    break;
  case "plan":
    runClaudeTask("plan", rawArgs);
    break;
  case "status":
    printStatus();
    break;
}
