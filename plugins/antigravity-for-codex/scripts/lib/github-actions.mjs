import fs from "node:fs";
import path from "node:path";

const WORKFLOW_RELATIVE_PATH = path.join(".github", "workflows", "antigravity-for-codex-review.yml");
const DEFAULT_RELEASE_REF = "antigravity-for-codex-v0.1.0";
const DEFAULT_TIMEOUT_MINUTES = 30;
const LOCAL_PATH_PATTERN = /\/Users\/[A-Za-z0-9._/-]+|\/private\/var\/folders\/[A-Za-z0-9._/-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._\\/-]+/;

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
  if (!ref || ref === "main" || ref === "master" || ref === "HEAD") {
    throw new Error("--ref must be an immutable tag or commit SHA, not a mutable branch.");
  }
  if (!/^[A-Za-z0-9._/-]+$/.test(ref)) {
    throw new Error("--ref contains unsupported characters.");
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
  const modelArg = model ? ` --model "${escapeYamlString(model)}"` : "";
  return readTemplate(pluginRoot)
    .replaceAll("{{TIMEOUT_MINUTES}}", String(timeoutMinutes))
    .replaceAll("{{MODEL_PROVIDER}}", provider)
    .replaceAll("{{MODEL}}", escapeYamlString(model))
    .replaceAll("{{MODEL_ARG_SUFFIX}}", modelArg)
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

export function validateWorkflow(text) {
  const body = String(text ?? "");
  const runBlocks = extractRunBlocks(body);
  const checks = [
    result(body.includes("pull_request:"), "has-pull-request-trigger"),
    result(!body.includes("pull_request_target"), "no-pull-request-target"),
    result(body.includes("contents: read"), "minimal-contents-permission"),
    result(body.includes("npm install -g @openai/codex"), "codex-cli-install"),
    result(body.includes("codex plugin marketplace add yilibinbin/external-models-for-codex"), "marketplace-install"),
    result(body.includes("codex plugin add antigravity-for-codex@external-models-for-codex"), "plugin-install"),
    result(body.includes("antigravity-for-codex-v0.1.0"), "immutable-release-ref"),
    result(body.includes("ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER: \"{{MODEL_PROVIDER}}\"") || body.includes("ANTIGRAVITY_FOR_CODEX_MODEL_PROVIDER:"), "provider-env"),
    result(body.includes("antigravity-companion.mjs review"), "review-command"),
    result(!body.includes("--dangerously-skip-permissions"), "no-dangerous-permission-flag"),
    result(!LOCAL_PATH_PATTERN.test(body), "no-local-absolute-paths"),
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
