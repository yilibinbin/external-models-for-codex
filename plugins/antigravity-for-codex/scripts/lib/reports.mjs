import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { canonicalWorkspaceRoot, stateDirForCwd } from "./state.mjs";

function reportsDirForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateDirForCwd(cwd, env), "reports");
}
function workspaceId(cwd = process.cwd()) {
  return createHash("sha256").update(canonicalWorkspaceRoot(cwd)).digest("hex").slice(0, 16);
}

function nowStamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function byteLength(value) {
  return Buffer.byteLength(String(value ?? ""), "utf8");
}

function structuredSummary(parsed) {
  if (!parsed || typeof parsed !== "object") {
    return undefined;
  }
  return {
    verdict: typeof parsed.verdict === "string" ? parsed.verdict : undefined,
    findingCount: Array.isArray(parsed.findings) ? parsed.findings.length : undefined
  };
}

export function operationReport({ command, args = {}, result, startedAt, endedAt, parsed }) {
  const startMs = Date.parse(startedAt);
  const endMs = Date.parse(endedAt);
  return {
    version: 1,
    command,
    status: result?.status ?? undefined,
    modelProvider: result?.provider?.modelProvider ?? args.modelProvider ?? "",
    model: result?.provider?.model ?? args.model ?? "",
    structured: structuredSummary(parsed),
    json: Boolean(args.json),
    startedAt,
    endedAt,
    durationMs: Number.isFinite(startMs) && Number.isFinite(endMs) ? Math.max(0, endMs - startMs) : undefined,
    outcome: result?.outcome?.kind || "",
    retryable: Boolean(result?.outcome?.retryable),
    stdoutBytes: byteLength(result?.stdout),
    stderrBytes: byteLength(result?.stderr),
    errorCode: result?.errorCode ?? "",
    errorPresent: Boolean(result?.error),
    workspaceId: workspaceId(process.cwd())
  };
}

export function writeOperationReport(cwd, report, env = process.env) {
  const dir = reportsDirForCwd(cwd, env);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try {
    fs.chmodSync(dir, 0o700);
  } catch {
    // Non-POSIX filesystems may not support chmod.
  }
  const file = path.join(dir, `${nowStamp()}-${process.pid}-${Math.random().toString(16).slice(2, 8)}.json`);
  const tmpFile = `${file}.tmp`;
  fs.writeFileSync(tmpFile, `${JSON.stringify(report, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(tmpFile, file);
  return { file };
}

export function latestReport(cwd = process.cwd(), env = process.env) {
  const dir = reportsDirForCwd(cwd, env);
  if (!fs.existsSync(dir)) {
    return null;
  }
  const reports = fs.readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => {
      const file = path.join(dir, name);
      try {
        return {
          file,
          report: JSON.parse(fs.readFileSync(file, "utf8"))
        };
      } catch {
        return null;
      }
    })
    .filter(Boolean)
    .sort((left, right) => String(right.report?.startedAt ?? right.file).localeCompare(String(left.report?.startedAt ?? left.file)));
  return reports[0]?.report ?? null;
}
