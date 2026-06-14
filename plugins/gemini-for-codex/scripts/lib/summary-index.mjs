import path from "node:path";
import { atomicWriteJson, readJson, stateDirForCwd } from "./state.mjs";
import { sanitizeSummary } from "./sanitize.mjs";
import { assertSafeStateId } from "./state-ids.mjs";

export function summariesDirForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateDirForCwd(cwd, env), "summaries");
}

function summaryFile(cwd, loopId, round, env) {
  if (!Number.isInteger(round) || round < 1) {
    throw new Error(`Round must be a positive integer; received ${JSON.stringify(round)}.`);
  }
  const dir = summariesDirForCwd(cwd, env);
  const safeLoopId = assertSafeStateId(loopId, "loop id");
  const file = path.join(dir, `${safeLoopId}-${String(round).padStart(3, "0")}.json`);
  const relative = path.relative(dir, file);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("Summary path escaped state directory.");
  }
  return file;
}

export function writeRoundSummary(cwd, loopId, round, summary, env = process.env) {
  const payload = {
    schema_version: 1,
    loopId: assertSafeStateId(loopId, "loop id"),
    round,
    createdAt: new Date().toISOString(),
    command: sanitizeSummary(summary.command || "", { cwd, maxBytes: 2048 }),
    verdict: summary.verdict || "",
    scoreTotal: Number.isFinite(summary.scoreTotal) ? summary.scoreTotal : null,
    threshold: Number.isFinite(summary.threshold) ? summary.threshold : 85,
    blockingFindings: Number.isInteger(summary.blockingFindings) && summary.blockingFindings >= 0 ? summary.blockingFindings : 0,
    failureCategory: summary.failureCategory || "",
    acceptedFindingIds: Array.isArray(summary.acceptedFindingIds) ? summary.acceptedFindingIds.filter((item) => typeof item === "string") : []
  };
  atomicWriteJson(summaryFile(cwd, loopId, round, env), payload);
  return payload;
}

export function readRoundSummary(cwd, loopId, round, env = process.env) {
  return readJson(summaryFile(cwd, loopId, round, env));
}
