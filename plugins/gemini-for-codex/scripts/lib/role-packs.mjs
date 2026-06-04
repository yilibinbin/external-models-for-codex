import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { canonicalWorkspaceRoot } from "./state.mjs";

const MAX_PACK_BYTES = 64 * 1024;
const MAX_NESTING_DEPTH = 12;
const NAME_PATTERN = /^[a-z][a-z0-9-]{0,63}$/;

const FORBIDDEN_FIELDS = new Set([
  "tools",
  "allowedTools",
  "disallowedTools",
  "shell",
  "command",
  "commands",
  "argv",
  "exec",
  "hooks",
  "env",
  "environment",
  "mcp",
  "mcpServers",
  "write",
  "permissions",
  "permissionMode",
  "backend",
  "provider",
  "contextProvider",
  "promptTemplatePath",
  "extension",
  "extensions",
  "agentFile",
  "agentPath",
  "max_effort"
]);
const ALLOWED_PACK_FIELDS = new Set(["schema_version", "name", "description", "roles", "limits", "tags", "gate_compatible", "native_agents_compatible"]);
const ALLOWED_LIMIT_FIELDS = new Set(["max_roles", "max_native_agents", "estimated_cost_warning_roles"]);
const BOUNDARY_TAGS = [
  "git_context",
  "gemini_context",
  "focus",
  "review_roles",
  "planning_request",
  "rescue_request",
  "role_name",
  "role_directive",
  "subagents",
  "adversarial_lenses",
  "lens",
  "task",
  "rules",
  "output_contract",
  "scale_guidance"
];

