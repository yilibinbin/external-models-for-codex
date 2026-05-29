#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";

const VALID_COMMANDS = new Set(["setup", "review", "adversarial-review", "multi-review", "plan", "status", "review-gate"]);
const VALID_SCOPES = new Set(["auto", "working-tree", "branch"]);
const VALID_REVIEW_GATE_MODES = new Set(["multi-role"]);
const STATE_VERSION = 1;
const PLUGIN_DATA_ENV = "CLAUDE_PLUGIN_DATA";
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

function canonicalWorkspaceRoot(cwd = process.cwd()) {
  const rootResult = run("git", ["rev-parse", "--show-toplevel"], { cwd });
  const candidate = rootResult.status === 0 ? rootResult.stdout.trim() : cwd;
  const resolved = path.resolve(candidate || cwd);
  try {
    return fs.realpathSync.native(resolved);
  } catch {
    return resolved;
  }
}

function stateDirForCwd(cwd = process.cwd()) {
  const workspaceRoot = canonicalWorkspaceRoot(cwd);
  const slug = (path.basename(workspaceRoot) || "workspace")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "") || "workspace";
  const hash = createHash("sha256").update(workspaceRoot).digest("hex").slice(0, 16);
  const baseDir = process.env[PLUGIN_DATA_ENV]
    ? path.join(process.env[PLUGIN_DATA_ENV], "state")
    : path.join(os.homedir(), ".codex", "claude-for-codex", "state");
  return path.join(baseDir, `${slug}-${hash}`);
}

function stateFileForCwd(cwd = process.cwd()) {
  return path.join(stateDirForCwd(cwd), "state.json");
}

function defaultState() {
  return {
    version: STATE_VERSION,
    config: {
      reviewGateEnabled: false,
      reviewGateMode: "multi-role"
    }
  };
}

function loadState(cwd = process.cwd()) {
  const stateFile = stateFileForCwd(cwd);
  if (!fs.existsSync(stateFile)) {
    return defaultState();
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(stateFile, "utf8"));
    return {
      ...defaultState(),
      ...parsed,
      config: {
        ...defaultState().config,
        ...(parsed.config ?? {})
      }
    };
  } catch {
    return defaultState();
  }
}

