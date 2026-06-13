import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { gitSignalTimeoutMs, parsePositiveInteger } from "./job-lifecycle.mjs";
import { spawnSyncWithRetry } from "./spawn-retry.mjs";

const MAX_WORKTREE_FINGERPRINT_FILE_BYTES = 1024 * 1024;
const DEFAULT_MAX_UNTRACKED_FINGERPRINT_BYTES = 4 * 1024 * 1024;
const DEFAULT_MAX_UNTRACKED_FINGERPRINT_FILES = 512;
const FINGERPRINT_SEPARATOR = "\n--- antigravity-for-codex ---\n";
const GIT_REPOSITORY_ENV_KEYS = new Set([
  "GIT_ALTERNATE_OBJECT_DIRECTORIES",
  "GIT_CEILING_DIRECTORIES",
  "GIT_COMMON_DIR",
  "GIT_CONFIG",
  "GIT_CONFIG_COUNT",
  "GIT_CONFIG_GLOBAL",
  "GIT_CONFIG_NOSYSTEM",
  "GIT_CONFIG_PARAMETERS",
  "GIT_CONFIG_SYSTEM",
  "GIT_DIR",
  "GIT_DISCOVERY_ACROSS_FILESYSTEM",
  "GIT_INDEX_FILE",
  "GIT_NAMESPACE",
  "GIT_OBJECT_DIRECTORY",
  "GIT_WORK_TREE"
]);

function spawnSync(command, args, options) {
  return spawnSyncWithRetry(command, args, options);
}

function gitTimedOut(result) {
  return result?.errorCode === "ETIMEDOUT" || result?.errorCode === "ETERM";
}

function sanitizedGitEnv(sourceEnv) {
  const env = { ...(sourceEnv ?? process.env) };
  for (const key of Object.keys(env)) {
    if (GIT_REPOSITORY_ENV_KEYS.has(key) || /^GIT_CONFIG_(KEY|VALUE)_\d+$/.test(key)) {
      delete env[key];
    }
  }
  env.NO_COLOR = "1";
  env.LANG = "C";
  env.LC_ALL = "C";
  env.LC_MESSAGES = "C";
  return env;
}

function runGit(cwd, args, options = {}) {
  const env = sanitizedGitEnv(options.env ?? process.env);
  const result = spawnSync("git", [
    "-c", "color.ui=false",
    "-c", "color.status=false",
    "-c", "color.diff=false",
    "-c", "core.fsmonitor=false",
    ...args
  ], {
    cwd,
    env,
    encoding: "utf8",
    killSignal: "SIGKILL",
    maxBuffer: 20 * 1024 * 1024,
    timeout: options.timeout ?? gitSignalTimeoutMs(env)
  });
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    error: result.error ? String(result.error.message ?? result.error) : "",
    errorCode: result.error?.code ? String(result.error.code) : ""
  };
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

