import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { canonicalWorkspaceRoot } from "./workspace.mjs";
import { stateDirForCwd } from "./state.mjs";

const TELEMETRY_OFF_ENV = "CLAUDE_FOR_CODEX_NO_TELEMETRY";

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

function severityCounts(findings = []) {
  const counts = {};
  for (const finding of Array.isArray(findings) ? findings : []) {
    const severity = typeof finding?.severity === "string" ? finding.severity : "unknown";
    counts[severity] = (counts[severity] ?? 0) + 1;
  }
  return counts;
}

function structuredSummary(parsed) {
  if (!parsed || typeof parsed !== "object") {
    return undefined;
  }
  return {
    verdict: typeof parsed.verdict === "string" ? parsed.verdict : undefined,
    findingCount: Array.isArray(parsed.findings) ? parsed.findings.length : undefined,
    severityCounts: severityCounts(parsed.findings)
  };
}

export function reportFromResult({ command, args = {}, result, startedAt, endedAt, parsed, roleResults = [] }) {
  const startMs = Date.parse(startedAt);
  const endMs = Date.parse(endedAt);
  return {
    version: 1,
    command,
    backend: "cli",
    scope: args.scope ?? "auto",
    model: args.model ?? "",
    effort: args.effort ?? "",
    jsonOutput: Boolean(args.jsonOutput),
    writeMode: Boolean(args.write),
    roles: Array.isArray(args.reviewRoles) ? args.reviewRoles.map((role) => role.name) : undefined,
    lenses: Array.isArray(args.resolvedAdversarialLenses) ? args.resolvedAdversarialLenses.map((lens) => lens.name) : undefined,
    startedAt,
    endedAt,
    durationMs: Number.isFinite(startMs) && Number.isFinite(endMs) ? Math.max(0, endMs - startMs) : undefined,
    exitStatus: result?.status ?? undefined,
    stdoutBytes: byteLength(result?.stdout),
    stderrBytes: byteLength(result?.stderr),
    errorCode: result?.errorCode ?? "",
    errorPresent: Boolean(result?.error),
    structured: structuredSummary(parsed),
    roleResults: roleResults.map(({ role, result: roleResult, parsed: roleParsed }) => ({
      role: role?.name ?? String(role ?? ""),
      exitStatus: roleResult?.status ?? undefined,
      stdoutBytes: byteLength(roleResult?.stdout),
      stderrBytes: byteLength(roleResult?.stderr),
      errorPresent: Boolean(roleResult?.error),
      structured: structuredSummary(roleParsed)
    })).filter((entry) => entry.role),
    workspaceId: workspaceId(process.cwd())
  };
}

export function writeReport(cwd, report, env = process.env) {
  if (env[TELEMETRY_OFF_ENV] === "1") {
    return { written: false, reason: `${TELEMETRY_OFF_ENV}=1` };
  }
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
  return { written: true, file };
}

export function safeWriteReport(cwd, report, env = process.env) {
  try {
    return writeReport(cwd, report, env);
  } catch (error) {
    return { written: false, reason: error?.message ? String(error.message) : String(error) };
  }
}

export function listReports(cwd = process.cwd(), env = process.env) {
  const dir = reportsDirForCwd(cwd, env);
  if (!fs.existsSync(dir)) {
    return { reportsDir: dir, reports: [] };
  }
  const reports = fs.readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => {
      const file = path.join(dir, name);
      try {
        return {
          file,
          ...JSON.parse(fs.readFileSync(file, "utf8"))
        };
      } catch (error) {
        return {
          file,
          status: "corrupt",
          error: error?.message ? String(error.message) : String(error)
        };
      }
    })
    .sort((left, right) => String(right.startedAt ?? right.file).localeCompare(String(left.startedAt ?? left.file)));
  return { reportsDir: dir, reports };
}

export function latestReport(cwd = process.cwd(), env = process.env) {
  const listed = listReports(cwd, env);
  return {
    reportsDir: listed.reportsDir,
    report: listed.reports[0] ?? null
  };
}
