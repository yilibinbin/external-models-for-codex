import fs from "node:fs";
import path from "node:path";
import { RELEASE_REF } from "./version.mjs";

const WORKFLOW_RELATIVE_PATH = path.join(".github", "workflows", "antigravity-for-codex-review.yml");
const DEFAULT_RELEASE_REF = RELEASE_REF;
const DEFAULT_TIMEOUT_MINUTES = 30;
const LOCAL_PATH_PATTERN = /\/Users\/[A-Za-z0-9._/-]+|\/home\/[A-Za-z0-9._/-]+|\/private\/var\/folders\/[A-Za-z0-9._/-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._\\/-]+/;

function result(ok, name, detail = "") {
  return { ok, name, detail };
}

function readTemplate(pluginRoot) {
  return fs.readFileSync(path.join(pluginRoot, "templates", "github-actions", "antigravity-for-codex-review.yml"), "utf8");
}

function escapeYamlString(value) {
  return String(value ?? "").replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\r?\n/g, " ");
}

function validateReleaseRef(value) {
  const ref = String(value ?? DEFAULT_RELEASE_REF).trim();
  const lower = ref.toLowerCase();
  if (!ref || lower === "main" || lower === "master" || lower === "head" || lower.startsWith("refs/heads/") || lower.startsWith("heads/")) {
    throw new Error("--ref must be an immutable tag or commit SHA, not a mutable branch.");
  }
  if (!/^[A-Za-z0-9._/-]+$/.test(ref)) {
    throw new Error("--ref contains unsupported characters.");
  }
  if (!/^antigravity-for-codex-v\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?$/.test(ref) && !/^[a-fA-F0-9]{40}$/.test(ref)) {
    throw new Error("--ref must be an Antigravity release tag or a full commit SHA.");
  }
  return ref;
}

function validateProvider(value) {
  const provider = String(value ?? "gemini").trim().toLowerCase();
  if (provider !== "gemini" && provider !== "claude") {
    throw new Error("--model-provider must be gemini or claude.");
  }
  return provider;
}

export function workflowPath(cwd = process.cwd()) {
  return path.join(cwd, WORKFLOW_RELATIVE_PATH);
}

export function renderWorkflow(pluginRoot, options = {}) {
  const timeoutMinutes = Number(options.timeoutMinutes ?? DEFAULT_TIMEOUT_MINUTES);
  if (!Number.isFinite(timeoutMinutes) || timeoutMinutes < 5 || timeoutMinutes > 120) {
    throw new Error("--timeout-minutes must be between 5 and 120.");
  }
  const provider = validateProvider(options.modelProvider);
  const releaseRef = validateReleaseRef(options.releaseRef);
  const model = String(options.model ?? "").trim();
  return readTemplate(pluginRoot)
    .replaceAll("{{TIMEOUT_MINUTES}}", String(timeoutMinutes))
    .replaceAll("{{MODEL_PROVIDER}}", provider)
    .replaceAll("{{MODEL}}", escapeYamlString(model))
    .replaceAll("{{RELEASE_REF}}", releaseRef);
}

export function extractRunBlocks(text) {
  const lines = String(text ?? "").split(/\r?\n/);
  const blocks = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const match = line.match(/^(\s*)run:\s*\|/);
    if (!match) continue;
    const baseIndent = match[1].length;
    const block = [];
    for (let cursor = index + 1; cursor < lines.length; cursor += 1) {
      const current = lines[cursor];
      if (current.trim() && current.match(/^(\s*)/)[1].length <= baseIndent) break;
      block.push(current);
    }
    blocks.push(block.join("\n"));
  }
  return blocks;
}

function uncommentedBody(text) {
  return String(text ?? "")
    .split(/\r?\n/)
    .filter((line) => !line.trimStart().startsWith("#"))
    .join("\n");
}

function hasExactContentsRead(text) {
  const lines = text.split(/\r?\n/);
  const permissionsIndex = lines.findIndex((line) => /^permissions:\s*$/.test(line));
  if (permissionsIndex < 0) return false;
  if (lines.some((line) => /^\s+permissions:\s*$/.test(line))) return false;
  let foundContentsRead = false;
  for (let index = permissionsIndex + 1; index < lines.length; index += 1) {
    const line = lines[index].replace(/\s+#.*$/, "");
    if (line.trim() && !line.startsWith(" ")) {
      break;
    }
    const permission = line.match(/^\s{2}([A-Za-z0-9_-]+):\s*(\S+)\s*$/);
    if (!permission) continue;
    const [, name, value] = permission;
    if (value === "write") return false;
    if (name === "contents") {
      if (value !== "read") return false;
      foundContentsRead = true;
    }
  }
  return foundContentsRead;
}

function marketplaceInstallUsesImmutableRef(text) {
  const lines = text.split(/\r?\n/);
  return lines.some((line) => {
    if (!line.includes("codex plugin marketplace add yilibinbin/external-models-for-codex")) {
      return false;
    }
    const match = line.match(/\s--ref\s+([^\s]+)/);
    if (!match) return false;
    return /^(?:antigravity-for-codex-v\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?|[a-fA-F0-9]{40})$/.test(match[1]);
  });
}

export function validateWorkflow(text) {
  const body = String(text ?? "");
  const activeBody = uncommentedBody(body);
  const runBlocks = extractRunBlocks(body);
  const checks = [
    result(activeBody.includes("pull_request:"), "has-pull-request-trigger"),
    result(!activeBody.includes("pull_request_target"), "no-pull-request-target"),
    result(hasExactContentsRead(activeBody), "minimal-contents-permission"),
    result(activeBody.includes("npm install -g @openai/codex"), "codex-cli-install"),
    result(activeBody.includes("codex plugin marketplace add yilibinbin/external-models-for-codex"), "marketplace-install"),
    result(activeBody.includes("codex plugin add antigravity-for-codex@external-models-for-codex"), "plugin-install"),
    result(activeBody.includes("codex plugin list --json") && activeBody.includes("ANTIGRAVITY_PLUGIN_ROOT=$ANTIGRAVITY_PLUGIN_ROOT"), "plugin-root-resolved"),
    result(marketplaceInstallUsesImmutableRef(activeBody), "immutable-release-ref"),
    result(activeBody.includes("ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER: \"{{MODEL_PROVIDER}}\"") || activeBody.includes("ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER:"), "provider-env"),
    result(activeBody.includes("args=(review") && activeBody.includes("$ANTIGRAVITY_PLUGIN_ROOT/scripts/antigravity-companion.mjs"), "review-command"),
    result(!activeBody.includes("node plugins/antigravity-for-codex/scripts/"), "no-repo-relative-runtime-path"),
    result(!activeBody.includes("--dangerously-skip-permissions"), "no-dangerous-permission-flag"),
    result(!LOCAL_PATH_PATTERN.test(activeBody), "no-local-absolute-paths"),
    result(runBlocks.length > 0 && runBlocks.every((block) => !block.includes("${{ github.")), "no-github-context-in-run")
  ];
  return { ok: checks.every((check) => check.ok), checks };
}

export function writeWorkflow(cwd, text, options = {}) {
  const target = workflowPath(cwd);
  if (fs.existsSync(target) && !options.force) {
    throw new Error(`${WORKFLOW_RELATIVE_PATH} already exists; pass --force to overwrite.`);
  }
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, text, "utf8");
  return target;
}