export const ADVERSARIAL_LENSES = Object.freeze({
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

export const DEFAULT_ADVERSARIAL_LENSES = Object.freeze(["skeptic", "architect", "minimalist"]);

export const REVIEW_ROLES = Object.freeze({
  correctness: {
    directive: "Find bugs, regressions, edge cases, and behavioral contract breaks.",
    gateCompatible: true
  },
  security: {
    directive: "Review read-only safety, secrets exposure, injection risks, and unsafe command or path handling.",
    gateCompatible: true
  },
  tests: {
    directive: "Find missing, brittle, or overfit tests and release validation gaps.",
    gateCompatible: true
  },
  release: {
    directive: "Review install, marketplace, versioning, documentation, and upgrade risks.",
    gateCompatible: true
  },
  adversarial: {
    directive: "Challenge assumptions, simpler alternatives, hidden costs, and failure modes.",
    gateCompatible: false
  },
  skeptic: {
    directive: ADVERSARIAL_LENSES.skeptic.directive,
    gateCompatible: false
  },
  architect: {
    directive: ADVERSARIAL_LENSES.architect.directive,
    gateCompatible: false
  },
  minimalist: {
    directive: ADVERSARIAL_LENSES.minimalist.directive,
    gateCompatible: false
  }
});

export const DEFAULT_MULTI_REVIEW_ROLES = Object.freeze([
  "correctness",
  "security",
  "tests",
  "release",
  "adversarial"
]);

const BUILT_IN_ROLE_PACKS = Object.freeze({
  default: {
    schema_version: 1,
    name: "default",
    description: "Current default Gemini multi-review team.",
    roles: DEFAULT_MULTI_REVIEW_ROLES,
    limits: { max_roles: 5, max_native_agents: 5, estimated_cost_warning_roles: 5 },
    native_agents_compatible: true
  },
  security: {
    schema_version: 1,
    name: "security",
    description: "Security-focused Gemini review team.",
    roles: ["security", "correctness", "adversarial"],
    limits: { max_roles: 3, max_native_agents: 3, estimated_cost_warning_roles: 3 },
    native_agents_compatible: true
  },
  release: {
    schema_version: 1,
    name: "release",
    description: "Release-readiness Gemini review team.",
    roles: ["release", "tests", "correctness", "security"],
    limits: { max_roles: 4, max_native_agents: 4, estimated_cost_warning_roles: 4 },
    gate_compatible: true,
    native_agents_compatible: true
  },
  frontend: {
    schema_version: 1,
    name: "frontend",
    description: "Frontend-adjacent Gemini preset over correctness, tests, and minimalist roles.",
    roles: ["correctness", "tests", "minimalist"],
    limits: { max_roles: 3, max_native_agents: 3, estimated_cost_warning_roles: 3 },
    native_agents_compatible: true
  },
  backend: {
    schema_version: 1,
    name: "backend",
    description: "Backend correctness, security, and architecture Gemini review team.",
    roles: ["correctness", "security", "tests", "architect"],
    limits: { max_roles: 4, max_native_agents: 4, estimated_cost_warning_roles: 4 },
    native_agents_compatible: true
  },
  testing: {
    schema_version: 1,
    name: "testing",
    description: "Testing and validation focused Gemini review team.",
    roles: ["tests", "correctness", "minimalist"],
    limits: { max_roles: 3, max_native_agents: 3, estimated_cost_warning_roles: 3 },
    native_agents_compatible: true
  },
  docs: {
    schema_version: 1,
    name: "docs",
    description: "Documentation-readiness Gemini preset over release, correctness, and minimalist roles.",
    roles: ["release", "correctness", "minimalist"],
    limits: { max_roles: 3, max_native_agents: 3, estimated_cost_warning_roles: 3 },
    native_agents_compatible: true
  },
  minimal: {
    schema_version: 1,
    name: "minimal",
    description: "Single Gemini correctness reviewer.",
    roles: ["correctness"],
    limits: { max_roles: 1, max_native_agents: 1, estimated_cost_warning_roles: 1 },
    gate_compatible: true,
    native_agents_compatible: true
  }
});

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function stableStringify(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function hasControlCharacters(text) {
  return /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(text);
}

function hasBoundaryTag(text) {
  const lower = text.toLowerCase();
  if (/^---$/m.test(text) || /^(name|description):/im.test(text) || /@gfc_[a-z0-9_]+/i.test(text)) {
    return true;
  }
  return BOUNDARY_TAGS.some((tag) => lower.includes(`<${tag}`) || lower.includes(`</${tag}>`));
}

function validateName(name, label) {
  if (typeof name !== "string" || !NAME_PATTERN.test(name)) {
    throw new Error(`Invalid ${label} "${String(name)}".`);
  }
}

function scanForbiddenFields(value, pathParts = [], depth = 0) {
  if (depth > MAX_NESTING_DEPTH) {
    throw new Error("Role pack nesting is too deep.");
  }
  if (!value || typeof value !== "object") {
    return;
  }
  for (const [key, child] of Object.entries(value)) {
    if (FORBIDDEN_FIELDS.has(key)) {
      throw new Error(`Forbidden role pack field "${[...pathParts, key].join(".")}".`);
    }
    scanForbiddenFields(child, [...pathParts, key], depth + 1);
  }
}

function validateDescription(description) {
  if (typeof description !== "string" || !description.trim()) {
    throw new Error("Role pack description is required.");
  }
  if (Buffer.byteLength(description, "utf8") > 2048) {
    throw new Error("Role pack description is too large.");
  }
  if (hasControlCharacters(description)) {
    throw new Error("Role pack description contains control characters.");
  }
  if (hasBoundaryTag(description)) {
    throw new Error("Role pack description contains reserved prompt or native-agent boundary markers.");
  }
  if (/^(ALLOW|BLOCK):/im.test(description)) {
    throw new Error("Role pack description contains reserved gate verdict markers.");
  }
}

function validateLimits(limits, roleCount) {
  if (limits === undefined) {
    return;
  }
  if (!limits || typeof limits !== "object" || Array.isArray(limits)) {
    throw new Error("Role pack limits must be an object.");
  }
  for (const key of Object.keys(limits)) {
    if (!ALLOWED_LIMIT_FIELDS.has(key)) {
      throw new Error(`Unknown role pack limits field "${key}".`);
    }
  }
  if (limits.max_roles !== undefined && (!Number.isInteger(limits.max_roles) || limits.max_roles < 1 || limits.max_roles > 8)) {
    throw new Error("Role pack limits.max_roles must be an integer from 1 to 8.");
  }
  if (limits.max_roles !== undefined && roleCount > limits.max_roles) {
    throw new Error(`Role pack has ${roleCount} roles, exceeding max_roles ${limits.max_roles}.`);
  }
  if (limits.max_native_agents !== undefined && (!Number.isInteger(limits.max_native_agents) || limits.max_native_agents < 1 || limits.max_native_agents > 8)) {
    throw new Error("Role pack limits.max_native_agents must be an integer from 1 to 8.");
  }
  if (limits.estimated_cost_warning_roles !== undefined && (!Number.isInteger(limits.estimated_cost_warning_roles) || limits.estimated_cost_warning_roles < 1)) {
    throw new Error("Role pack limits.estimated_cost_warning_roles must be a positive integer.");
  }
}

function normalizePack(pack, { source = "user" } = {}) {
  scanForbiddenFields(pack);
  if (!pack || typeof pack !== "object" || Array.isArray(pack)) {
    throw new Error("Role pack must be a JSON object.");
  }
  for (const key of Object.keys(pack)) {
    if (!ALLOWED_PACK_FIELDS.has(key)) {
      throw new Error(`Unknown role pack field "${key}".`);
    }
  }
  if (pack.schema_version !== 1) {
    throw new Error("Role pack schema_version must be 1.");
  }
  validateName(pack.name, "role pack name");
  validateDescription(pack.description);
  if (!Array.isArray(pack.roles) || !pack.roles.length) {
    throw new Error("Role pack roles must be a non-empty array.");
  }
  const seen = new Set();
  for (const role of pack.roles) {
    if (typeof role !== "string") {
      throw new Error("Role pack roles must be strings.");
    }
    validateName(role, "role name");
    if (!Object.hasOwn(REVIEW_ROLES, role)) {
      throw new Error(`Unknown role "${role}" in role pack "${pack.name}".`);
    }
    if (seen.has(role)) {
      throw new Error(`Duplicate role "${role}" in role pack "${pack.name}".`);
    }
    seen.add(role);
  }
  validateLimits(pack.limits, pack.roles.length);
  if (pack.tags !== undefined) {
    if (!Array.isArray(pack.tags) || !pack.tags.every((tag) => typeof tag === "string" && NAME_PATTERN.test(tag))) {
      throw new Error("Role pack tags must be role-pack style identifiers.");
    }
  }
  if (pack.gate_compatible !== undefined && typeof pack.gate_compatible !== "boolean") {
    throw new Error("Role pack gate_compatible must be a boolean.");
  }
  if (pack.native_agents_compatible !== undefined && typeof pack.native_agents_compatible !== "boolean") {
    throw new Error("Role pack native_agents_compatible must be a boolean.");
  }
  return {
    schema_version: 1,
    name: pack.name,
    description: pack.description,
    roles: [...pack.roles],
    limits: pack.limits ? { ...pack.limits } : undefined,
    tags: pack.tags ? [...pack.tags] : undefined,
    gate_compatible: Boolean(pack.gate_compatible),
    native_agents_compatible: pack.native_agents_compatible !== false,
    source
  };
}

function git(args, cwd) {
  const result = spawnSync("git", args, { cwd, encoding: "utf8", timeout: 5000 });
  return result.status === 0 ? result.stdout.trim() : "";
}

function safeRealpath(candidate) {
  try {
    return fs.realpathSync.native(candidate);
  } catch {
    return path.resolve(candidate);
  }
}

function isInside(root, candidate) {
  return candidate === root || candidate.startsWith(`${root}${path.sep}`);
}

function linkedWorktreeRoots(cwd) {
  const output = git(["worktree", "list", "--porcelain"], cwd);
  return output.split(/\r?\n/)
    .filter((line) => line.startsWith("worktree "))
    .map((line) => safeRealpath(line.slice("worktree ".length).trim()))
    .filter(Boolean);
}

function ensureUserPackPathIsRepoExternal(file, cwd) {
  const resolved = path.resolve(file);
  const real = safeRealpath(resolved);
  const workspaceRoot = safeRealpath(canonicalWorkspaceRoot(cwd));
  const gitDir = git(["rev-parse", "--git-common-dir"], cwd);
  const forbiddenRoots = new Set([
    workspaceRoot,
    safeRealpath(cwd),
    ...linkedWorktreeRoots(cwd)
  ]);
  if (gitDir) {
    forbiddenRoots.add(safeRealpath(path.isAbsolute(gitDir) ? gitDir : path.join(workspaceRoot, gitDir)));
  }
  for (const root of forbiddenRoots) {
    if (root && isInside(root, real)) {
      throw new Error("User role pack files must not live inside the workspace.");
    }
  }
  const linkStats = fs.lstatSync(resolved);
  if (linkStats.isSymbolicLink()) {
    for (const root of forbiddenRoots) {
      if (root && isInside(root, real)) {
        throw new Error("User role pack symlinks must not target the workspace.");
      }
    }
  }
  return real;
}

export function validateRolePackObject(pack, options = {}) {
  return normalizePack(pack, options);
}

export function validateRolePackFile(file, options = {}) {
  const cwd = options.cwd ?? process.cwd();
  const real = ensureUserPackPathIsRepoExternal(file, cwd);
  const handle = fs.openSync(real, "r");
  try {
    const before = fs.fstatSync(handle);
    if (before.size > MAX_PACK_BYTES) {
      throw new Error("Role pack file exceeds 64 KiB.");
    }
    const text = fs.readFileSync(handle, "utf8");
    const after = fs.fstatSync(handle);
    if (before.dev !== after.dev || before.ino !== after.ino) {
      throw new Error("Role pack file changed while being read.");
    }
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (error) {
      throw new Error(`Role pack JSON is invalid: ${error.message || String(error)}`);
    }
    return normalizePack(parsed, { source: "user" });
  } finally {
    fs.closeSync(handle);
  }
}

export function resolveRolePack(name) {
  if (!Object.hasOwn(BUILT_IN_ROLE_PACKS, name)) {
    throw new Error(`Unknown role pack "${name}". Valid packs: ${Object.keys(BUILT_IN_ROLE_PACKS).sort().join(", ")}.`);
  }
  return normalizePack(clone(BUILT_IN_ROLE_PACKS[name]), { source: "builtin" });
}

export function rolePackGateCompatible(pack) {
  return pack.roles.every((role) => REVIEW_ROLES[role]?.gateCompatible === true);
}

export function rolePackNativeAgentsCompatible(pack) {
  return pack.native_agents_compatible !== false;
}

export function rolesForPack(pack) {
  return pack.roles.map((name) => ({
    name,
    directive: REVIEW_ROLES[name].directive
  }));
}

export function defaultRoleObjects() {
  return DEFAULT_MULTI_REVIEW_ROLES.map((name) => ({
    name,
    directive: REVIEW_ROLES[name].directive
  }));
}

export function resolveExplicitRoles(roles) {
  const validRoles = Object.keys(REVIEW_ROLES).sort();
  const seenRoles = new Set();
  for (const role of roles) {
    if (!Object.hasOwn(REVIEW_ROLES, role)) {
      throw new Error(`Unknown review role "${role}". Valid roles: ${validRoles.join(", ")}.`);
    }
    if (seenRoles.has(role)) {
      throw new Error(`Duplicate review role "${role}".`);
    }
    seenRoles.add(role);
  }
  return roles.map((name) => ({
    name,
    directive: REVIEW_ROLES[name].directive
  }));
}

export function hashRolePack(pack) {
  const canonical = {
    schema_version: pack.schema_version,
    name: pack.name,
    description: pack.description,
    roles: pack.roles,
    limits: pack.limits ?? {},
    tags: pack.tags ?? [],
    gate_compatible: Boolean(pack.gate_compatible),
    native_agents_compatible: pack.native_agents_compatible !== false
  };
  return `sha256:${createHash("sha256").update(stableStringify(canonical)).digest("hex")}`;
}

export function rolePackSummary(pack) {
  return {
    name: pack.name,
    source: pack.source ?? "builtin",
    schema_version: pack.schema_version,
    description: pack.description,
    roles: [...pack.roles],
    gate_compatible: rolePackGateCompatible(pack),
    native_agents_compatible: rolePackNativeAgentsCompatible(pack),
    hash: hashRolePack(pack)
  };
}

export function listRolePacks() {
  return Object.keys(BUILT_IN_ROLE_PACKS)
    .sort()
    .map((name) => rolePackSummary(resolveRolePack(name)));
}

export function validateBuiltInRolePacks() {
  const failures = [];
  for (const name of Object.keys(BUILT_IN_ROLE_PACKS)) {
    try {
      resolveRolePack(name);
    } catch (error) {
      failures.push(`${name}: ${error.message || String(error)}`);
    }
  }
  return { ok: failures.length === 0, failures };
}

export function rolePackReportMetadata(pack) {
  return {
    name: pack.name,
    source: pack.source ?? "builtin",
    schema_version: pack.schema_version,
    hash: hashRolePack(pack),
    roles: [...pack.roles],
    gate_compatible: rolePackGateCompatible(pack),
    native_agents_compatible: rolePackNativeAgentsCompatible(pack)
  };
}

export function userRolePackDir(env = process.env) {
  return path.join(env.HOME || os.homedir(), ".codex", "gemini-for-codex", "roles");
}