function saveState(cwd, state) {
  const stateDir = stateDirForCwd(cwd);
  fs.mkdirSync(stateDir, { recursive: true });
  const payload = {
    version: STATE_VERSION,
    config: {
      ...defaultState().config,
      ...(state.config ?? {})
    }
  };
  const stateFile = stateFileForCwd(cwd);
  const tmpFile = `${stateFile}.${process.pid}.tmp`;
  fs.writeFileSync(tmpFile, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  fs.renameSync(tmpFile, stateFile);
  return payload;
}

function setConfig(cwd, key, value) {
  const state = loadState(cwd);
  state.config = {
    ...state.config,
    [key]: value
  };
  return saveState(cwd, state);
}

function getConfig(cwd = process.cwd()) {
  return loadState(cwd).config;
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
  return runClaude(args, { timeout: options.timeout });
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

function reviewPrompt(kind, args) {
  const focus = args._.join(" ").trim();
  const gitContext = collectGitContext(args);
  const adversarial = kind === "adversarial-review";
  const reviewRoles = args.reviewRoles?.length
    ? args.reviewRoles.map((role) => role.name).join(", ")
    : "";

  if (adversarial) {
    const adversarialLenses = args.resolvedAdversarialLenses ?? resolveAdversarialLenses(args);
    return [
      "<task>Run an adversarial read-only code and design review.</task>",
      gitContext,
      adversarialLensSection(adversarialLenses),
      "<scale_guidance>",
      "If the diff is small, emphasize Skeptic findings.",
      "If the diff is medium, weigh Skeptic and Architect findings.",
      "If the diff is large or spans many files, use Skeptic, Architect, and Minimalist lenses.",
      "When explicit lenses are provided, use only those lenses.",
      "Small means fewer than 50 changed lines across one or two files; medium means roughly 50 to 200 changed lines or three to five files; large means more than 200 changed lines or more than five files.",
      "</scale_guidance>",
      "<rules>",
      "- Do not edit files.",
      "- Do not suggest that you are about to apply fixes.",
      "- First infer the author's intent from the request, focus text, git context, and changed files.",
      "- Challenge whether the work achieves that intent well.",
      "- Find real problems, not validation or style preferences.",
      "- Ground every finding in concrete evidence from changed files or explicit git context.",
      "- Include exact file paths and line numbers when available.",
      "- Deduplicate overlapping lens findings.",
      "- Apply lead judgment: accept strong findings and reject false positives or overreach.",
      "- Use PASS only when there are no high-severity findings.",
      "- Use CONTESTED when high-severity findings exist but lens evidence or agreement is mixed.",
      "- Use REJECT when high-severity findings have strong evidence or consensus across lenses.",
      "</rules>",
      focus ? `<focus>${focus}</focus>` : "",
      adversarialVerdictContract()
    ].filter(Boolean).join("\n");
  }

  return [
    "<task>Run a read-only code review.</task>",
    gitContext,
    reviewRoles ? `<review_roles>${reviewRoles}</review_roles>` : "",
    "<rules>",
    "- Do not edit files.",
    "- Do not suggest that you are about to apply fixes.",
    "- Put findings first, ordered by severity.",
    "- Ground every finding in concrete evidence from changed files or explicit git context.",
    "- Include exact file paths and line numbers when available.",
    "- If there are no findings, say so and list residual risks briefly.",
    "- Focus on concrete bugs, regressions, missing tests, and maintainability risks.",
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

function multiReviewRolePrompt(role, args, gitContext) {
  const focus = args._.join(" ").trim();

  return [
    "<task>Run a role-specialized read-only code review.</task>",
    `<role_name>${role.name}</role_name>`,
    `<role_directive>${role.directive}</role_directive>`,
    gitContext,
    "<rules>",
    "- Do not edit files.",
    "- Do not suggest that you are about to apply fixes.",
    "- Put findings first, ordered by severity.",
    "- Ground every finding in concrete evidence from changed files or explicit git context.",
    "- Include exact file paths and line numbers when available.",
    "- If there are no findings, say so and list residual risks briefly.",
    "- Focus only on this role's directive; do not broaden into unrelated review areas.",
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

function reviewGateRolePrompt(role, args, gitContext) {
  return [
    "<task>Run a stop-gate review of the current git changes.</task>",
    `<role_name>${role.name}</role_name>`,
    `<role_directive>${role.directive}</role_directive>`,
    gitContext,
    "<rules>",
    "- Do not edit files.",
    "- Do not suggest that you are about to apply fixes.",
    "- Review only the current git working-tree changes shown in the git context.",
    "- Use BLOCK only for concrete issues that should prevent stopping now.",
    "- Use ALLOW if you do not see a blocking issue for this role.",
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

function buildSetupReport(actionsTaken = []) {
  const cwd = process.cwd();
  const config = getConfig(cwd);
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
      bypassEnv: REVIEW_GATE_ENV
    },
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
  process.exit(report.claudeAvailable && report.gitAvailable ? 0 : 1);
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
  const config = getConfig(cwd);
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
  if (config.lastAllowedReviewGateDiffHash === diffHash) {
    return;
  }

  const roles = DEFAULT_MULTI_REVIEW_ROLES.map((name) => ({
    name,
    directive: REVIEW_ROLES[name].directive
  }));
  args.scope = "working-tree";
  args.timeout = REVIEW_GATE_ROLE_TIMEOUT_MS;

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
  setConfig(cwd, "lastAllowedReviewGateDiffHash", diffHash);
}

function runClaudeTask(kind, rawArgs) {
  let args;
  try {
    args = parseArgs(rawArgs);
    if (kind === "adversarial-review" && args.roles !== undefined) {
      throw new Error("--roles is only valid for multi-review; use --adversarial-lenses for adversarial-review.");
    }
    if (kind === "adversarial-review") {
      args.resolvedAdversarialLenses = resolveAdversarialLenses(args);
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
  const prompt = kind === "plan" ? planPrompt(args) : reviewPrompt(kind, args);
  const result = claudePrint(prompt, args);

  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.error || "claude --print failed\n");
    process.exit(result.status);
  }
  process.stdout.write(result.stdout);
}

function runClaudeMultiReview(rawArgs) {
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

  const gitContext = collectGitContext(args);
  const results = [];
  for (const role of args.reviewRoles) {
    const prompt = multiReviewRolePrompt(role, args, gitContext);
    const result = claudePrint(prompt, args);
    results.push({ role, result });
  }

  const succeeded = results.filter(({ result }) => result.status === 0).map(({ role }) => role.name);
  const failed = results.filter(({ result }) => result.status !== 0).map(({ role }) => role.name);
  const sections = [
    "# Claude Multi-Agent Review",
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
    `roles succeeded: ${succeeded.length ? succeeded.join(", ") : "(none)"}`,
    `roles failed: ${failed.length ? failed.join(", ") : "(none)"}`,
    "exit policy: exits non-zero if any role fails; completed role output remains visible."
  ];

  process.stdout.write(`${sections.join("\n\n")}\n`);
  process.exit(failed.length ? 1 : 0);
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
  case "review":
    runClaudeTask("review", rawArgs);
    break;
  case "adversarial-review":
    runClaudeTask("adversarial-review", rawArgs);
    break;
  case "multi-review":
    runClaudeMultiReview(rawArgs);
    break;
  case "plan":
    runClaudeTask("plan", rawArgs);
    break;
  case "status":
    printStatus();
    break;
  case "review-gate":
    runReviewGate(rawArgs);
    break;
}
