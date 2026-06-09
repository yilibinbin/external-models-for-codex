import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const MAX_WORKTREE_FINGERPRINT_FILE_BYTES = 1024 * 1024;
const DEFAULT_MAX_UNTRACKED_FINGERPRINT_BYTES = 4 * 1024 * 1024;
const DEFAULT_MAX_UNTRACKED_FINGERPRINT_FILES = 512;
const HOOK_GIT_SIGNAL_TIMEOUT_MS = 500;
const HOOK_MAX_UNTRACKED_FINGERPRINT_BYTES = 512 * 1024;
const HOOK_MAX_UNTRACKED_FINGERPRINT_FILES = 128;
const FINGERPRINT_SEPARATOR = "\n--- claude-for-codex ---\n";

function parsePositiveInteger(value, fallback, { min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < min) {
    return fallback;
  }
  return Math.min(Math.trunc(parsed), max);
}

function gitSignalTimeoutMs(env = process.env) {
  return parsePositiveInteger(env.CLAUDE_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS, 10_000, {
    min: 100,
    max: 60_000
  });
}

export function hookFingerprintOptions(env = process.env) {
  return {
    env: {
      ...env,
      CLAUDE_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS: String(parsePositiveInteger(
        env.CLAUDE_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS,
        HOOK_GIT_SIGNAL_TIMEOUT_MS,
        { min: 100, max: HOOK_GIT_SIGNAL_TIMEOUT_MS }
      )),
      CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_BYTES: String(parsePositiveInteger(
        env.CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_BYTES,
        HOOK_MAX_UNTRACKED_FINGERPRINT_BYTES,
        { min: 1, max: HOOK_MAX_UNTRACKED_FINGERPRINT_BYTES }
      )),
      CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_FILES: String(parsePositiveInteger(
        env.CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_FILES,
        HOOK_MAX_UNTRACKED_FINGERPRINT_FILES,
        { min: 1, max: HOOK_MAX_UNTRACKED_FINGERPRINT_FILES }
      ))
    }
  };
}

function runGit(cwd, args, options = {}) {
  const result = spawnSync("git", args, {
    cwd,
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
    timeout: options.timeout ?? gitSignalTimeoutMs(options.env ?? process.env)
  });
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message ?? result.error) : "",
    errorCode: result.error?.code ? String(result.error.code) : ""
  };
}

function gitCommandTimedOut(result) {
  return String(result?.errorCode ?? "") === "ETIMEDOUT";
}

function isUnbornHead(args, result) {
  if (args.length !== 2 || args[0] !== "rev-parse" || args[1] !== "HEAD") {
    return false;
  }
  const stderr = String(result?.stderr ?? "");
  return result?.status !== 0
    && stderr.includes("ambiguous argument 'HEAD'")
    && stderr.includes("unknown revision or path not in the working tree");
}

function isNotGitRepository(result) {
  const stderr = String(result?.stderr ?? "");
  return result?.status !== 0 && stderr.includes("not a git repository");
}

function workingTreeFingerprintPart(cwd, args, options = {}) {
  const result = runGit(cwd, args, options);
  if (gitCommandTimedOut(result)) {
    return { text: `ETIMEDOUT ${args.join(" ")}`, timedOut: true };
  }
  if (isNotGitRepository(result)) {
    return {
      text: [
        `NON_GIT_REPOSITORY ${args.join(" ")}`,
        `status=${result.status}`,
        result.stderr
      ].join("\n"),
      stdout: "",
      timedOut: false,
      nonGit: true
    };
  }
  if (isUnbornHead(args, result)) {
    return {
      text: [
        "UNBORN_HEAD rev-parse HEAD",
        `status=${result.status}`,
        result.stderr
      ].join("\n"),
      stdout: "",
      timedOut: false
    };
  }
  if (result.errorCode || result.status !== 0) {
    return {
      text: [
        `INCONCLUSIVE ${args.join(" ")}`,
        `status=${result.status}`,
        `errorCode=${result.errorCode}`,
        result.error,
        result.stdout,
        result.stderr
      ].join("\n"),
      stdout: result.stdout,
      timedOut: true
    };
  }
  return {
    text: [
      `status=${result.status}`,
      result.stdout,
      result.stderr
    ].join("\n"),
    stdout: result.stdout,
    timedOut: false
  };
}

function safeWorkspacePath(cwd, relativePath) {
  const root = path.resolve(cwd);
  const fullPath = path.resolve(root, relativePath);
  return fullPath === root || fullPath.startsWith(`${root}${path.sep}`) ? fullPath : null;
}

function untrackedFingerprintBudget(env = process.env) {
  return {
    remainingBytes: parsePositiveInteger(
      env.CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_BYTES,
      DEFAULT_MAX_UNTRACKED_FINGERPRINT_BYTES,
      { min: 1, max: 64 * 1024 * 1024 }
    ),
    maxFiles: parsePositiveInteger(
      env.CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_FILES,
      DEFAULT_MAX_UNTRACKED_FINGERPRINT_FILES,
      { min: 1, max: 10_000 }
    )
  };
}

function budgetExceededPart(reason) {
  return {
    text: [
      "UNTRACKED_FINGERPRINT_BUDGET_EXCEEDED",
      reason
    ].join("\n"),
    stdout: "",
    timedOut: true,
    budgetExceeded: true
  };
}

