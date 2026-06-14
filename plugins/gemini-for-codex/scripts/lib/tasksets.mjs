import path from "node:path";
import { randomUUID } from "node:crypto";
import { atomicWriteJson, readJson, stateDirForCwd } from "./state.mjs";
import { assertSafeStateId } from "./state-ids.mjs";

const SAFE_TASKSET_ID = /^ts-[A-Za-z0-9_-]+$/;
const SAFE_SUBTASK_ID = /^T-[0-9]{3,6}$/;
const VALID_SOURCES = new Set(["manual", "issue", "diff", "plan"]);
const VALID_RISKS = new Set(["low", "medium", "high"]);
const VALID_TYPES = new Set(["code", "ui", "docs", "tests", "release", "security"]);
const VALID_STATUSES = new Set(["pending", "reviewed", "accepted", "needs-work"]);

function assertPlainObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be a JSON object.`);
}

function normalizeEnum(value, valid, fallback) {
  const normalized = String(value ?? "").trim().toLowerCase();
  return valid.has(normalized) ? normalized : fallback;
}

function stringList(value) {
  return Array.isArray(value)
    ? value.filter((item) => typeof item === "string" && item.trim()).map((item) => item.trim())
    : [];
}

function assertSafeTasksetId(id) {
  const text = assertSafeStateId(id, "taskset id");
  if (!SAFE_TASKSET_ID.test(text)) throw new Error("Taskset id must match ts-[A-Za-z0-9_-]+ and contain no path separators.");
  return text;
}

export function tasksetsDirForCwd(cwd = process.cwd(), env = process.env) {
  return path.join(stateDirForCwd(cwd, env), "tasksets");
}

function tasksetFile(cwd, id, env) {
  const dir = tasksetsDirForCwd(cwd, env);
  const file = path.join(dir, `${assertSafeTasksetId(id)}.json`);
  const relative = path.relative(dir, file);
  if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("Taskset path escaped state directory.");
  return file;
}

export function normalizeTaskset(input) {
  assertPlainObject(input, "taskset");
  const subtasks = Array.isArray(input.subtasks) ? input.subtasks : [];
  const seenSubtaskIds = new Set();
  let nextSubtaskNumber = 1;
  function uniqueSubtaskId(task, index) {
    const requested = String(task.id || "");
    if (SAFE_SUBTASK_ID.test(requested) && !seenSubtaskIds.has(requested)) {
      seenSubtaskIds.add(requested);
      return requested;
    }
    while (true) {
      const generated = `T-${String(Math.max(nextSubtaskNumber, index + 1)).padStart(3, "0")}`;
      nextSubtaskNumber += 1;
      if (!seenSubtaskIds.has(generated)) {
        seenSubtaskIds.add(generated);
        return generated;
      }
    }
  }
  return {
    schema_version: 1,
    id: assertSafeTasksetId(input.id || `ts-${randomUUID()}`),
    source: normalizeEnum(input.source, VALID_SOURCES, "manual"),
    title: String(input.title || "Gemini taskset").trim(),
    createdAt: input.createdAt || new Date().toISOString(),
    updatedAt: input.updatedAt || new Date().toISOString(),
    subtasks: subtasks.map((task, index) => {
      assertPlainObject(task, `subtask ${index + 1}`);
      return {
        id: uniqueSubtaskId(task, index),
        title: String(task.title || "").trim(),
        description: String(task.description || "").trim(),
        acceptance_criteria: stringList(task.acceptance_criteria),
        risk: normalizeEnum(task.risk, VALID_RISKS, "medium"),
        type: normalizeEnum(task.type, VALID_TYPES, "code"),
        status: normalizeEnum(task.status, VALID_STATUSES, "pending"),
        evidence: stringList(task.evidence)
      };
    })
  };
}

export function saveTaskset(cwd, input, env = process.env) {
  const taskset = normalizeTaskset(input);
  taskset.updatedAt = new Date().toISOString();
  const file = tasksetFile(cwd, taskset.id, env);
  atomicWriteJson(file, taskset);
  return { ...taskset, path: file };
}

export function readTaskset(cwd, id, env = process.env) {
  try {
    return { ok: true, taskset: normalizeTaskset(readJson(tasksetFile(cwd, id, env))) };
  } catch (error) {
    return { ok: false, error: error.message || String(error) };
  }
}
