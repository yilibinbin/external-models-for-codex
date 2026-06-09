import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const MAX_WORKTREE_FINGERPRINT_FILE_BYTES = 1024 * 1024;
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
    errorCode: result.error?.code ? String(result.error.code) : ""
  };
}

function gitCommandTimedOut(result) {
  return String(result?.errorCode ?? "") === "ETIMEDOUT";
}

function workingTreeFingerprintPart(cwd, args, options = {}) {
  const result = runGit(cwd, args, options);
  if (gitCommandTimedOut(result)) {
    return { text: `ETIMEDOUT ${args.join(" ")}`, timedOut: true };
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

function fileFingerprint(filePath) {
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
  if (result.status !== 0) {
    return { text: `status=${result.status}\n${result.stderr}`, stdout: "", timedOut: false };
  }
  const files = result.stdout.split("\0").filter(Boolean).sort();
  return {
    text: JSON.stringify(files.map((file) => {
      const safePath = safeWorkspacePath(cwd, file);
      return {
        path: file,
        fingerprint: safePath ? fileFingerprint(safePath) : { type: "unsafe-path" }
      };
    })),
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