function fingerprintPart(cwd, args, options = {}) {
  const result = runGit(cwd, args, options);
  if (gitTimedOut(result)) {
    return { text: `ETIMEDOUT git ${args.join(" ")}`, timedOut: true, untrusted: true, failureKind: "timeout" };
  }
  if (isNotGitRepository(result)) {
    return {
      text: [
        `NON_GIT_REPOSITORY git ${args.join(" ")}`,
        `status=${result.status}`,
        result.stderr
      ].join("\n"),
      stdout: "",
      timedOut: false,
      nonGit: true,
      untrusted: true,
      failureKind: "non-git"
    };
  }
  if (isUnbornHead(args, result)) {
    return {
      text: [
        "UNBORN_HEAD git rev-parse HEAD",
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
        `INCONCLUSIVE git ${args.join(" ")}`,
        `status=${result.status}`,
        `errorCode=${result.errorCode}`,
        result.error,
        result.stdout,
        result.stderr
      ].join("\n"),
      stdout: result.stdout,
      timedOut: false,
      untrusted: true,
      failureKind: "inconclusive"
    };
  }
  return {
    text: [
      `git ${args.join(" ")}`,
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

function repoTopLevelPart(cwd, options = {}) {
  const result = runGit(cwd, ["rev-parse", "--show-toplevel"], options);
  if (gitTimedOut(result)) {
    return {
      text: "ETIMEDOUT git rev-parse --show-toplevel",
      stdout: "",
      timedOut: true,
      untrusted: true,
      failureKind: "timeout"
    };
  }
  if (isNotGitRepository(result)) {
    return {
      text: [
        "NON_GIT_REPOSITORY git rev-parse --show-toplevel",
        `status=${result.status}`,
        result.stderr
      ].join("\n"),
      stdout: "",
      timedOut: false,
      nonGit: true,
      untrusted: true,
      failureKind: "non-git"
    };
  }
  const topLevel = result.stdout.trim();
  if (result.errorCode || result.status !== 0 || !topLevel) {
    return {
      text: [
        "INCONCLUSIVE git rev-parse --show-toplevel",
        `status=${result.status}`,
        `errorCode=${result.errorCode}`,
        result.error,
        result.stdout,
        result.stderr
      ].join("\n"),
      stdout: result.stdout,
      timedOut: false,
      untrusted: true,
      failureKind: "inconclusive"
    };
  }
  return {
    text: [
      "git rev-parse --show-toplevel",
      `status=${result.status}`,
      topLevel,
      result.stderr
    ].join("\n"),
    stdout: result.stdout,
    timedOut: false,
    topLevel: path.resolve(topLevel)
  };
}

function untrackedFingerprintBudget(env = process.env) {
  return {
    remainingBytes: parsePositiveInteger(
      env.ANTIGRAVITY_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_BYTES,
      DEFAULT_MAX_UNTRACKED_FINGERPRINT_BYTES,
      { min: 1, max: 64 * 1024 * 1024 }
    ),
    maxFiles: parsePositiveInteger(
      env.ANTIGRAVITY_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_FILES,
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
    timedOut: false,
    untrusted: true,
    budgetExceeded: true
  };
}

function gitRelevantMode(stat) {
  return (stat.mode & 0o111) ? "100755" : "100644";
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
      mode: gitRelevantMode(stat),
      sha256: createHash("sha256").update(fs.readFileSync(filePath)).digest("hex")
    };
  } catch (error) {
    return { type: "error", errorCode: error?.code || "UNKNOWN" };
  }
}

export function isTrustedUntrackedFingerprint(fingerprint) {
  return Boolean(fingerprint)
    && fingerprint.type === "file"
    && Number.isFinite(fingerprint.size)
    && (fingerprint.mode === "100644" || fingerprint.mode === "100755")
    && typeof fingerprint.sha256 === "string"
    && fingerprint.sha256.length > 0;
}

function untrackedFilesFingerprintPart(topLevel, options = {}) {
  const result = runGit(topLevel, ["ls-files", "--others", "--exclude-standard", "--full-name", "-z"], options);
  if (gitTimedOut(result)) {
    return {
      text: "ETIMEDOUT git ls-files --others --exclude-standard --full-name -z",
      stdout: "",
      timedOut: true,
      untrusted: true,
      failureKind: "timeout"
    };
  }
  if (isNotGitRepository(result)) {
    return {
      text: [
        "NON_GIT_REPOSITORY git ls-files --others --exclude-standard --full-name -z",
        `status=${result.status}`,
        result.stderr
      ].join("\n"),
      stdout: "",
      timedOut: false,
      nonGit: true,
      untrusted: true,
      failureKind: "non-git"
    };
  }
  if (result.errorCode || result.status !== 0) {
    return {
      text: [
        "INCONCLUSIVE git ls-files --others --exclude-standard --full-name -z",
        `status=${result.status}`,
        `errorCode=${result.errorCode}`,
        result.error,
        result.stderr
      ].join("\n"),
      stdout: "",
      timedOut: false,
      untrusted: true,
      failureKind: "inconclusive"
    };
  }
  const files = result.stdout.split("\0").filter(Boolean).sort();
  const budget = untrackedFingerprintBudget(options.env ?? process.env);
  if (files.length > budget.maxFiles) {
    return budgetExceededPart(`files=${files.length} maxFiles=${budget.maxFiles}`);
  }
  const fingerprints = [];
  let untrusted = false;
  for (const file of files) {
    const safePath = safeWorkspacePath(topLevel, file);
    const fingerprint = safePath ? fileFingerprint(safePath, budget) : { type: "outside-workspace" };
    if (fingerprint.type === "budget-exceeded") {
      return budgetExceededPart(`file=${file} size=${fingerprint.size} remainingBytes=${fingerprint.remainingBytes}`);
    }
    if (!isTrustedUntrackedFingerprint(fingerprint)) {
      untrusted = true;
    }
    fingerprints.push({
      path: file,
      fingerprint
    });
  }
  return {
    text: JSON.stringify(fingerprints),
    stdout: result.stdout,
    timedOut: false,
    untrusted,
    failureKind: untrusted ? "untracked-untrusted" : ""
  };
}

function hashParts(parts) {
  return createHash("sha256").update(parts.map((part) => part.text).join(FINGERPRINT_SEPARATOR)).digest("hex");
}

export function worktreeFingerprint(cwd = process.cwd(), options = {}) {
  const topLevelPart = repoTopLevelPart(cwd, options);
  if (!topLevelPart.topLevel) {
    const fingerprint = hashParts([topLevelPart]);
    return {
      fingerprint,
      hash: fingerprint,
      text: topLevelPart.text,
      status: "inconclusive",
      timedOut: topLevelPart.timedOut,
      nonGit: Boolean(topLevelPart.nonGit),
      untrusted: true,
      failureKind: topLevelPart.failureKind ?? ""
    };
  }
  const probeCwd = topLevelPart.topLevel;
  const headPart = fingerprintPart(probeCwd, ["rev-parse", "HEAD"], options);
  const parts = [
    headPart,
    fingerprintPart(probeCwd, ["status", "--porcelain=v1"], options),
    fingerprintPart(probeCwd, ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "--stat"], options),
    fingerprintPart(probeCwd, ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "--binary"], options),
    fingerprintPart(probeCwd, ["diff", "--cached", "--no-color", "--no-ext-diff", "--no-textconv", "--binary"], options),
    untrackedFilesFingerprintPart(probeCwd, options)
  ];
  const fingerprint = hashParts(parts);
  const timedOut = parts.some((part) => part.timedOut);
  const untrusted = parts.some((part) => part.untrusted);
  const nonGit = parts.some((part) => part.nonGit);
  return {
    fingerprint,
    hash: fingerprint,
    text: parts.map((part) => part.text).join(FINGERPRINT_SEPARATOR),
    status: timedOut || untrusted || nonGit ? "inconclusive" : "trusted",
    timedOut,
    nonGit,
    untrusted,
    failureKind: parts.find((part) => part.failureKind)?.failureKind ?? "",
    budgetExceeded: parts.some((part) => part.budgetExceeded)
  };
}
