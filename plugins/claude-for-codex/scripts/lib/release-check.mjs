import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { renderWorkflow, validateWorkflow } from "./github-actions.mjs";
import { validateBuiltInRolePacks } from "./role-packs.mjs";
import { SECRET_PATTERNS, sanitizeSummary } from "./sanitize.mjs";

const SECRET_ASSIGNMENT_PATTERN = /\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*["']([A-Za-z0-9_./+=:-]{16,})["']/i;
const DEFAULT_RELEASE_REF = "claude-for-codex-v0.14.1";
const EXPECTED_SKILLS = [
  "claude-adversarial-review",
  "claude-cancel",
  "claude-collaboration-loop",
  "claude-github-actions-review",
  "claude-leases",
  "claude-mailbox",
  "claude-multi-review",
  "claude-plan",
  "claude-rescue",
  "claude-result",
  "claude-review",
  "claude-review-gate",
  "claude-role-packs",
  "claude-status",
  "claude-subagent-review",
  "claude-ultrareview"
];

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function listFiles(root, dirs) {
  const files = [];
  function walk(current) {
    if (!fs.existsSync(current)) {
      return;
    }
    if (fs.statSync(current).isFile()) {
      files.push(current);
      return;
    }
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      if (entry.name === ".git" || entry.name === "__pycache__" || entry.name === ".pytest_cache") {
        continue;
      }
      const full = path.join(current, entry.name);
      if (full.includes(`${path.sep}docs${path.sep}superpowers${path.sep}`)) {
        continue;
      }
      if (entry.isDirectory()) {
        walk(full);
      } else {
        files.push(full);
      }
    }
  }
  for (const dir of dirs) {
    walk(path.join(root, dir));
  }
  return files;
}

function result(ok, name, detail = "") {
  return { ok, name, detail };
}