function fileFingerprint(filePath, budget) {
  try {
    const stat = fs.lstatSync(filePath);
    if (stat.isSymbolicLink()) {
      return { type: "symlink", target: fs.readlinkSync(filePath) };
    }
    if (!stat.isFile()) {
      return { type: "other", size: stat.size, mtimeMs: Math.trunc(stat.mtimeMs) };
    }
    if (stat.size > MAX_WORKTREE_FINGERPRINT_FILE_BYTES) {
      return { type: "file-large", size: stat.size, mtimeMs: Math.trunc(stat.mtimeMs) };
    }
    if (budget && stat.size > budget.remainingBytes) {
      return {
        type: "budget-exceeded",
        size: stat.size,
        remainingBytes: budget.remainingBytes
      };
    }
    if (budget) {
      budget.remainingBytes -= stat.size;
    }
    return {
      type: "file",
      size: stat.size,
      sha256: createHash("sha256").update(fs.readFileSync(filePath)).digest("hex")
    };
  } catch (error) {
    return { type: "error", errorCode: error?.code || "UNKNOWN" };
  }
}

function untrackedFilesFingerprintPart(cwd, options = {}) {
  const result = runGit(cwd, ["ls-files", "--others", "--exclude-standard", "-z"], options);
  if (gitCommandTimedOut(result)) {
    return { text: "ETIMEDOUT ls-files --others --exclude-standard -z", stdout: "", timedOut: true };
  }
  if (isNotGitRepository(result)) {
    return {
      text: [
        "NON_GIT_REPOSITORY ls-files --others --exclude-standard -z",
        `status=${result.status}`,
        result.stderr
      ].join("\n"),
      stdout: "",
      timedOut: false
    };
  }
  if (result.errorCode || result.status !== 0) {
    return {
      text: [
        "INCONCLUSIVE ls-files --others --exclude-standard -z",
        `status=${result.status}`,
        `errorCode=${result.errorCode}`,
        result.error,
        result.stderr
      ].join("\n"),
      stdout: "",
      timedOut: true
    };
  }
  const files = result.stdout.split("\0").filter(Boolean).sort();
  const budget = untrackedFingerprintBudget(options.env ?? process.env);
  if (files.length > budget.maxFiles) {
    return budgetExceededPart(`files=${files.length} maxFiles=${budget.maxFiles}`);
  }
  const fingerprints = [];
  for (const file of files) {
    const safePath = safeWorkspacePath(cwd, file);
    const fingerprint = safePath ? fileFingerprint(safePath, budget) : { type: "unsafe-path" };
    if (fingerprint.type === "budget-exceeded") {
      return budgetExceededPart(`file=${file} size=${fingerprint.size} remainingBytes=${fingerprint.remainingBytes}`);
    }
    fingerprints.push({
      path: file,
      fingerprint
    });
  }
  return {
    text: JSON.stringify(fingerprints),
    stdout: result.stdout,
    timedOut: false
  };
}

function optionValue(args = [], name) {
  for (let index = 0; index < args.length; index += 1) {
    const arg = String(args[index] ?? "");
    if (arg === name) {
      return args[index + 1] ? String(args[index + 1]) : "";
    }
    if (arg.startsWith(`${name}=`)) {
      return arg.slice(name.length + 1);
    }
  }
  return "";
}

function hashParts(parts) {
  return createHash("sha256").update(parts.map((part) => part.text).join(FINGERPRINT_SEPARATOR)).digest("hex");
}

function hashStdoutParts(parts) {
  return createHash("sha256").update(parts.map((part) => part.stdout ?? "").join(FINGERPRINT_SEPARATOR)).digest("hex");
}

export function workingTreeFingerprintDetails(cwd = process.cwd(), args = [], options = {}) {
  const baseRef = optionValue(args, "--base");
  const headPart = workingTreeFingerprintPart(cwd, ["rev-parse", "HEAD"], options);
  if (headPart.nonGit) {
    return {
      hash: hashParts([headPart]),
      legacyHashes: [],
      timedOut: false,
      nonGit: true
    };
  }
  const basePart = baseRef ? workingTreeFingerprintPart(cwd, ["rev-parse", baseRef], options) : null;
  const statusPart = workingTreeFingerprintPart(cwd, ["status", "--short", "--untracked-files=all"], options);
  const stagedDiffPart = workingTreeFingerprintPart(cwd, ["diff", "--cached"], options);
  const unstagedDiffPart = workingTreeFingerprintPart(cwd, ["diff"], options);
  const untrackedPart = untrackedFilesFingerprintPart(cwd, options);
  const parts = [
    headPart,
    ...(basePart ? [basePart] : []),
    statusPart,
    stagedDiffPart,
    unstagedDiffPart,
    untrackedPart
  ];
  return {
    hash: hashParts(parts),
    legacyHashes: [
      hashParts([statusPart, stagedDiffPart, unstagedDiffPart, untrackedPart]),
      hashParts([statusPart, stagedDiffPart, unstagedDiffPart]),
      hashStdoutParts([statusPart, stagedDiffPart, unstagedDiffPart])
    ],
    timedOut: parts.some((part) => part.timedOut)
  };
}

export function workingTreeFingerprint(cwd = process.cwd(), args = [], options = {}) {
  return workingTreeFingerprintDetails(cwd, args, options).hash;
}

export function workingTreeFingerprintMatches(savedFingerprint, currentDetails) {
  if (!savedFingerprint || !currentDetails) {
    return false;
  }
  return savedFingerprint === currentDetails.hash || (currentDetails.legacyHashes ?? []).includes(savedFingerprint);
}
