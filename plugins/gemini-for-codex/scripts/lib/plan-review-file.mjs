import fs from "node:fs";
import path from "node:path";
import { canonicalWorkspaceRoot } from "./workspace.mjs";

export const MAX_PLAN_REVIEW_BYTES = 256 * 1024;

function planReviewError(message, code = "PLAN_REVIEW_FILE_ERROR") {
  const error = new Error(message);
  error.code = code;
  return error;
}

function planTooLargeError(maxBytes) {
  return planReviewError(`PLAN_TOO_LARGE: --plan file exceeds ${maxBytes} bytes.`, "PLAN_TOO_LARGE");
}

function realpathIfPossible(filePath) {
  try {
    return fs.realpathSync.native(filePath);
  } catch {
    return path.resolve(filePath);
  }
}

function isPathInside(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === "" || (relative && !relative.startsWith("..") && !path.isAbsolute(relative));
}

function displayWorkspaceRoot(cwd, workspaceReal) {
  const cwdResolved = path.resolve(cwd);
  const cwdReal = realpathIfPossible(cwdResolved);
  if (!isPathInside(cwdReal, workspaceReal)) {
    return workspaceReal;
  }
  return path.resolve(cwdResolved, path.relative(cwdReal, workspaceReal));
}

function assertWorkspacePath(candidate, roots) {
  if (!roots.some((root) => isPathInside(candidate, root))) {
    throw planReviewError("--plan must be inside the workspace.", "PLAN_OUTSIDE_WORKSPACE");
  }
}

function assertRegularIdentity(before, after) {
  if (before.dev !== after.dev || before.ino !== after.ino) {
    throw planReviewError("--plan changed while being opened; retry with a stable regular file.", "PLAN_CHANGED");
  }
}

function openedRealpath(requestedAbsolute) {
  try {
    return fs.realpathSync.native(requestedAbsolute);
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "ENOTDIR") {
      throw planReviewError("--plan changed while being opened; retry with a stable regular file.", "PLAN_CHANGED");
    }
    throw error;
  }
}

export function readBoundedUtf8FromFd(fd, maxBytes = MAX_PLAN_REVIEW_BYTES) {
  const chunks = [];
  let total = 0;
  while (total < maxBytes + 1) {
    const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, maxBytes + 1 - total));
    const bytesRead = fs.readSync(fd, buffer, 0, buffer.length, null);
    if (bytesRead === 0) {
      break;
    }
    chunks.push(buffer.subarray(0, bytesRead));
    total += bytesRead;
  }
  if (total > maxBytes) {
    throw planTooLargeError(maxBytes);
  }
  return Buffer.concat(chunks, total).toString("utf8");
}

export function readWorkspaceBoundPlanFile(inputPath, cwd = process.cwd()) {
  if (typeof inputPath !== "string" || !inputPath.trim()) {
    throw planReviewError("--plan requires a file path.", "PLAN_MISSING");
  }
  const workspaceReal = realpathIfPossible(canonicalWorkspaceRoot(cwd));
  const workspaceDisplay = displayWorkspaceRoot(cwd, workspaceReal);
  const lexicalRoots = [...new Set([workspaceReal, workspaceDisplay].map((entry) => path.resolve(entry)))];
  const requestedAbsolute = path.resolve(cwd, inputPath);
  assertWorkspacePath(requestedAbsolute, lexicalRoots);
  const parent = path.dirname(requestedAbsolute);
  let parentReal;
  try {
    parentReal = fs.realpathSync.native(parent);
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "ENOTDIR") {
      throw planReviewError("--plan file does not exist.", "PLAN_NOT_FOUND");
    }
    throw error;
  }
  assertWorkspacePath(parentReal, [workspaceReal]);
  let initialStat;
  try {
    initialStat = fs.lstatSync(requestedAbsolute);
  } catch (error) {
    if (error?.code === "ENOENT" || error?.code === "ENOTDIR") {
      throw planReviewError("--plan file does not exist.", "PLAN_NOT_FOUND");
    }
    throw error;
  }
  if (initialStat.isSymbolicLink()) {
    throw planReviewError("--plan must not be a symlink.", "PLAN_SYMLINK");
  }
  if (!initialStat.isFile()) {
    throw planReviewError("--plan must be a regular file.", "PLAN_NOT_REGULAR");
  }
  if (initialStat.size > MAX_PLAN_REVIEW_BYTES) {
    throw planTooLargeError(MAX_PLAN_REVIEW_BYTES);
  }
  const noFollow = fs.constants.O_NOFOLLOW ?? 0;
  let fd;
  try {
    fd = fs.openSync(requestedAbsolute, fs.constants.O_RDONLY | noFollow);
  } catch (error) {
    if (error?.code === "ELOOP") {
      throw planReviewError("--plan must not be a symlink.", "PLAN_SYMLINK");
    }
    if (error?.code === "ENOENT" || error?.code === "ENOTDIR") {
      throw planReviewError("--plan file does not exist.", "PLAN_NOT_FOUND");
    }
    if (error?.code === "EISDIR") {
      throw planReviewError("--plan must be a regular file.", "PLAN_NOT_REGULAR");
    }
    throw error;
  }
  try {
    const openedStat = fs.fstatSync(fd);
    assertRegularIdentity(initialStat, openedStat);
    if (!openedStat.isFile()) {
      throw planReviewError("--plan must be a regular file.", "PLAN_NOT_REGULAR");
    }
    if (openedStat.size > MAX_PLAN_REVIEW_BYTES) {
      throw planTooLargeError(MAX_PLAN_REVIEW_BYTES);
    }
    const openedAbsolute = openedRealpath(requestedAbsolute);
    assertWorkspacePath(openedAbsolute, [workspaceReal]);
    const openedPathStat = fs.statSync(openedAbsolute);
    assertRegularIdentity(openedStat, openedPathStat);
    const text = readBoundedUtf8FromFd(fd, MAX_PLAN_REVIEW_BYTES);
    const finalStat = fs.fstatSync(fd);
    assertRegularIdentity(openedStat, finalStat);
    if (finalStat.size > MAX_PLAN_REVIEW_BYTES) {
      throw planTooLargeError(MAX_PLAN_REVIEW_BYTES);
    }
    const relative = path.relative(workspaceReal, openedAbsolute) || path.basename(openedAbsolute);
    return { absolute: openedAbsolute, relative, text };
  } finally {
    fs.closeSync(fd);
  }
}