function markdownSection(text, heading) {
  const lines = text.split(/\r?\n/);
  const headingLine = `## ${heading}`;
  const start = lines.findIndex((line) => line.trim() === headingLine);
  if (start === -1) {
    return "";
  }
  const end = lines.findIndex((line, index) => index > start && /^##\s/.test(line.trim()));
  const sectionLines = lines.slice(start + 1, end === -1 ? undefined : end);
  return sectionLines.join("\n");
}

function sourceArrayIncludes(text, exportName, values) {
  const match = new RegExp(`${exportName}\\s*=\\s*Object\\.freeze\\(\\s*\\[([^\\]]+)\\]\\s*\\)`).exec(text);
  if (!match) return false;
  const found = new Set([...match[1].matchAll(/["']([^"']+)["']/g)].map((entry) => entry[1]));
  return values.every((value) => found.has(value));
}

function sourceHasAliasProfile(text, alias, effort) {
  return new RegExp(`model\\s*:\\s*["']${alias}["'][\\s\\S]{0,80}effort\\s*:\\s*["']${effort}["']`).test(text);
}

function normalizedWhitespace(text) {
  return String(text ?? "").replace(/\s+/g, " ").trim();
}

function workflowCommandPinsStandardQuality(text) {
  const normalized = normalizedWhitespace(text);
  return /claude-companion\.mjs\s+review\b/.test(normalized)
    && /--json\b/.test(normalized)
    && /--quality\s+standard\b/.test(normalized)
    && /--scope\s+branch\b/.test(normalized);
}

function githubActionsDefaultsToStandardQuality(text) {
  return /quality\s*:\s*["']standard["']/.test(text)
    || /const\s+quality\s*=\s*options\.quality\s*\?\?\s*["']standard["']/.test(text);
}

function resolveLayout(root) {
  const repoPluginRoot = path.join(root, "plugins", "claude-for-codex");
  if (fs.existsSync(path.join(repoPluginRoot, ".codex-plugin", "plugin.json"))) {
    return { repoRoot: root, pluginRoot: repoPluginRoot, installedPluginOnly: false };
  }
  if (fs.existsSync(path.join(root, ".codex-plugin", "plugin.json"))) {
    return { repoRoot: null, pluginRoot: root, installedPluginOnly: true };
  }
  return { repoRoot: root, pluginRoot: repoPluginRoot, installedPluginOnly: false };
}

function commandExists(name) {
  const check = spawnSync(name, ["--version"], { encoding: "utf8", timeout: 5000 });
  return check.status === 0;
}

function checkManifest(root) {
  const { pluginRoot } = resolveLayout(root);
  const manifest = readJson(path.join(pluginRoot, ".codex-plugin", "plugin.json"));
  const changelog = fs.readFileSync(path.join(pluginRoot, "CHANGELOG.md"), "utf8");
  const unreleasedBody = markdownSection(changelog, "Unreleased").trim();
  const checks = [
    result(manifest.version === "0.14.1", "manifest-version", `version=${manifest.version}`),
    result(changelog.includes("## 0.14.1"), "changelog-version", "CHANGELOG contains 0.14.1"),
    result(fs.readFileSync(path.join(pluginRoot, "README.md"), "utf8").includes("Current version: `0.14.1`"), "readme-current-version", "README current version is 0.14.1"),
    result(unreleasedBody.length === 0, "changelog-unreleased-empty", unreleasedBody ? "Unreleased contains entries" : ""),
    result(!Object.prototype.hasOwnProperty.call(manifest, "hooks"), "manifest-no-hooks-field"),
    result(manifest.repository === "https://github.com/yilibinbin/external-models-for-codex", "repository-url", manifest.repository)
  ];
  return checks;
}

function semanticFixtureSafe(parsed) {
  const providers = parsed.providers;
  if (!providers || typeof providers !== "object" || Array.isArray(providers)) {
    return false;
  }
  return Object.values(providers).every((provider) => {
    if (!Array.isArray(provider.command) || !provider.command.every((part) => typeof part === "string" && part)) {
      return false;
    }
    const env = provider.env ?? {};
    if (!env || typeof env !== "object" || Array.isArray(env)) {
      return false;
    }
    return Object.keys(env).every((key) => key === "PATH" || key === "LANG" || key === "LC_ALL" || /^SEMANTIC_PROVIDER_[A-Z0-9_]+$/.test(key));
  });
}

function checkSemanticFixtures(root) {
  const { pluginRoot } = resolveLayout(root);
  const fixtureDir = path.join(pluginRoot, "fixtures", "semantic");
  const safe = readJson(path.join(fixtureDir, "safe-provider.json"));
  const unsafe = readJson(path.join(fixtureDir, "unsafe-provider.json"));
  return [
    result(semanticFixtureSafe(safe), "semantic-fixture-safe"),
    result(!semanticFixtureSafe(unsafe), "semantic-fixture-unsafe")
  ];
}

function checkHooks(root) {
  const { pluginRoot } = resolveLayout(root);
  const hooksFile = path.join(pluginRoot, "hooks", "hooks.json");
  const parsed = readJson(hooksFile);
  const events = Object.keys(parsed.hooks ?? {}).sort();
  return [
    result(JSON.stringify(events) === JSON.stringify(["SessionEnd", "SessionStart", "Stop", "UserPromptSubmit"].sort()), "hook-events", events.join(",")),
    result(Object.values(parsed.hooks ?? {}).every((entries) => Array.isArray(entries)), "hook-shapes")
  ];
}

function checkDocs(root) {
  const { repoRoot, pluginRoot, installedPluginOnly } = resolveLayout(root);
  const docs = installedPluginOnly
    ? [{ base: pluginRoot, label: "README.md", file: "README.md" }]
    : [
        { base: repoRoot, label: "README.md", file: "README.md" },
        { base: repoRoot, label: "docs/README.en.md", file: "docs/README.en.md" },
        { base: repoRoot, label: "docs/README.zh-CN.md", file: "docs/README.zh-CN.md" },
        { base: pluginRoot, label: "plugins/claude-for-codex/README.md", file: "README.md" }
      ];
  const checks = [];
  for (const doc of docs) {
    const text = fs.readFileSync(path.join(doc.base, doc.file), "utf8");
    checks.push(result(text.includes("external-models-for-codex"), `docs-marketplace-${doc.label}`));
    checks.push(result(text.includes(DEFAULT_RELEASE_REF), `docs-immutable-ref-${doc.label}`));
    checks.push(result(!text.includes("external-models-for-codex-local"), `docs-no-old-marketplace-${doc.label}`));
    checks.push(result(!/\/Users\/fanghao/.test(text), `docs-no-local-path-${doc.label}`));
  }
  return checks;
}

function checkNativeReleaseAssets(root) {
  const { repoRoot, pluginRoot, installedPluginOnly } = resolveLayout(root);
  const companion = fs.readFileSync(path.join(pluginRoot, "scripts", "claude-companion.mjs"), "utf8");
  const backend = fs.readFileSync(path.join(pluginRoot, "scripts", "lib", "claude-backend.mjs"), "utf8");
  const qualityPolicy = fs.readFileSync(path.join(pluginRoot, "scripts", "lib", "quality-policy.mjs"), "utf8");
  const githubActions = fs.readFileSync(path.join(pluginRoot, "scripts", "lib", "github-actions.mjs"), "utf8");
  const nativeHelper = path.join(pluginRoot, "scripts", "lib", "claude-native-review.mjs");
  const ultrareviewSkill = path.join(pluginRoot, "skills", "claude-ultrareview", "SKILL.md");
  const hooks = fs.readFileSync(path.join(pluginRoot, "hooks", "hooks.json"), "utf8");
  const hookWrapper = fs.readFileSync(path.join(pluginRoot, "hooks", "claude-review-gate.mjs"), "utf8");
  const defaultWorkflow = renderWorkflow(pluginRoot);
  const docSpecs = installedPluginOnly
    ? [{ base: pluginRoot, file: "README.md" }]
    : [
        { base: repoRoot, file: "README.md" },
        { base: repoRoot, file: "docs/README.en.md" },
        { base: repoRoot, file: "docs/README.zh-CN.md" },
        { base: pluginRoot, file: "README.md" }
      ];
  const requiredDocMarkers = [
    "--agent-team sdk-subagents",
    "--backend sdk",
    "@anthropic-ai/claude-agent-sdk",
    "--native-structured",
    "--stream-progress",
    "--confirm-cost"
  ];
  const docs = docSpecs.map(({ base, file }) => fs.readFileSync(path.join(base, file), "utf8"));
  const docsJoined = docs.join("\n");
  const docsOk = docs.every((text) => requiredDocMarkers.every((marker) => text.includes(marker)));
  const nativeOptInDocsOk = docs.every((text) => text.includes("--backend sdk --agent-team sdk-subagents"));
  const defaultCliDocsOk = docsJoined.includes("CLI mode remains the default") && docsJoined.includes("CLI remains the default backend");
  const ultrareviewConsentDocsOk = docsJoined.includes("--confirm-cost") && docsJoined.includes("CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1");
  const ultrareviewNotDefaultDocsOk = docsJoined.includes("not used by hooks or default review paths") && docsJoined.includes("never used by hooks or default review paths");
  const detail = "claude-ultrareview; native assets/docs include --agent-team sdk-subagents, --confirm-cost, @anthropic-ai/claude-agent-sdk";
  return [
    result(fs.existsSync(nativeHelper), "native-review-helper", path.relative(pluginRoot, nativeHelper)),
    result(fs.existsSync(ultrareviewSkill), "ultrareview-skill", "claude-ultrareview"),
    result(
      companion.includes("--agent-team") &&
        companion.includes("sdk-subagents") &&
        companion.includes("--native-structured") &&
        companion.includes("--stream-progress") &&
        companion.includes("--confirm-cost"),
      "native-cli-flags",
      detail
    ),
    result(backend.includes("@anthropic-ai/claude-agent-sdk"), "native-sdk-package-compat", "@anthropic-ai/claude-agent-sdk"),
    result(docsOk, "native-docs", detail),
    result(
      companion.includes("args.agentTeam = args.agentTeam ?? \"plugin\"") &&
        companion.includes("--agent-team sdk-subagents requires --backend sdk or CLAUDE_FOR_CODEX_BACKEND=sdk.") &&
        companion.includes("if (args.agentTeam === \"sdk-subagents\" && args.backend !== \"sdk\")") &&
        nativeOptInDocsOk,
      "native-sdk-explicit-opt-in",
      "--backend sdk --agent-team sdk-subagents"
    ),
    result(
      backend.includes("const backend = args.backend || env[BACKEND_ENV] || \"cli\";") &&
        companion.includes("args.agentTeam = args.agentTeam ?? \"plugin\"") &&
        defaultWorkflow.includes("claude-companion.mjs review --json") &&
        !defaultWorkflow.includes("--backend sdk") &&
        !defaultWorkflow.includes("--agent-team sdk-subagents") &&
        defaultCliDocsOk,
      "native-default-cli-preserved",
      "default backend=cli; default agentTeam=plugin"
    ),
    result(
      companion.includes("let confirmed = process.env.CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW === \"1\";") &&
        companion.includes("if (arg === \"--confirm-cost\")") &&
        companion.includes("if (!confirmed)") &&
        companion.includes("pass --confirm-cost or set CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1") &&
        ultrareviewConsentDocsOk,
      "ultrareview-cost-consent",
      "--confirm-cost or CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1"
    ),
    result(
      !hooks.includes("ultrareview") &&
        hookWrapper.includes("[RUNTIME, \"review-gate\"]") &&
        defaultWorkflow.includes("claude-companion.mjs review --json") &&
        !defaultWorkflow.includes("ultrareview") &&
        ultrareviewNotDefaultDocsOk,
      "ultrareview-not-hook-default",
      "hooks call review-gate; generated workflow calls review"
    ),
    result(
      fs.existsSync(path.join(pluginRoot, "scripts", "lib", "quality-policy.mjs")) &&
        sourceArrayIncludes(qualityPolicy, "VALID_QUALITIES", ["auto", "fast", "standard", "strong", "max"]) &&
        sourceArrayIncludes(qualityPolicy, "VALID_EFFORTS", ["low", "medium", "high", "xhigh", "max"]) &&
        companion.includes("--quality auto|fast|standard|strong|max"),
      "quality-policy-assets",
      "--quality auto|fast|standard|strong|max"
    ),
    result(
      !qualityPolicy.includes("ultracode") &&
        !companion.includes("--effort\", \"ultracode") &&
        !defaultWorkflow.includes("ultracode") &&
        !hooks.includes("ultracode"),
      "quality-no-ultracode-effort",
      "ultracode is not a CLI effort value"
    ),
    result(
      !hooks.includes("--quality strong") &&
        !hooks.includes("--quality max") &&
        !hooks.includes("--backend sdk") &&
        !hooks.includes("ultrareview") &&
        !hookWrapper.includes("--quality strong") &&
        !hookWrapper.includes("--quality max") &&
        !hookWrapper.includes("ultrareview"),
      "quality-hook-conservative",
      "installed hooks do not force expensive quality paths"
    ),
    result(
      workflowCommandPinsStandardQuality(defaultWorkflow) &&
        githubActionsDefaultsToStandardQuality(githubActions) &&
        !defaultWorkflow.includes("--quality max") &&
        !defaultWorkflow.includes("ultrareview"),
      "quality-ci-default-standard",
      "default GitHub Actions workflow pins --quality standard"
    ),
    result(
      !/claude-(opus|sonnet|haiku)-\d/i.test(qualityPolicy) &&
        sourceHasAliasProfile(qualityPolicy, "opus", "xhigh") &&
        sourceHasAliasProfile(qualityPolicy, "sonnet", "high"),
      "quality-no-concrete-model-defaults",
      "policy uses Claude Code aliases"
    )
  ];
}

export function readOnlyIsolationChecksFromSource({ companion = "", backend = "" } = {}) {
  const combined = `${companion}\n${backend}`;
  const cliMarkers = [
    "--disable-slash-commands",
    "--no-session-persistence",
    "--setting-sources",
    "READ_ONLY_BUILTIN_TOOLS.join(\",\")",
    "READ_ONLY_MCP_TOOLS.join(\",\")",
    "--strict-mcp-config",
    "configuredWriteDenyTools(process.env)",
    "formatDenyToolsForCli(denyTools)",
    "parseUnknownDenyToolFailure"
  ];
  const sdkMarkers = [
    "settingSources: []",
    "skills: []",
    "hooks: {}",
    "plugins: []",
    "persistSession: false",
    "CLAUDE_FOR_CODEX_ISOLATED_REVIEW",
    "structuredOutput = event.structured_output"
  ];
  return [
    result(
      cliMarkers.every((marker) => companion.includes(marker)),
      "read-only-cli-isolation",
      "disable slash commands/session persistence/settings; read allow-list; write deny-list; strict MCP"
    ),
    result(
      sdkMarkers.every((marker) => backend.includes(marker)),
      "read-only-sdk-isolation",
      "settingSources=[], skills=[], hooks={}, plugins=[], persistSession=false"
    ),
    result(
      !/CLAUDE_CONFIG_DIR\s*[:=]/.test(combined),
      "read-only-no-config-dir-relocation",
      "CLAUDE_CONFIG_DIR is not assigned in read-only companion/backend code"
    ),
    result(
      backend.includes("metadata.structuredOutput") && companion.includes("aggregate.metadata?.structuredOutput"),
      "read-only-sdk-structured-output",
      "SDK structured_output is captured as metadata and consumed by native subagent aggregation"
    ),
    result(
      companion.includes("structuredReview") &&
        companion.includes("role_results[].result.review") &&
        companion.includes("result.metadata?.structuredReview"),
      "sdk-native-structured-review-contract",
      "SDK native structured multi-review consumes nested role review objects"
    )
  ];
}

function checkReadOnlyIsolation(root) {
  const { pluginRoot } = resolveLayout(root);
  const companion = fs.readFileSync(path.join(pluginRoot, "scripts", "claude-companion.mjs"), "utf8");
  const backend = fs.readFileSync(path.join(pluginRoot, "scripts", "lib", "claude-backend.mjs"), "utf8");
  const mcpGit = fs.readFileSync(path.join(pluginRoot, "scripts", "lib", "mcp-git.mjs"), "utf8");
  return [
    ...readOnlyIsolationChecksFromSource({ companion, backend }),
    result(
      mcpGit.includes("GIT_TIMEOUT_MS") &&
        mcpGit.includes("timeout: GIT_TIMEOUT_MS") &&
        mcpGit.includes('killSignal: "SIGKILL"'),
      "read-only-git-mcp-timeout",
      "Git MCP subprocess calls have a bounded timeout"
    )
  ];
}

function checkSecrets(root) {
  const { repoRoot, pluginRoot, installedPluginOnly } = resolveLayout(root);
  const scanRoot = installedPluginOnly ? pluginRoot : repoRoot;
  const scanDirs = installedPluginOnly
    ? ["README.md", "CHANGELOG.md", "scripts", "hooks", "skills", "prompts", "schemas", "templates", "fixtures"]
    : ["README.md", "docs", "plugins/claude-for-codex"];
  const checks = [];
  for (const file of listFiles(scanRoot, scanDirs)) {
    if (file.includes(`${path.sep}docs${path.sep}superpowers${path.sep}`)) {
      continue;
    }
    const text = fs.readFileSync(file, "utf8");
    const relative = path.relative(scanRoot, file);
    if (text.includes("release-check allowlist")) {
      continue;
    }
    for (const { name, pattern } of SECRET_PATTERNS) {
      pattern.lastIndex = 0;
      if (pattern.test(text)) {
        checks.push(result(false, `secret-scan-${name}`, relative));
      }
    }
    for (const line of text.split(/\r?\n/)) {
      if (SECRET_ASSIGNMENT_PATTERN.test(line)) {
        checks.push(result(false, "secret-scan-api-key-assignment", relative));
        break;
      }
    }
  }
  return checks.length ? checks : [result(true, "secret-scan")];
}

function checkSkills(root) {
  const { pluginRoot } = resolveLayout(root);
  const skillsDir = path.join(pluginRoot, "skills");
  const skills = fs.readdirSync(skillsDir).filter((name) => fs.existsSync(path.join(skillsDir, name, "SKILL.md"))).sort();
  const expected = [...EXPECTED_SKILLS].sort();
  return [
    result(JSON.stringify(skills) === JSON.stringify(expected), "skill-inventory", `actual=${skills.join(",")}`),
    ...skills.map((skill) => {
      const text = fs.readFileSync(path.join(skillsDir, skill, "SKILL.md"), "utf8");
      return result(text.startsWith("---") && text.includes("claude-companion.mjs"), `skill-${skill}`);
    })
  ];
}

function checkSubagentReviewDocs(root) {
  const { pluginRoot } = resolveLayout(root);
  const skill = fs.readFileSync(path.join(pluginRoot, "skills", "claude-subagent-review", "SKILL.md"), "utf8");
  const readme = fs.readFileSync(path.join(pluginRoot, "README.md"), "utf8");
  const subagentSection = markdownSection(readme, "Codex subagent delegation");
  const skillMarkers = [
    "subagent-command",
    "workerCommand",
    "must not replace it with raw claude",
    "claude -p",
    "reserve-job"
  ];
  const readmeMarkers = [
    "Codex subagent delegation",
    "subagent-command",
    'node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command',
    'node "${CODEX_PLUGIN_ROOT}/scripts/claude-companion.mjs" subagent-command rescue "$ARGUMENTS"'
  ];
  return [
    result(
      skillMarkers.every((marker) => skill.includes(marker)) &&
        readmeMarkers.every((marker) => readme.includes(marker)) &&
        !subagentSection.includes("node plugins/claude-for-codex/scripts/claude-companion.mjs"),
      "subagent-review-docs",
      "claude-subagent-review and README document safe subagent-command delegation"
    )
  ];
}

function checkGithubActionsCi(root) {
  const { pluginRoot } = resolveLayout(root);
  const defaultWorkflow = renderWorkflow(pluginRoot);
  const annotationWorkflow = renderWorkflow(pluginRoot, { annotations: true });
  const validation = validateWorkflow(defaultWorkflow);
  const annotationValidation = validateWorkflow(annotationWorkflow, { annotations: true });
  const checks = [
    result(validation.ok && annotationValidation.ok, "github-actions-template-safe"),
    result(validation.checks.some((check) => check.name === "github-actions-fork-safe" && check.ok), "github-actions-fork-safe"),
    result(validation.checks.some((check) => check.name === "immutable-release-ref" && check.ok), "github-actions-immutable-ref"),
    result(defaultWorkflow.includes(`--ref ${DEFAULT_RELEASE_REF}`), "github-actions-current-release-ref", DEFAULT_RELEASE_REF),
    result(defaultWorkflow.includes("ubuntu-latest") && defaultWorkflow.includes("npm install -g @openai/codex"), "github-actions-runner-sim"),
    result(!/\/Users\/fanghao/.test(defaultWorkflow), "github-actions-no-local-paths"),
    result(!defaultWorkflow.includes("pull_request_target"), "github-actions-no-pull-request-target"),
    result(defaultWorkflow.includes("BASE_SHA: ${{ github.event.pull_request.base.sha }}"), "github-actions-base-sha-env"),
    result(defaultWorkflow.includes("retention-days: 5"), "github-actions-short-artifact-retention")
  ];
  return checks;
}

function checkPrompts(root) {
  const { pluginRoot } = resolveLayout(root);
  const promptDir = path.join(pluginRoot, "prompts");
  const prompts = fs.readdirSync(promptDir).filter((name) => name.endsWith(".md"));
  return prompts.map((prompt) => {
    const text = fs.readFileSync(path.join(promptDir, prompt), "utf8");
    return result(text.includes("<task>") && text.includes("{{"), `prompt-${prompt}`);
  });
}

function checkRolePacks() {
  const validation = validateBuiltInRolePacks();
  return [
    result(validation.ok, "role-packs-valid", validation.failures.join("; "))
  ];
}

function checkMailboxLeaseSupport(root) {
  const { pluginRoot } = resolveLayout(root);
  const sanitized = sanitizeSummary(`ghp_${"A".repeat(24)} /home/alice/project /tmp/work C:\\Users\\alice\\file`, { cwd: pluginRoot });
  return [
    result(fs.existsSync(path.join(pluginRoot, "scripts", "lib", "sanitize.mjs")), "sanitize-module"),
    result(fs.existsSync(path.join(pluginRoot, "scripts", "lib", "mailbox.mjs")), "mailbox-module"),
    result(fs.existsSync(path.join(pluginRoot, "scripts", "lib", "leases.mjs")), "leases-module"),
    result(!sanitized.includes("ghp_") && !sanitized.includes("/home/alice") && !sanitized.includes("/tmp/work") && !sanitized.includes("C:\\Users"), "mailbox-sanitizer-fixture")
  ];
}

function remoteInstallSmoke(root, options) {
  if (!options.remoteInstall) {
    return [result(true, "remote-install-smoke", "skipped")];
  }
  if (!commandExists("codex")) {
    return [result(!options.requireRemoteInstall, "remote-install-smoke", "codex unavailable")];
  }
  const timeout = options.timeoutMs ?? 30000;
  const tmp = fs.mkdtempSync(path.join(fs.realpathSync("/tmp"), "cfc-release-check-"));
  const env = {
    ...process.env,
    HOME: tmp,
    CODEX_HOME: path.join(tmp, ".codex")
  };
  fs.mkdirSync(env.CODEX_HOME, { recursive: true, mode: 0o700 });
  const releaseRef = options.releaseRef ?? DEFAULT_RELEASE_REF;
  const add = spawnSync("codex", ["plugin", "marketplace", "add", "yilibinbin/external-models-for-codex", "--ref", releaseRef], {
    env,
    encoding: "utf8",
    timeout
  });
  if (add.status !== 0) {
    return [result(!options.requireRemoteInstall, "remote-install-smoke", `skipped: ${add.stderr || add.error || "marketplace add failed"}`)];
  }
  const install = spawnSync("codex", ["plugin", "add", "claude-for-codex@external-models-for-codex"], {
    env,
    encoding: "utf8",
    timeout
  });
  return [result(install.status === 0 || !options.requireRemoteInstall, "remote-install-smoke", install.status === 0 ? `installed ref=${releaseRef}` : `skipped: ${install.stderr || install.error || "install failed"}`)];
}

export function runReleaseCheck(root, options = {}) {
  const checks = [
    ...checkManifest(root),
    ...checkHooks(root),
    ...checkDocs(root),
    ...checkNativeReleaseAssets(root),
    ...checkReadOnlyIsolation(root),
    ...checkSecrets(root),
    ...checkSkills(root),
    ...checkSubagentReviewDocs(root),
    ...checkRolePacks(),
    ...checkMailboxLeaseSupport(root),
    ...checkPrompts(root),
    ...checkSemanticFixtures(root),
    ...(options.ciSimulate ? checkGithubActionsCi(root) : []),
    ...remoteInstallSmoke(root, options)
  ];
  return {
    ok: checks.every((check) => check.ok),
    checks
  };
}
