#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import process from "node:process";

const VALID_COMMANDS = new Set(["setup", "review", "adversarial-review", "plan", "status"]);

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

function parseArgs(argv) {
  const tokens = normalizeArgv(argv);
  const parsed = { _: [] };
  for (let index = 0; index < tokens.length; index += 1) {
    const arg = tokens[index];
    if (arg === "--base") {
      parsed.base = tokens[++index];
    } else if (arg === "--scope") {
      parsed.scope = tokens[++index];
    } else if (arg === "--path" || arg === "--paths") {
      parsed.path = tokens[++index];
    } else if (arg === "--model") {
      parsed.model = tokens[++index];
    } else if (arg === "--effort") {
      parsed.effort = tokens[++index];
    } else {
      parsed._.push(arg);
    }
  }
  return parsed;
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

  for (const char of value) {
    if (escaping) {
      current += char;
      escaping = false;
      continue;
    }
    if (char === "\\") {
      escaping = true;
      continue;
    }
    if (quote) {
      if (char === quote) {
        quote = null;
      } else {
        current += char;
      }
      continue;
    }
    if (char === "\"" || char === "'") {
      quote = char;
      continue;
    }
    if (/\s/.test(char)) {
      if (current) {
        tokens.push(current);
        current = "";
      }
      continue;
    }
    current += char;
  }

  if (escaping) {
    current += "\\";
  }
  if (current) {
    tokens.push(current);
  }
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
  const base = options.base;
  const pathspec = options.path;
  const pathArgs = pathspec ? ["--", pathspec] : [];
  const headExists = hasHead();
  const status = git(["status", "--short", "--untracked-files=all", ...pathArgs]);
  const stagedStat = git(["diff", "--cached", "--stat", ...pathArgs]);
  const stagedDiff = git(["diff", "--cached", ...pathArgs]);
  const unstagedStat = git(["diff", "--stat", ...pathArgs]);
  const unstagedDiff = git(["diff", ...pathArgs]);
  const branchStat = base
    ? headExists
      ? git(["diff", "--stat", `${base}...HEAD`, ...pathArgs])
      : safeResult("(HEAD unavailable; branch diff skipped)")
    : null;
  const branchNameOnly = base
    ? headExists
      ? git(["diff", "--name-only", `${base}...HEAD`, ...pathArgs])
      : changedFilesFromStatus(status)
    : headExists
      ? git(["diff", "--name-only", "HEAD", ...pathArgs])
      : changedFilesFromStatus(status);

  return [
    "<git_context>",
    `cwd: ${process.cwd()}`,
    `scope: ${options.scope ?? "auto"}`,
    `base: ${base ?? ""}`,
    `path: ${pathspec ?? ""}`,
    "",
    formatCommandResult(`git status --short --untracked-files=all${pathspec ? ` -- ${pathspec}` : ""}`, status),
    "",
    formatCommandResult(`git diff --cached --stat${pathspec ? ` -- ${pathspec}` : ""}`, stagedStat),
    "",
    formatCommandResult(`git diff --cached${pathspec ? ` -- ${pathspec}` : ""}`, stagedDiff),
    "",
    formatCommandResult(`git diff --stat${pathspec ? ` -- ${pathspec}` : ""}`, unstagedStat),
    "",
    formatCommandResult(`git diff${pathspec ? ` -- ${pathspec}` : ""}`, unstagedDiff),
    "",
    branchStat
      ? formatCommandResult(
          headExists
            ? `git diff --stat ${base}...HEAD${pathspec ? ` -- ${pathspec}` : ""}`
            : "branch diff skipped",
          branchStat
        )
      : "branch diff:\n(empty)",
    "",
    base
      ? headExists
        ? formatCommandResult(`git diff --name-only ${base}...HEAD${pathspec ? ` -- ${pathspec}` : ""}`, branchNameOnly)
        : formatCommandResult("changed files from git status fallback", branchNameOnly)
      : headExists
        ? formatCommandResult(`git diff --name-only HEAD${pathspec ? ` -- ${pathspec}` : ""}`, branchNameOnly)
        : formatCommandResult("changed files from git status fallback", branchNameOnly),
    "</git_context>"
  ].join("\n");
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

  return [
    adversarial
      ? "<task>Run an adversarial read-only code and design review.</task>"
      : "<task>Run a read-only code review.</task>",
    gitContext,
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
  const args = parseArgs(rawArgs);
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
