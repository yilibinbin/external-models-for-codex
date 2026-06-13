import fs from "node:fs";
import path from "node:path";

const WORKFLOW_RELATIVE_PATH = path.join(".github", "workflows", "gemini-for-codex-review.yml");
const DEFAULT_RELEASE_REF = "gemini-for-codex-v0.11.3";
const DEFAULT_TIMEOUT_MINUTES = 30;
const LOCAL_PATH_PATTERN = /\/Users\/[A-Za-z0-9._/-]+|\/private\/var\/folders\/[A-Za-z0-9._/-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._\\/-]+/;
const MARKER = "<!-- gemini-for-codex-review -->";

function result(ok, name, detail = "") {
  return { ok, name, detail };
}

function readTemplate(pluginRoot) {
  return fs.readFileSync(path.join(pluginRoot, "templates", "github-actions", "gemini-for-codex-review.yml"), "utf8");
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

function validateContextProvider(value) {
  const provider = String(value ?? "off").trim();
  if (!provider) {
    throw new Error("--context-provider requires a value.");
  }
  if (provider === "auto") {
    throw new Error("--context-provider auto is not supported for CI workflows; use off or an explicit provider.");
  }
  if (!/^[A-Za-z0-9_.-]{1,64}$/.test(provider)) {
    throw new Error("--context-provider must be off or a provider name.");
  }
  return provider;
}

export function workflowPath(cwd = process.cwd()) {
  return path.join(cwd, WORKFLOW_RELATIVE_PATH);
}

export function renderWorkflow(pluginRoot, options = {}) {
  const annotations = Boolean(options.annotations);
  const timeoutMinutes = Number(options.timeoutMinutes ?? DEFAULT_TIMEOUT_MINUTES);
  if (!Number.isFinite(timeoutMinutes) || timeoutMinutes < 5 || timeoutMinutes > 120) {
    throw new Error("--timeout-minutes must be between 5 and 120.");
  }
  const releaseRef = validateReleaseRef(options.releaseRef);
  const contextProvider = validateContextProvider(options.contextProvider ?? "off");
  const model = String(options.model ?? "").trim();
  const modelArg = model ? `--model "${escapeYamlString(model)}"` : "";
  const modelArgSuffix = modelArg ? ` ${modelArg}` : "";
  const annotationStep = annotations ? [
    "",
    "      - name: Render Checks annotations",
    "        if: steps.fork-safety.outputs.safe_to_review == 'true'",
    "        shell: bash",
    "        run: |",
    "          set -euo pipefail",
    "          node plugins/gemini-for-codex/scripts/gemini-companion.mjs github-actions render-annotations --input gemini-for-codex-review.json > gemini-for-codex-annotations.json",
    "",
    "      - name: Publish Checks annotations",
    "        if: steps.fork-safety.outputs.safe_to_review == 'true'",
    "        uses: actions/github-script@v7",
    "        with:",
    "          script: |",
    "            const fs = require('fs');",
    "            const annotations = JSON.parse(fs.readFileSync('gemini-for-codex-annotations.json', 'utf8'));",
    "            const { owner, repo } = context.repo;",
    "            const head_sha = context.payload.pull_request.head.sha;",
    "            const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));",
    "            async function withBackoff(fn) {",
    "              let lastError;",
    "              for (const delay of [0, 1000, 3000]) {",
    "                if (delay) await sleep(delay);",
    "                try { return await fn(); } catch (error) {",
    "                  lastError = error;",
    "                  if (![403, 429].includes(error.status)) throw error;",
    "                }",
    "              }",
    "              throw lastError;",
    "            }",
    "            await withBackoff(() => github.rest.checks.create({",
    "              owner,",
    "              repo,",
    "              name: 'Gemini for Codex Review',",
    "              head_sha,",
    "              status: 'completed',",
    "              conclusion: annotations.some((item) => item.annotation_level === 'failure') ? 'failure' : 'success',",
    "              output: {",
    "                title: 'Gemini for Codex Review',",
    "                summary: `${annotations.length} annotation(s)`,",
    "                annotations",
    "              }",
    "            }));"
  ].join("\n") : "";
  return readTemplate(pluginRoot)
    .replaceAll("{{CHECKS_PERMISSION}}", annotations ? "  checks: write" : "")
    .replaceAll("{{TIMEOUT_MINUTES}}", String(timeoutMinutes))
    .replaceAll("{{MODEL}}", escapeYamlString(model))
    .replaceAll("{{MODEL_ARG_SUFFIX}}", modelArgSuffix)
    .replaceAll("{{RELEASE_REF}}", releaseRef)
    .replaceAll("{{CONTEXT_PROVIDER}}", contextProvider)
    .replaceAll("{{ANNOTATION_STEP}}", annotationStep);
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

export function validateWorkflow(text, options = {}) {
  const body = String(text ?? "");
  const runBlocks = extractRunBlocks(body);
  const checks = [
    result(body.includes("pull_request:"), "has-pull-request-trigger"),
    result(!body.includes("pull_request_target"), "no-pull-request-target"),
    result(body.includes("contents: read") && body.includes("pull-requests: write"), "minimal-permissions"),
    result(!options.annotations || body.includes("checks: write"), "checks-permission-when-annotations"),
    result(body.includes("npm install -g @openai/codex"), "codex-cli-install"),
    result(body.includes("codex plugin marketplace add yilibinbin/external-models-for-codex"), "marketplace-install"),
    result(body.includes("codex plugin add gemini-for-codex@external-models-for-codex"), "plugin-install"),
    result(/--ref\s+(?!main\b)[A-Za-z0-9._/-]+/.test(body), "immutable-release-ref"),
    result(!LOCAL_PATH_PATTERN.test(body), "no-local-absolute-paths"),
    result(body.includes("BASE_SHA: ${{ github.event.pull_request.base.sha }}"), "base-sha-env-mapping"),
    result(body.includes("fetch-depth: 0"), "checkout-fetch-depth"),
    result(body.includes("HEAD_REPO: ${{ github.event.pull_request.head.repo.full_name }}") && body.includes("BASE_REPO: ${{ github.repository }}"), "fork-env-mapping"),
    result(body.includes("steps.fork-safety.outputs.safe_to_review == 'true'"), "fork-safe-step-gates"),
    result(body.includes("Gemini review skipped for fork pull request"), "github-actions-fork-safe"),
    result(body.includes("actions/upload-artifact@v4") && body.includes("retention-days: 5"), "structured-artifact"),
    result(runBlocks.length > 0 && runBlocks.every((block) => !block.includes("${{ github.")), "no-github-context-in-run"),
    result(body.includes('"$BASE_SHA"') && body.includes('"$HEAD_REPO"') && body.includes('"$BASE_REPO"'), "quoted-shell-vars"),
    result(!body.includes("rescue --write"), "no-write-rescue"),
    result(!options.annotations || (body.includes("github.rest.checks.create") && body.includes("render-annotations")), "checks-api-submit")
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

function asText(value) {
  return value === null || value === undefined ? "" : String(value);
}

export function sanitizeText(value) {
  return asText(value)
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .replace(LOCAL_PATH_PATTERN, "[local-path]")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function findingLine(finding) {
  const file = sanitizeText(finding.file ?? "unknown");
  const line = Number.isInteger(finding.line_start) ? `:${finding.line_start}` : "";
  return `${file}${line}`;
}

export function renderReviewComment(review) {
  const verdict = sanitizeText(review?.verdict ?? "needs-attention");
  const summary = sanitizeText(review?.summary ?? "");
  const findings = Array.isArray(review?.findings) ? review.findings : [];
  const nextSteps = Array.isArray(review?.next_steps) ? review.next_steps : [];
  const lines = [
    MARKER,
    "## Gemini for Codex Review",
    "",
    `Verdict: ${verdict}`,
    "",
    "Summary:",
    summary || "No summary provided.",
    "",
    "Findings:"
  ];
  if (!findings.length) {
    lines.push("- none");
  } else {
    for (const finding of findings) {
      lines.push(`- [${sanitizeText(finding.severity ?? "info")}] ${sanitizeText(finding.title ?? "Finding")}`);
      lines.push(`  ${findingLine(finding)}`);
      lines.push(`  ${sanitizeText(finding.recommendation ?? finding.body ?? "")}`);
    }
  }
  lines.push("", "Next steps:");
  if (!nextSteps.length) {
    lines.push("- Inspect the review result and decide whether changes are required.");
  } else {
    for (const step of nextSteps) {
      lines.push(`- ${sanitizeText(step)}`);
    }
  }
  return lines.join("\n");
}

export function validAnnotationPath(value) {
  const text = asText(value);
  if (!text || text.startsWith("/") || text.includes("..") || /[\u0000-\u001F\u007F]/.test(text) || /^[A-Za-z]:[\\/]/.test(text)) {
    return false;
  }
  return true;
}

function annotationLevel(severity) {
  if (severity === "critical" || severity === "high") return "failure";
  if (severity === "medium") return "warning";
  return "notice";
}

export function reviewToAnnotations(review) {
  const findings = Array.isArray(review?.findings) ? review.findings : [];
  const annotations = [];
  for (const finding of findings) {
    if (!validAnnotationPath(finding.file)) continue;
    const line = Number.isInteger(finding.line_start) && finding.line_start > 0 ? finding.line_start : 1;
    const endLine = Number.isInteger(finding.line_end) && finding.line_end >= line ? finding.line_end : line;
    annotations.push({
      path: finding.file,
      start_line: line,
      end_line: endLine,
      annotation_level: annotationLevel(finding.severity),
      title: sanitizeText(finding.title ?? "Gemini for Codex finding"),
      message: sanitizeText(finding.recommendation ?? finding.body ?? finding.summary ?? "")
    });
  }
  return annotations;
}

export function readReviewJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

export function workflowRelativePath() {
  return WORKFLOW_RELATIVE_PATH;
}
