import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { renderWorkflow, validateWorkflow } from "./github-actions.mjs";
import { installedEntry as installedClaudePluginEntry } from "./install-consistency.mjs";
import {
  nativeAgentProfiles,
  renderNativeAgentMarkdown,
  sdkAgentsFromNativeProfiles,
  validateNativeAgentMarkdown
} from "./native-agent-profiles.mjs";
import { validateClaudeCodePluginPack } from "./claude-plugin-pack.mjs";
import { validateBuiltInRolePacks } from "./role-packs.mjs";
import { SECRET_PATTERNS, sanitizeSummary } from "./sanitize.mjs";

const SECRET_ASSIGNMENT_PATTERN = /\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*["']([A-Za-z0-9_./+=:-]{16,})["']/i;
const DEFAULT_RELEASE_REF = "claude-for-codex-v0.20.0";
const EXPECTED_SKILLS = [
  "claude-adversarial-review",
  "claude-assisted-review",
  "claude-cancel",
  "claude-collaboration-loop",
  "claude-github-actions-review",
  "claude-leases",
  "claude-mailbox",
  "claude-multi-review",
  "claude-plan",
  "claude-plan-review",
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
  return /claude-companion\.mjs"?\s+review\b/.test(normalized)
    && /--json\b/.test(normalized)
    && /--quality\s+standard\b/.test(normalized)
    && /--scope\s+branch\b/.test(normalized);
}

function githubActionsDefaultsToStandardQuality(text) {
  return /quality\s*:\s*["']standard["']/.test(text)
    || /const\s+quality\s*=\s*options\.quality\s*\?\?\s*["']standard["']/.test(text);
}

function manifestAssetChecks(pluginRoot) {
  const manifest = readJson(path.join(pluginRoot, ".codex-plugin", "plugin.json"));
  const iface = manifest.interface ?? {};
  const defaultPromptCount = Array.isArray(iface.defaultPrompt) ? iface.defaultPrompt.length : 0;
  const assetSpecs = [
    ["composerIcon", iface.composerIcon],
    ["logo", iface.logo],
    ...((iface.screenshots ?? []).map((value, index) => [`screenshots.${index}`, value]))
  ];
  return [
    result(defaultPromptCount <= 3, "manifest-defaultPrompt-limit", `count=${defaultPromptCount}`),
    ...assetSpecs.map(([label, value]) => {
      const relativePath = String(value ?? "");
      const safeRelative = relativePath && !path.isAbsolute(relativePath) && !relativePath.split(/[\\/]/).includes("..");
      const codexSchemaPath = relativePath.startsWith("./");
      return result(Boolean(codexSchemaPath && safeRelative && fs.existsSync(path.join(pluginRoot, relativePath))), `manifest-asset-${label}`, relativePath);
    })
  ];
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

function validateCodexInstalledClaudePlugin(listStdout) {
  let parsed;
  try {
    parsed = JSON.parse(listStdout);
  } catch (error) {
    return { ok: false, detail: `plugin list JSON parse failed: ${error.message}` };
  }
  const plugin = installedClaudePluginEntry(parsed);
  if (!plugin) {
    return { ok: false, detail: "claude-for-codex@external-models-for-codex missing from codex plugin list" };
  }
  const pluginRoot = plugin.source?.path;
  if (typeof pluginRoot !== "string" || !pluginRoot) {
    return { ok: false, detail: "installed Claude plugin missing source.path" };
  }
  const runtime = path.join(pluginRoot, "scripts", "claude-companion.mjs");
  if (!fs.existsSync(runtime)) {
    return { ok: false, detail: `installed Claude runtime missing: ${runtime}` };
  }
  return { ok: true, detail: `installed root=${pluginRoot}` };
}

function checkManifest(root) {
  const { pluginRoot } = resolveLayout(root);
  const manifest = readJson(path.join(pluginRoot, ".codex-plugin", "plugin.json"));
  const changelog = fs.readFileSync(path.join(pluginRoot, "CHANGELOG.md"), "utf8");
  const unreleasedBody = markdownSection(changelog, "Unreleased").trim();
  const checks = [
    result(manifest.version === "0.20.0", "manifest-version", `version=${manifest.version}`),
    result(changelog.includes("## 0.20.0"), "changelog-version", "CHANGELOG contains 0.20.0"),
    result(fs.readFileSync(path.join(pluginRoot, "README.md"), "utf8").includes("Current version: `0.20.0`"), "readme-current-version", "README current version is 0.20.0"),
    result(unreleasedBody.length === 0, "changelog-unreleased-empty", unreleasedBody ? "Unreleased contains entries" : ""),
    result(!Object.prototype.hasOwnProperty.call(manifest, "hooks"), "manifest-no-hooks-field"),
    result(manifest.repository === "https://github.com/yilibinbin/external-models-for-codex", "repository-url", manifest.repository)
  ];
  return checks;
}

function checkVersionSurfacesCurrent(root) {
  const { pluginRoot, repoRoot, installedPluginOnly = false } = resolveLayout(root);
  let codexManifest;
  let claudeManifest;
  try {
    codexManifest = readJson(path.join(pluginRoot, ".codex-plugin", "plugin.json"));
    claudeManifest = readJson(path.join(pluginRoot, "claude-code-plugin", ".claude-plugin", "plugin.json"));
  } catch (error) {
    return [result(false, "version-surfaces-current", `manifest read failed: ${error.message || String(error)}`)];
  }
  const version = codexManifest.version;
  const expectedVersion = "0.20.0";
  const releaseRef = `claude-for-codex-v${version}`;
  const pluginReadme = fs.readFileSync(path.join(pluginRoot, "README.md"), "utf8");
  const releaseRefFiles = [
    path.join(pluginRoot, "scripts", "lib", "release-check.mjs"),
    path.join(pluginRoot, "scripts", "lib", "github-actions.mjs"),
    path.join(pluginRoot, "README.md")
  ];
  if (!installedPluginOnly && repoRoot) {
    releaseRefFiles.push(
      path.join(repoRoot, "README.md"),
      path.join(repoRoot, "docs", "README.en.md"),
      path.join(repoRoot, "docs", "README.zh-CN.md")
    );
  }
  const missingReleaseRefFiles = releaseRefFiles.filter((file) => !fs.existsSync(file));
  const releaseRefTexts = releaseRefFiles
    .filter((file) => fs.existsSync(file))
    .map((file) => fs.readFileSync(file, "utf8"));
  const changelog = fs.readFileSync(path.join(pluginRoot, "CHANGELOG.md"), "utf8");
  const changelogHeading = new RegExp(`## ${version} - \\d{4}-\\d{2}-\\d{2}`);
  const ok = version === expectedVersion
    && claudeManifest.version === version
    && missingReleaseRefFiles.length === 0
    && releaseRefTexts.every((text) => text.includes(releaseRef))
    && pluginReadme.includes(`Current version: \`${version}\``)
    && changelogHeading.test(changelog);
  const layout = installedPluginOnly ? "plugin-only" : "repo";
  const mismatchDetail = claudeManifest.version === version ? "" : `; claudePluginVersion=${claudeManifest.version}`;
  const detail = missingReleaseRefFiles.length
    ? `releaseRef=${releaseRef}; layout=${layout}${mismatchDetail}; missing=${missingReleaseRefFiles.map((file) => path.relative(pluginRoot, file)).join(",")}`
    : `releaseRef=${releaseRef}; layout=${layout}${mismatchDetail}`;
  return [result(ok, "version-surfaces-current", detail)];
}

function filesForStaleReleaseScan(root) {
  const { pluginRoot, repoRoot, installedPluginOnly = false } = resolveLayout(root);
  const ignore = (file) => /(^|\/)(CHANGELOG\.md|tests\/|docs\/superpowers\/)/.test(file.split(path.sep).join("/"));
  if (!installedPluginOnly && repoRoot) {
    const listed = spawnSync("git", ["ls-files"], { cwd: repoRoot, encoding: "utf8", maxBuffer: 1024 * 1024 });
    if (listed.status === 0) {
      return listed.stdout
        .split(/\r?\n/)
        .filter(Boolean)
        .map((file) => path.join(repoRoot, file))
        .filter((file) => fs.existsSync(file) && fs.statSync(file).isFile() && !ignore(path.relative(repoRoot, file)));
    }
  }
  return listFiles(pluginRoot, ["."])
    .filter((file) => fs.existsSync(file) && fs.statSync(file).isFile() && !ignore(path.relative(pluginRoot, file)));
}

function checkNoStaleReleaseRef(root) {
  const { pluginRoot } = resolveLayout(root);
  const currentVersion = readJson(path.join(pluginRoot, ".codex-plugin", "plugin.json")).version;
  const currentParts = String(currentVersion).split(".").map((part) => Number.parseInt(part, 10));
  const releasePatterns = [
    /claude-for-codex-v(\d+\.\d+\.\d+)/g,
    /Claude for Codex (\d+\.\d+\.\d+)/g,
    /Current version:\s*`?(\d+\.\d+\.\d+)/g
  ];
  const compareVersionParts = (left, right) => {
    for (let index = 0; index < 3; index += 1) {
      const difference = left[index] - right[index];
      if (difference !== 0) return difference;
    }
    return 0;
  };
  const isNonCurrentReleaseVersion = (version) => {
    const parts = String(version).split(".").map((part) => Number.parseInt(part, 10));
    if (parts.length !== 3 || !parts.every(Number.isInteger) || currentParts.length !== 3 || !currentParts.every(Number.isInteger)) {
      return false;
    }
    return compareVersionParts(parts, currentParts) !== 0;
  };
  const matches = [];
  for (const file of filesForStaleReleaseScan(root)) {
    const text = fs.readFileSync(file, "utf8");
    for (const pattern of releasePatterns) {
      pattern.lastIndex = 0;
      for (const match of text.matchAll(pattern)) {
        if (isNonCurrentReleaseVersion(match[1])) {
          matches.push(`${path.relative(pluginRoot, file)}:${match[0]}`);
        }
      }
    }
  }
  return [result(matches.length === 0, "no-stale-release-ref", matches.length ? `stale refs in ${matches.join(",")}` : "no stale release refs outside changelog/history fixtures")];
}

function checkManifestMetadataCurrent(root) {
  const { pluginRoot } = resolveLayout(root);
  const manifest = readJson(path.join(pluginRoot, ".codex-plugin", "plugin.json"));
  const iface = manifest.interface ?? {};
  const capabilities = Array.isArray(iface.capabilities) ? iface.capabilities : [];
  const prompts = Array.isArray(iface.defaultPrompt) ? iface.defaultPrompt : [];
  const requiredCapabilities = [
    "Optional scorecard review JSON",
    "Taskset planning state",
    "Validation evidence ingestion",
    "Bounded assisted review",
    "Saved implementation plan review",
    "Opt-in Claude Code native reviewer plugin pack",
    "Renderer-validated Claude Code Markdown agents"
  ];
  const ok = String(manifest.description ?? "").includes("scorecard review")
    && String(manifest.description ?? "").includes("taskset planning")
    && String(manifest.description ?? "").includes("bounded assisted review")
    && String(manifest.description ?? "").includes("saved-plan review")
    && String(manifest.description ?? "").includes("opt-in Claude Code native review agents")
    && String(iface.longDescription ?? "").includes("first-class plan-review command")
    && String(iface.longDescription ?? "").includes("scorecard-based quality review")
    && String(iface.longDescription ?? "").includes("bounded assisted review loops")
    && String(iface.longDescription ?? "").includes("opt-in Claude Code plugin pack")
    && requiredCapabilities.every((capability) => capabilities.includes(capability))
    && prompts.some((prompt) => String(prompt).includes("saved implementation plan"));
  return [result(ok, "manifest-metadata-current", ok ? "marketplace metadata mentions scorecard/taskset/assisted-review, plan-review, and native plugin pack" : "manifest metadata missing scorecard/taskset/assisted-review/plan-review/native plugin pack copy")];
}

function checkClaudeNativeUniversality(root) {
  const { pluginRoot } = resolveLayout(root);
  const profiles = nativeAgentProfiles();
  const agents = sdkAgentsFromNativeProfiles(profiles, { model: "opus", effort: "high" });
  const markdownValid = profiles.every((profile) =>
    validateNativeAgentMarkdown(renderNativeAgentMarkdown(profile), `${profile.markdownAgentName.replaceAll("_", "-")}.md`).ok
  );
  const pack = validateClaudeCodePluginPack(pluginRoot);
  const companion = fs.readFileSync(path.join(pluginRoot, "scripts", "claude-companion.mjs"), "utf8");
  return [
    result(profiles.length === 5 && Object.keys(agents).length === 5 && markdownValid, "native-agent-profiles", `profiles=${profiles.length}`),
    result(pack.ok, "claude-code-plugin-pack", pack.ok ? "agent files match renderNativeAgentMarkdown output" : pack.errors.join("; ")),
    result(!fs.existsSync(path.join(pluginRoot, ".claude-plugin", "plugin.json")), "no-root-claude-plugin-manifest"),
    result(companion.includes('"plan-review"'), "plan-review-command", companion.includes('"plan-review"') ? "registered" : "missing"),
    ...checkManifestMetadataCurrent(root)
  ];
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
  const outcomeClassifier = fs.readFileSync(path.join(pluginRoot, "scripts", "lib", "outcome-classifier.mjs"), "utf8");
  const modelRegistryPath = path.join(pluginRoot, "scripts", "lib", "model-registry.mjs");
  const modelRegistry = fs.existsSync(modelRegistryPath) ? fs.readFileSync(modelRegistryPath, "utf8") : "";
  const githubActions = fs.readFileSync(path.join(pluginRoot, "scripts", "lib", "github-actions.mjs"), "utf8");
  const nativeHelper = path.join(pluginRoot, "scripts", "lib", "claude-native-review.mjs");
  const nativeProfiles = path.join(pluginRoot, "scripts", "lib", "native-agent-profiles.mjs");
  const nativeReview = [
    fs.existsSync(nativeHelper) ? fs.readFileSync(nativeHelper, "utf8") : "",
    fs.existsSync(nativeProfiles) ? fs.readFileSync(nativeProfiles, "utf8") : ""
  ].join("\n");
  const hookCompat = path.join(pluginRoot, "scripts", "lib", "hook-compat.mjs");
  const doctor = path.join(pluginRoot, "scripts", "lib", "doctor.mjs");
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
  const defaultCliDocsOk = docsJoined.includes("CLI mode remains the default backend");
  const ultrareviewConsentDocsOk = docsJoined.includes("--confirm-cost") && docsJoined.includes("CLAUDE_FOR_CODEX_ALLOW_ULTRAREVIEW=1");
  const ultrareviewNotDefaultDocsOk = docsJoined.includes("never used by hooks or default review paths");
  const v018DocMarkers = [
    "model alias registry",
    "outcome classification",
    "doctor --json",
    "fork-safe CI dogfood",
    "fresh isolated context"
  ];
  const v018DocsOk = docs.every((text) => v018DocMarkers.every((marker) => text.includes(marker)));
  const detail = "claude-ultrareview; native assets/docs include --agent-team sdk-subagents, --confirm-cost, @anthropic-ai/claude-agent-sdk";
  return [
    ...manifestAssetChecks(pluginRoot),
    result(fs.existsSync(nativeHelper), "native-review-helper", path.relative(pluginRoot, nativeHelper)),
    result(
      fs.existsSync(hookCompat) &&
        companion.includes("hookCompatibilityReport") &&
        fs.readFileSync(hookCompat, "utf8").includes("CODEX_DISCOVERED_HOOK_EVENTS"),
      "hook-compat-report",
      "hook compatibility report preserves Codex-discovered hook subset"
    ),
    result(
      fs.existsSync(doctor) &&
        companion.includes('"doctor"') &&
        companion.includes("doctorReport") &&
        fs.readFileSync(doctor, "utf8").includes("semanticCapabilities"),
      "doctor-command",
      "doctor --json reports cheap diagnostics without Claude prompt execution"
    ),
    result(fs.existsSync(path.join(pluginRoot, "scripts", "lib", "install-consistency.mjs")), "install-consistency-module"),
    result(fs.existsSync(path.join(pluginRoot, "scripts", "lib", "project-instructions.mjs")), "project-instructions-module"),
    result(fs.existsSync(path.join(pluginRoot, "schemas", "scorecard-output.schema.json")), "scorecard-schema"),
    result(fs.existsSync(path.join(pluginRoot, "schemas", "taskset.schema.json")), "taskset-schema"),
    result(
      companion.includes('"assisted-review"') &&
        fs.existsSync(path.join(pluginRoot, "skills", "claude-assisted-review", "SKILL.md")),
      "assisted-review-command",
      "assisted-review command and skill are present"
    ),
    result(
      !hooks.includes("assisted-review") &&
        !hookWrapper.includes("assisted-review") &&
        !hooks.includes("ultrareview") &&
        !hookWrapper.includes("ultrareview"),
      "review-gate-no-assisted-or-ultrareview",
      "Stop hook remains conservative"
    ),
    result(qualityPolicy.includes("explanation:"), "quality-policy-explanation"),
    result(
      ["review.md", "multi-review-role.md", "adversarial-review.md", "plan.md", "plan-review-role.md", "rescue.md"].every((prompt) =>
        fs.readFileSync(path.join(pluginRoot, "prompts", prompt), "utf8").includes("PROJECT_INSTRUCTIONS_BLOCK")
      ),
      "project-instructions-prompts"
    ),
    result(fs.existsSync(ultrareviewSkill), "ultrareview-skill", "claude-ultrareview"),
    result(
      docsJoined.includes("review --scorecard --json") &&
        docsJoined.includes("plan --taskset") &&
        docsJoined.includes("assisted-review --scorecard") &&
        docsJoined.toLowerCase().includes("validation logs are user-provided") &&
        docsJoined.toLowerCase().includes("scorecards are advisory"),
      "autoresearch-quality-loop-docs",
      "docs mention scorecards, tasksets, validation evidence, and assisted-review boundaries"
    ),
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
    result(v018DocsOk, "native-maturity-docs", "docs include model alias registry, outcome classification, doctor, SDK isolation, and CI dogfood"),
    result(
      outcomeClassifier.includes("unknownOnly") &&
        outcomeClassifier.includes("const failureText = status === 0") &&
        outcomeClassifier.includes("`${result.stderr ?? \"\"}\\n${result.error ?? \"\"}`"),
      "outcome-success-stdout-not-failure",
      "successful model stdout is not scanned with broad auth/rate-limit/timeout failure classifiers"
    ),
    result(
      modelRegistry.includes("safeModelAliasOrId(env.CLAUDE_FOR_CODEX_TOP_MODEL)") &&
        modelRegistry.includes('requested === "default"') &&
        nativeReview.includes('normalized.family === "custom"') &&
        nativeReview.includes('normalized.family === "default"'),
      "model-registry-default-and-env-fallback",
      "default is a selectable CLI alias, invalid top-model env falls back, and SDK child agents inherit for custom/default models"
    ),
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
        workflowCommandPinsStandardQuality(defaultWorkflow) &&
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
        workflowCommandPinsStandardQuality(defaultWorkflow) &&
        !defaultWorkflow.includes("ultrareview") &&
        ultrareviewNotDefaultDocsOk,
      "ultrareview-not-hook-default",
      "hooks call review-gate; generated workflow calls review"
    ),
    result(
      fs.existsSync(path.join(pluginRoot, "scripts", "lib", "quality-policy.mjs")) &&
        sourceArrayIncludes(qualityPolicy, "VALID_QUALITIES", ["auto", "fast", "standard", "strong", "max"]) &&
        sourceArrayIncludes(qualityPolicy, "VALID_EFFORTS", ["low", "medium", "high", "xhigh", "max"]) &&
        modelRegistry.includes("MODEL_ALIAS_REGISTRY") &&
        ["best", "fable", "opus", "sonnet", "haiku", "opusplan", "inherit"].every((alias) => modelRegistry.includes(`alias: "${alias}"`)) &&
        modelRegistry.includes("supportsOneMillionSuffix: true") &&
        qualityPolicy.includes("VALID_MODEL_ALIASES = MODEL_ALIASES") &&
        companion.includes("--quality auto|fast|standard|strong|max"),
      "quality-policy-assets",
      "--quality auto|fast|standard|strong|max"
    ),
    result(
      qualityPolicy.includes("resolveTopModel") &&
        modelRegistry.includes("resolveTopModelFromCapabilities") &&
        qualityPolicy.includes("DEFAULT_TOP_MODEL_FALLBACK") &&
        qualityPolicy.includes('model: "top"') &&
        qualityPolicy.includes("topModelProfile") &&
        qualityPolicy.includes("topModelSelected") &&
        companion.includes("modelAliasAdvertised") &&
        companion.includes("modelAliasCapabilities") &&
        companion.includes("fallbackModelCapabilities") &&
        companion.includes("fallbackModel") &&
        companion.includes("fallbackModelList") &&
        nativeReview.includes("normalizeModelSelection") &&
        nativeReview.includes('model || "inherit"'),
      "quality-top-model-policy",
      "max quality is capability-aware and SDK subagents preserve safe Claude model ids"
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
        !/claude-fable-\d/i.test(qualityPolicy) &&
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
    ),
    result(
      backend.includes("withSdkTimeout") &&
        backend.includes("AbortController") &&
        backend.includes("abortController.abort()") &&
        backend.includes("claudeForCodexDeadline") &&
        backend.includes("timeoutDeadline") &&
        backend.includes("cleanupActiveMcpConfigs"),
      "sdk-timeout-abort",
      "SDK backend enforces bounded timeout with AbortSignal and timeout outcome metadata"
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

function longRunningLifecycleChecks(pluginRoot) {
  const companion = fs.readFileSync(path.join(pluginRoot, "scripts", "claude-companion.mjs"), "utf8");
  const jobs = fs.readFileSync(path.join(pluginRoot, "scripts", "lib", "jobs.mjs"), "utf8");
  const backend = fs.readFileSync(path.join(pluginRoot, "scripts", "lib", "claude-backend.mjs"), "utf8");
  const processText = fs.readFileSync(path.join(pluginRoot, "scripts", "lib", "process.mjs"), "utf8");
  const fingerprint = fs.readFileSync(path.join(pluginRoot, "scripts", "lib", "worktree-fingerprint.mjs"), "utf8");
  const gate = fs.readFileSync(path.join(pluginRoot, "hooks", "claude-review-gate.mjs"), "utf8");
  const unread = fs.readFileSync(path.join(pluginRoot, "hooks", "unread-result.mjs"), "utf8");
  const lifecyclePath = path.join(pluginRoot, "scripts", "lib", "job-lifecycle.mjs");
  const lifecycle = fs.existsSync(lifecyclePath) ? fs.readFileSync(lifecyclePath, "utf8") : "";
  const progressPath = path.join(pluginRoot, "scripts", "lib", "progress.mjs");
  const workerSignalHandlerIndex = companion.indexOf('process.once("SIGTERM", signalHandler)');
  const workerSpawnIndex = companion.indexOf("child = spawn(process.execPath");
  const cancelChildTerminationIndex = jobs.indexOf("let childTermination = Number.isInteger(requestedChildGroupPid)");
  const cancelChildDeliveredIndex = jobs.indexOf("childTermination.ok && childTermination.delivered");
  const cancelWorkerTerminationIndex = jobs.indexOf("let workerTermination = Number.isInteger(requested.workerPid)");
  // These source guards intentionally complement behavior tests. They pin
  // security/lifecycle invariants that are easy to preserve accidentally in
  // tests while losing in a refactor, such as ordering of signal handling and
  // exact fail-open hook boundaries.
  return [
    result(fs.existsSync(lifecyclePath), "job-lifecycle-helper", "scripts/lib/job-lifecycle.mjs exists"),
    result(jobs.includes("withJobLock") && jobs.includes("claimQueuedJob") && jobs.includes("claimReservedJob"), "atomic-job-claim-lock", "direct and reserved claims share locked path"),
    result(/async function runJobWorker/.test(companion) && !/runJobWorker[\s\S]{0,900}spawnSync/.test(companion), "async-background-worker", "__run-job uses async supervision"),
    result(companion.includes("async function runJobWorker") && companion.includes('status: "finish_failed"') && companion.includes("Background job finished but final state could not be persisted."), "direct-worker-finish-failed", "direct async workers surface finishJob lock/null failures instead of silently losing computed results"),
    result(
      companion.includes("makeCappedOutputAccumulator") &&
        /function runStoredJobCommand[\s\S]*stdoutOutput\.push/.test(companion) &&
        /function runStoredJobCommand[\s\S]*stderrOutput\.push/.test(companion) &&
        !/function runStoredJobCommand[\s\S]*stdoutChunks\.push/.test(companion),
      "background-output-capped-in-memory",
      "async worker caps stdout/stderr in memory before finishJob"
    ),
    result(companion.includes("DEFAULT_BACKGROUND_WAIT_MS") && companion.includes("async function waitForJob"), "short-wait-window", "--wait is bounded and async"),
    result(companion.includes("MAX_BACKGROUND_WAIT_MS") && !/async function waitForJob[\s\S]{0,500}max:\s*HARD_JOB_TIMEOUT_MS/.test(companion), "wait-window-ceiling", "--wait has a small ceiling separate from the hard job timeout"),
    result(companion.includes("function isExpectedActiveWaitStatus") && companion.includes('status === "queued" || status === "running"') && companion.includes('process.exit(waited.job.status === "succeeded" || stillRunning ? 0 : 1)'), "wait-cancelled-nonzero", "--wait treats terminal non-success jobs as nonzero and reports only queued/running timeouts as healthy"),
    result(companion.includes("--wait-timeout-ms") && companion.includes("stripBackgroundArgs"), "wait-timeout-stripped", "wait timeout flags are stripped"),
    result(jobs.includes("findActiveJobByIdempotencyKey") && companion.includes("deriveJobIdempotencyKey") && companion.includes("reusedExisting"), "job-idempotency-reuse", "duplicate active background submissions reuse the existing job"),
    result(companion.includes("function jobSnapshotAfterReap") && companion.includes("reapLostJobs(cwd, { jobs })") && companion.includes("activeJobsFromList(jobs)") && companion.includes("findActiveJobByIdempotencyKeyFromActive(active") && companion.includes("canStartBackgroundJobFromActive(active"), "job-submission-single-snapshot", "background and reserved submission paths reuse one job snapshot for reaping, idempotency, and capacity checks"),
    result(jobs.includes("DEFAULT_TERMINAL_JOB_RETENTION_MS") && jobs.includes("DEFAULT_TERMINAL_JOB_MAX_FILES") && jobs.includes("CLAUDE_FOR_CODEX_TERMINAL_JOB_RETENTION_MS") && jobs.includes("CLAUDE_FOR_CODEX_TERMINAL_JOB_MAX_FILES") && jobs.includes("pruneTerminalJobsFromSnapshot") && jobs.includes("job.resultViewedAt"), "terminal-job-retention-bound", "terminal job files have a bounded retention cleanup path during reaper scans without pruning unread results"),
    result(jobs.includes("parsePositiveInteger,") && !jobs.includes("function parsePositiveInteger(value, fallback"), "job-retention-shared-integer-parser", "job retention parsing reuses the shared lifecycle integer parser"),
    result(companion.includes('state: "queued-stale"') && companion.includes("validateJobWorkerProcess(job.workerPid, job.id).ok"), "queued-live-worker-not-lost", "user-facing lifecycle output distinguishes stale queued jobs with live workers from lost jobs"),
    result(jobs.includes('idempotencyKey: job.idempotencyKey ?? ""') && !jobs.includes("idempotencyKey: job.idempotencyKey ?? deriveJobIdempotencyKey(job)"), "legacy-claim-no-fake-idempotency", "legacy queued jobs without stored idempotency keys are not stamped with a non-matching recomputed key"),
    result(jobs.includes("hasActiveDirectJobWithIdempotencyKey") && jobs.includes("withWorkspaceJobLock(cwd, env") && jobs.includes("Another active direct job already owns this idempotency key."), "reserved-claim-direct-duplicate-guard", "host-forwarded reservations cannot start after a same-key direct job is active under the workspace lock"),
    result(/const BACKGROUND_EXECUTION_CONTROL_ENVS[\s\S]*TOP_MODEL_ENV[\s\S]*TOP_MODEL_FALLBACK_ENV[\s\S]*"CLAUDE_FOR_CODEX_BACKEND"/.test(companion), "job-idempotency-top-model-controls", "background idempotency includes top-model routing environment controls"),
    result(
      companion.includes("function reserveBackgroundJob") &&
        companion.includes("reserveJob(cwd") &&
        companion.includes("randomUUID") &&
        companion.includes('"--job-id"') &&
        companion.includes('"--cwd"') &&
        companion.includes("parseReservedJobRunArgs") &&
        companion.includes("claimReservedJob(stateCwd") &&
        companion.includes("finishJob(stateCwd") &&
        companion.includes("findActiveJobByIdempotencyKey") &&
        companion.includes('existing.status !== "queued"') &&
        companion.includes("canStartBackgroundJob") &&
        companion.includes("capacity_blocked") &&
        companion.includes("alreadyRunning") &&
        companion.includes("do not dispatch a forwarding subagent"),
      "reserve-job-cap-idempotency",
      "host-forwarded reserved jobs use the workspace cap, idempotency path, and explicit state cwd without returning invalid worker commands for active running jobs"
    ),
    result(companion.includes("async function claimReservedJobWithRetry") && companion.includes('claim.status === "workspace_locked"') && companion.includes("Reserved job claim could not acquire required state locks; retry later."), "reserved-claim-lock-retry", "reserved worker claims retry transient lock contention and report retryable lock failures"),
    result(companion.includes('current?.status === "cancelled"') && companion.includes('finished.status === "cancelled" ? 0'), "reserved-worker-cancelled-zero-exit", "reserved workers report already-cancelled jobs as cancelled with zero worker exit status"),
    result(lifecycle.includes("workspaceFingerprint") && lifecycle.includes("executionControls") && companion.includes("workingTreeFingerprintDetails(cwd, foregroundArgs)") && companion.includes("backgroundExecutionControls(process.env)") && fingerprint.includes("untrackedFilesFingerprintPart") && fingerprint.includes('"rev-parse", "HEAD"') && fingerprint.includes('"rev-parse", baseRef'), "job-idempotency-fingerprint-controls", "background idempotency includes worktree, HEAD/base refs, and execution controls"),
    result(companion.includes("workingTreeFingerprintDetails") && companion.includes("fingerprint.reuseDisabled") && companion.includes("fingerprintReuseDisabled") && companion.includes("fingerprintTimedOut") && fingerprint.includes("reuseDisabled: parts.some") && fingerprint.includes("failureKind") && fingerprint.includes("untrusted: true") && fingerprint.includes("INCONCLUSIVE") && fingerprint.includes("NON_GIT_REPOSITORY") && fingerprint.includes("UNTRACKED_FINGERPRINT_BUDGET_EXCEEDED") && fingerprint.includes("CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_BYTES") && fingerprint.includes("CLAUDE_FOR_CODEX_MAX_UNTRACKED_FINGERPRINT_FILES"), "job-idempotency-timeout-no-reuse", "background idempotency is disabled when worktree fingerprint collection times out or fails, while non-git workspaces still get a stable fingerprint"),
    result(companion.includes("workingTreeFingerprintMatches") && companion.includes("const hookOptions = hookFingerprintOptions()") && companion.includes("workingTreeFingerprintDetails(cwd, [], hookOptions)") && unread.includes("hookFingerprintOptions") && unread.includes("workingTreeFingerprint(cwd, [], hookFingerprintOptions())") && fingerprint.includes("legacyHashes") && fingerprint.includes("hashStdoutParts") && fingerprint.includes("HOOK_GIT_SIGNAL_TIMEOUT_MS") && fingerprint.includes("HOOK_MAX_UNTRACKED_FINGERPRINT_BYTES"), "review-gate-baseline-shared-fingerprint", "review-gate and UserPromptSubmit hook share bounded fingerprint logic with legacy baseline compatibility"),
    result(companion.includes("diffFingerprint.reuseDisabled") && companion.includes("diffFingerprint.budgetExceeded") && companion.includes("working-tree fingerprint timed out") && companion.includes("working-tree fingerprint inconclusive") && companion.includes("running review without cached gate decision") && fingerprint.includes("failureKind") && fingerprint.includes("budgetExceeded: parts.some"), "review-gate-fingerprint-timeout-fail-open", "review-gate fails open for true fingerprint timeouts and inconclusive git failures but still reviews when only the bounded untracked fingerprint budget is exceeded"),
    result(/function hasReviewableGitChanges[\s\S]{0,750}gitShort\(\["rev-parse"[\s\S]{0,80}options\)/.test(companion) && /function hasReviewableGitChanges[\s\S]{0,1000}gitShort\(\["status"[\s\S]{0,80}options\)/.test(companion) && companion.includes("hasReviewableGitChanges(cwd, hookOptions)") && companion.includes('reason: "git status timed out"') && companion.includes("reviewable.reason}; allowing stop") && companion.includes('args.quality = "standard"') && companion.includes("gitRunner: (gitArgs) => gitShort(gitArgs, cwd, hookOptions)"), "review-gate-reviewable-git-timeout", "review-gate bounds reviewable git probes, quality policy, and prompt git context with hook-safe timeouts before running Claude"),
    result(jobs.includes("storedOutput(result.stdout") && jobs.includes("MAX_STORED_OUTPUT_BYTES"), "job-result-sanitized", "finishJob sanitizes persisted output"),
    result(jobs.includes("stdoutBytes") && jobs.includes("stdoutStoredBytes") && jobs.includes("stdoutTruncated") && jobs.includes("stderrTruncated"), "job-output-truncation-metadata", "stored job output exposes explicit truncation metadata"),
    result(jobs.includes("metadata.bytes") && jobs.includes("metadata.truncated"), "job-output-worker-byte-metadata", "finishJob preserves worker-reported byte counts and truncation state"),
    result(jobs.includes('status: "locked"') && jobs.includes("resultViewedAt") && jobs.includes("Job state is busy"), "result-lock-contention-non-ok", "result does not report ok when resultViewedAt cannot be persisted"),
    result(jobs.includes("const sanitized = sanitizeJobForPersistentWrite(job, cwd)") && jobs.includes("return sanitized;"), "job-write-returns-sanitized", "job writes return sanitized payloads"),
    result(jobs.includes("worker-launch-failed") && lifecycle.includes("JOB_QUEUED_LOST_AFTER_MS") && lifecycle.includes("CLAUDE_FOR_CODEX_QUEUED_LOST_AFTER_MS"), "queued-worker-bootstrap-reaper", "worker exits before claim are reaped from queued"),
    result(lifecycle.includes("JOB_RESERVATION_CLAIM_MS") && lifecycle.includes("CLAUDE_FOR_CODEX_RESERVATION_CLAIM_MS") && lifecycle.includes("reservationClaimMs") && lifecycle.includes("abandonedReservation") && jobs.includes("reservation-expired") && jobs.includes("Host-forwarded reserved job was not claimed") && jobs.includes("mutateLostJobUnderLock") && jobs.includes('current.status !== "queued"'), "queued-reservation-expiry", "abandoned host-forwarded reserved jobs release capacity after a separate reservation claim timeout only after locked revalidation"),
    result(/async function waitForJob[\s\S]{0,900}maybeReapLostJobs/.test(companion) && companion.includes("nextReapAt = now + 5_000"), "wait-reaps-lost-jobs", "--wait polls the lost-job reaper before reporting job state"),
    result(fs.existsSync(progressPath) && companion.includes("progressEventsFromLines"), "progress-event-parser", "machine progress events parsed"),
    result(companion.includes("makeProgressLineBuffer"), "stderr-line-buffering", "split stderr lines are buffered"),
    result(backend.includes("function maybeWriteSdkProgress(event, options)") && backend.includes("formatProgressEvent") && !backend.includes("event.phase"), "sdk-progress-hook-point", "SDK progress uses real event fields"),
    result(companion.includes("function materializeBackgroundArgs") && companion.includes('parsed.backend === "sdk"') && companion.includes('"--stream-progress"') && companion.includes("materializeBackgroundArgs(command, rawArgs)") && companion.includes("materializeBackgroundArgs(command, tokens.slice(1))"), "sdk-background-progress-default", "SDK-backed background and reserved jobs auto-enable stream progress"),
    result(companion.includes("process.kill(-child.pid") && companion.includes("stopChildWithEscalation") && companion.includes("SIGTERM") && companion.includes("SIGINT"), "signal-child-group-cleanup", "child group cleanup is wired"),
    result(workerSignalHandlerIndex >= 0 && workerSpawnIndex >= 0 && workerSignalHandlerIndex < workerSpawnIndex && companion.includes("if (stopRequested)") && companion.includes('stopChildWithEscalation("SIGTERM")'), "worker-signal-handler-before-child-spawn", "worker signal handler is installed before spawning the supervised child"),
    result(processText.includes("captureProcessGroupIdentity") && processText.includes("missing saved process identity") && companion.includes("Child process group identity could not be validated") && companion.includes("!isProcessAlive(child.pid)") && processText.includes("commandHash") && processText.includes("processIdentityCommandMatches"), "child-process-identity-required", "child groups require stable saved private identity before signaling while fast exits are allowed to close normally"),
    result(companion.includes("function stopUnvalidatedChild") && companion.includes('stopUnvalidatedChild("SIGKILL")') && companion.includes("Child process group identity could not be validated"), "unvalidated-child-no-negative-pgid", "identity validation failure does not signal an unvalidated process group"),
    result(processText.includes("current === expected") && !processText.includes("expected.includes(current)") && !processText.includes("current.includes(expected)"), "process-identity-no-prefix-match", "process identity validation does not accept prefix/subset command matches"),
    result(jobs.includes("persistCancelledAfterValidatedSignal") && jobs.includes("cancelledJobUpdates") && jobs.includes("cancelWorkerDeferred") && jobs.includes("deferred: true") && jobs.includes("childTermination.ok && workerTermination.ok") && jobs.includes("requires child process group validation before signaling the worker") && jobs.includes("{ preserveActive: true }") && cancelChildTerminationIndex >= 0 && cancelChildDeliveredIndex > cancelChildTerminationIndex && cancelWorkerTerminationIndex > cancelChildDeliveredIndex, "cancel-child-worker-deferred", "cancel validates and terminates the child group first, persists cancellation from that delivered signal, and defers worker termination to avoid killing the worker while it holds the job lock"),
    result(jobs.includes("function cancelQueuedJob") && jobs.includes('current.status !== "queued"') && jobs.includes("return cancelJob(cwd, jobId, env)"), "cancel-queued-lock-reread", "queued cancel re-reads under lock and routes claimed jobs through running cancel"),
    result(jobs.includes("cancelQueuedWorkerPid") && jobs.includes("terminateValidatedJobWorker(updated.cancelQueuedWorkerPid") && jobs.includes("Queued worker cancellation requires process identity validation"), "cancel-queued-worker-terminated", "queued jobs with a spawned worker are not reported cancelled until the validated worker is handled"),
    result(jobs.includes("Running job cancellation did not deliver a signal") && jobs.includes("signalDelivered") && jobs.includes("phaseBeforeCancel") && jobs.includes("missingStartingChildSupervision") && jobs.includes("before child supervision metadata was persisted") && jobs.includes("preserveActive") && jobs.includes("Job reached a terminal state before cancellation failure could be persisted"), "cancel-requires-delivered-signal", "running cancel cannot report cancelled unless a validated signal was delivered and startup child supervision is known"),
    result(jobs.includes("Running job cancellation request could not be persisted; refusing to signal") && jobs.includes("Cancellation signal was delivered but the cancelled state could not be persisted"), "cancel-persistence-required", "running cancel does not report success when request or terminal state persistence fails"),
    result(jobs.includes("updateJobUnlessTerminal(cwd, jobId, updates") && jobs.includes("Job reached a terminal state before cancellation could be persisted"), "cancel-preserves-terminal-race", "running cancel cannot overwrite a terminal result persisted during termination"),
    result(jobs.includes("function hasEffectiveCancelRequest") && jobs.includes("requestedAt > failedAt") && jobs.includes("const cancelledByRequest = hasEffectiveCancelRequest(current);"), "cancel-request-finish-semantics", "worker results after a non-failed persisted cancel request are treated as cancelled"),
    result(processText.includes("export function processGroupHasLiveMembers") && processText.includes("\"-eo\"") && !processText.includes("\"-axo\"") && processText.includes("refusing to signal leaderless process group") && processText.includes("deliveredToValidatedGroup") && jobs.includes("leaderless-orphaned"), "leaderless-child-group-cleanup", "child process groups can be preserved after the leader exits without signaling unvalidated leaderless groups"),
    result(processText.includes("DEFAULT_PS_TIMEOUT_MS") && processText.includes("CLAUDE_FOR_CODEX_PS_TIMEOUT_MS") && processText.includes("killSignal: \"SIGKILL\"") && processText.includes("maxBuffer: psProbeMaxBufferBytes()") && processText.includes("export function processGroupLiveness") && processText.includes("export function psProbeDiagnostics") && processText.includes("recordGroupScanProbeFailure") && processText.includes("process group liveness probe inconclusive after SIGKILL") && jobs.includes("leaderless-liveness-inconclusive"), "process-ps-probe-bounds", "process validation ps probes are bounded, diagnostic, and inconclusive process-group scans preserve capacity"),
    result(companion.includes("CLAUDE_FOR_CODEX_KILL_GRACE_MS") && companion.includes("SIGKILL") && companion.includes("processGroupHasLiveMembers") && companion.includes('stopChildGroup("SIGKILL", { requireLiveGroup: hardTimedOut })') && companion.includes("Child process group still alive after hard timeout SIGKILL escalation") && companion.includes("did not emit close after hard timeout SIGKILL escalation"), "hard-timeout-sigkill", "hard timeout escalates after grace period only when the process group still has live members and records failure when close never arrives"),
    result((companion.includes("status: hardTimedOut ? 1") || companion.includes("commandResult(hardTimedOut ? 1")) && companion.includes("status: 1,") && companion.includes("after hard timeout"), "hard-timeout-nonzero-status", "hard timeout cannot be persisted as a successful zero exit"),
    result(processText.includes("terminateValidatedJobWorker") && processText.includes("SIGKILL") && processText.includes("CLAUDE_FOR_CODEX_KILL_GRACE_MS") && processText.includes('error?.code !== "ESRCH"'), "cancel-sigkill-escalation", "user cancel escalates after grace period and treats already-absent workers as gone"),
    result(processText.includes("process group still alive after SIGKILL") && processText.includes("worker process still alive after SIGKILL"), "cancel-final-liveness-check", "cancel verifies worker/process group liveness after SIGKILL"),
    result(jobs.includes("lockOwnerMatches") && jobs.includes("commandHash") && jobs.includes("currentLockOwner") && jobs.includes("captureProcessIdentity(process.pid)") && !jobs.includes("process.argv.join") && jobs.includes("writeError") && jobs.includes("fs.rmSync(lockFile"), "owner-aware-file-locks", "stale lock cleanup checks hashed owner identity and cleans up metadata write failures"),
    result(processText.includes("\"stat=\"") && processText.includes("startsWith(\"Z\")"), "zombie-process-not-alive", "zombie processes are not treated as live workers"),
    result(jobs.includes("reapLostJobs") && jobs.includes("mutateLostJobUnderLock") && jobs.includes("classifyJobLiveness(current") && jobs.includes("validateJobWorkerProcess") && jobs.includes("validateProcessGroupLeader") && processText.includes("isProcessAlive"), "process-aware-reaper", "reaper validates worker/child processes under lock"),
    result(companion.includes("recommend-execution-mode") && companion.includes("recommendModeArgs") && companion.includes('token === "--base"') && companion.includes('gitShort(["diff", "--shortstat", `${base}...HEAD`') && companion.includes("branchFileLines") && companion.includes("changedLineEstimate"), "execution-mode-recommendation", "foreground/background recommendation includes branch/base diff scope"),
    result(lifecycle.includes("CLAUDE_FOR_CODEX_GIT_SIGNAL_TIMEOUT_MS") && companion.includes("gitSignalTimeoutMs(env)") && companion.includes("ETIMEDOUT") && companion.includes("git signal collection timed out") && companion.includes("status.timedOut") && companion.includes("staged.timedOut") && companion.includes("unstaged.timedOut") && companion.includes("branch?.timedOut") && companion.includes("branchNames?.timedOut"), "git-timeout-not-nonrepo", "git timeout is distinct from not-a-repository and is detected through the shared run() helper timeout"),
    result(companion.includes("CLAUDE_FOR_CODEX_MAX_ACTIVE_JOBS") && jobs.includes("withWorkspaceJobLock") && jobs.includes("canStartBackgroundJob"), "background-concurrency-cap", "active background cap is under lock"),
    result(processText.includes("supportsPosixProcessGroups") && companion.includes("backgroundPlatformSupport") && companion.includes("unsupported_platform") && companion.includes("assertBackgroundPlatformSupported"), "background-posix-platform-guard", "background and reserved jobs fail fast where POSIX process groups are unavailable"),
    result(!gate.includes("--background") && !gate.toLowerCase().includes("ultrareview") && !gate.includes("startBackgroundJob("), "review-gate-no-background", "Stop hook does not spawn tracked/cloud jobs"),
    result(!gate.includes("reserveJob(") && companion.includes("rawArgs.includes(\"reserve-job\")") && companion.includes("allowing stop"), "review-gate-no-reserve-job", "Stop hook rejects reserved/background routing"),
    result(companion.includes("REVIEW_GATE_ROLE_TIMEOUT_MS") && companion.includes("REVIEW_GATE_TIMEOUT_MS") && companion.includes("reviewGateTimeoutMs") && companion.includes("reviewGateCacheKey") && companion.includes("CLAUDE_FOR_CODEX_CLAUDE_KILL_GRACE_MS") && companion.includes('child.kill("SIGKILL")') && companion.includes("gateDeadline") && companion.includes("gateReviewComplete") && companion.includes("allowCount > 0") && companion.includes("review gate aggregate timeout reached; allowing stop") && companion.includes("warnGate") && companion.includes("allowing stop"), "review-gate-bounded-fail-open", "Stop hook has bounded aggregate and per-role fail-open behavior without caching incomplete reviews or reusing allow decisions across role/quality changes")
  ];
}

function resourceGovernorChecks(pluginRoot) {
  const governorPath = path.join(pluginRoot, "scripts", "lib", "resource-governor.mjs");
  const companionPath = path.join(pluginRoot, "scripts", "claude-companion.mjs");
  const backendPath = path.join(pluginRoot, "scripts", "lib", "claude-backend.mjs");
  const hookPath = path.join(pluginRoot, "hooks", "claude-review-gate.mjs");
  const governor = fs.existsSync(governorPath) ? fs.readFileSync(governorPath, "utf8") : "";
  const companion = fs.existsSync(companionPath) ? fs.readFileSync(companionPath, "utf8") : "";
  const backend = fs.existsSync(backendPath) ? fs.readFileSync(backendPath, "utf8") : "";
  const hook = fs.existsSync(hookPath) ? fs.readFileSync(hookPath, "utf8") : "";
  const requiredExports = [
    "CAPACITY_BLOCKED_STATUS",
    "CAPACITY_BLOCKED_EXIT_CODE",
    "defaultResourceLockRoot",
    "acquireClaudeCapacity",
    "withClaudeCapacity",
    "availableClaudeCapacity",
    "capacityBlockedResult",
    "inspectResourceGovernor",
    "refreshLeaseForTest",
    "setResourceGovernorTestHooksForTest"
  ];
  const directBypasses = [];
  const directClaudePattern = /(?:^|[^\w.])(?:spawn|spawnSync|execFile|execFileSync)\s*\(\s*["']claude["']|\.((?:spawn|spawnSync|execFile|execFileSync))\s*\(\s*["']claude["']|(?:spawn|spawnSync|execFile|execFileSync)\s*\(\s*["']sh["'][\s\S]{0,220}\bclaude\b|\.((?:spawn|spawnSync|execFile|execFileSync))\s*\(\s*["']sh["'][\s\S]{0,220}\bclaude\b/;
  for (const file of listFiles(pluginRoot, ["scripts", "hooks"])) {
    if (!file.endsWith(".mjs")) {
      continue;
    }
    const relative = path.relative(pluginRoot, file);
    if (relative === path.join("scripts", "lib", "release-check.mjs")) {
      continue;
    }
    const text = fs.readFileSync(file, "utf8");
    if (directClaudePattern.test(text)) {
      directBypasses.push(relative);
    }
  }
  const ok = fs.existsSync(governorPath)
    && requiredExports.every((name) => governor.includes(`export function ${name}`) || governor.includes(`export async function ${name}`) || governor.includes(`export const ${name}`))
    && companion.includes('RESOURCE_GOVERNOR_COMPANION_WIRING = "resource-governor:companion-v1"')
    && companion.includes("acquireClaudeCapacity({")
    && companion.includes("capacityBlockedClaudeResult")
    && companion.includes("writeCapacityBlockedAndExit")
    && companion.includes("runParallelRoleReviews(args.reviewRoles")
    && companion.includes("function sdkSubagentCapacitySlots(args, env = process.env)")
    && companion.includes("slots: sdkSubagentCapacitySlots(args, process.env)")
    && backend.includes("requires capacityLease before loading the SDK module")
    && !/capacityLease\s*\|\|\s*capacityChecked/.test(backend)
    && hook.includes("CAPACITY_BLOCKED_STATUS")
    && hook.includes("allowing stop")
    && directBypasses.length === 0;
  return [
    result(ok, "resource-governor", directBypasses.length ? `direct Claude spawn bypass: ${directBypasses.join(", ")}` : "global Claude work-slot governor is wired")
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

function readRoutingContract(pluginRoot) {
  try {
    return {
      loaded: true,
      value: readJson(path.join(pluginRoot, "contracts", "natural-language-routing.json"))
    };
  } catch (error) {
    return {
      loaded: false,
      value: {},
      error: error.message || String(error)
    };
  }
}

function markdownSectionBetween(text, anchor, startMarker, endMarker) {
  const anchorIndex = text.indexOf(anchor);
  if (anchorIndex === -1) return "";
  const start = text.indexOf(startMarker, anchorIndex);
  if (start === -1) return "";
  const bodyStart = start + startMarker.length;
  const end = text.indexOf(endMarker, bodyStart);
  if (end === -1) return "";
  return text.slice(bodyStart, end);
}

function checkNaturalLanguageRouting(root) {
  const { repoRoot, pluginRoot, installedPluginOnly } = resolveLayout(root);
  const { loaded, value: contract, error } = readRoutingContract(pluginRoot);
  if (!loaded) {
    return [result(false, "skills-natural-language-routing", error)];
  }
  const skillChecks = [];
  for (const skill of contract.routedClaudeSkills ?? []) {
    const skillPath = path.join(pluginRoot, "skills", skill, "SKILL.md");
    const text = fs.existsSync(skillPath) ? fs.readFileSync(skillPath, "utf8") : "";
    const userExamples = markdownSectionBetween(
      text,
      "## Natural-Language Claude Routing",
      contract.userExamplesStart,
      contract.userExamplesEnd
    );
    const ok = Boolean(text)
      && (contract.requiredAnchors ?? []).every((anchor) => text.includes(anchor))
      && (contract.requiredCommonPolicyPhrases ?? []).every((phrase) => text.includes(phrase))
      && ((contract.requiredPolicyPhrasesBySkill ?? {})[skill] ?? []).every((phrase) => text.includes(phrase))
      && ((contract.skillMarkers ?? {})[skill] ?? []).every((marker) => text.includes(marker))
      && Boolean(userExamples)
      && (contract.forbiddenUserExampleSubstrings ?? []).every((forbidden) => !userExamples.includes(forbidden));
    skillChecks.push(ok);
  }
  const docEntries = Object.entries(contract.docsRequiredPhrases ?? {});
  const docChecks = docEntries.flatMap(([relativePath, phrases]) => {
    if (installedPluginOnly && relativePath.startsWith("docs/")) {
      return [];
    }
    const absolute = installedPluginOnly && relativePath === "plugins/claude-for-codex/README.md"
      ? path.join(pluginRoot, "README.md")
      : path.join(repoRoot, relativePath);
    const text = fs.existsSync(absolute) ? fs.readFileSync(absolute, "utf8") : "";
    return [Boolean(text) && phrases.every((phrase) => text.includes(phrase))];
  });
  return [
    result(skillChecks.every(Boolean) && docChecks.every(Boolean), "skills-natural-language-routing")
  ];
}

function checkGithubActionsCi(root) {
  const { repoRoot, pluginRoot, installedPluginOnly } = resolveLayout(root);
  const defaultWorkflow = renderWorkflow(pluginRoot);
  const annotationWorkflow = renderWorkflow(pluginRoot, { annotations: true });
  const validation = validateWorkflow(defaultWorkflow);
  const annotationValidation = validateWorkflow(annotationWorkflow, { annotations: true });
  const ciWorkflow = installedPluginOnly ? "" : path.join(repoRoot, ".github", "workflows", "claude-for-codex-ci.yml");
  const ciText = ciWorkflow && fs.existsSync(ciWorkflow) ? fs.readFileSync(ciWorkflow, "utf8") : "";
  const checks = [
    result(validation.ok && annotationValidation.ok, "github-actions-template-safe"),
    result(validation.checks.some((check) => check.name === "github-actions-fork-safe" && check.ok), "github-actions-fork-safe"),
    result(validation.checks.some((check) => check.name === "immutable-release-ref" && check.ok), "github-actions-immutable-ref"),
    result(validation.checks.some((check) => check.name === "plugin-root-resolved" && check.ok), "github-actions-plugin-root-resolved"),
    result(validation.checks.some((check) => check.name === "no-repo-relative-runtime-path" && check.ok), "github-actions-no-repo-relative-runtime-path"),
    result(defaultWorkflow.includes(`--ref ${DEFAULT_RELEASE_REF}`), "github-actions-current-release-ref", DEFAULT_RELEASE_REF),
    result(defaultWorkflow.includes("ubuntu-latest") && defaultWorkflow.includes("npm install -g @openai/codex"), "github-actions-runner-sim"),
    result(!/\/Users\/fanghao/.test(defaultWorkflow), "github-actions-no-local-paths"),
    result(!defaultWorkflow.includes("pull_request_target"), "github-actions-no-pull-request-target"),
    result(defaultWorkflow.includes("BASE_SHA: ${{ github.event.pull_request.base.sha }}"), "github-actions-base-sha-env"),
    result(defaultWorkflow.includes("MODEL_ARGS=()"), "github-actions-model-effort-array"),
    result(defaultWorkflow.includes('MODEL_ARGS+=(--model "$CLAUDE_FOR_CODEX_MODEL")'), "github-actions-model-env-forwarded"),
    result(defaultWorkflow.includes('MODEL_ARGS+=(--effort "$CLAUDE_FOR_CODEX_EFFORT")'), "github-actions-effort-env-forwarded"),
    result(defaultWorkflow.includes('${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}'), "github-actions-model-effort-quoted"),
    result(defaultWorkflow.includes("retention-days: 5"), "github-actions-short-artifact-retention"),
    result(installedPluginOnly || fs.existsSync(ciWorkflow), "github-actions-ci-present"),
    result(installedPluginOnly || (!ciText.includes("pull_request_target") && ciText.includes("pull_request:") && ciText.includes("push:")), "github-actions-ci-fork-safe"),
    result(installedPluginOnly || ciText.includes("contents: read"), "github-actions-ci-minimal-permissions"),
    result(installedPluginOnly || ciText.includes("node --check plugins/claude-for-codex/scripts/claude-companion.mjs"), "github-actions-ci-node-check"),
    result(
      installedPluginOnly ||
        (
          ciText.includes("pytest -q tests/test_claude_permission_compat.py") &&
          ciText.includes('pytest -q tests/test_claude_for_codex_plugin.py -k "') &&
          ciText.includes("release_check") &&
          ciText.includes("plan_review") &&
          ciText.includes("multi_review") &&
          ciText.includes("native_plugin") &&
          ciText.includes("claude_code_plugin_pack") &&
          ciText.includes("native_agent_profiles") &&
          ciText.includes("review_uses_stdin_prompt_transport") &&
          ciText.includes("resource_governor") &&
          ciText.includes("capacity_blocked") &&
          ciText.includes("outcome_classifier") &&
          ciText.includes("doctor_json") &&
          ciText.includes("doctor_reports") &&
          ciText.includes("hook_compat") &&
          !ciText.includes("pytest -q tests/test_claude_for_codex_plugin.py tests/test_claude_permission_compat.py")
        ),
      "github-actions-ci-pytest",
      "PR dogfood runs permission compatibility plus bounded Claude plugin smoke tests instead of the full long lifecycle suite"
    ),
    result(installedPluginOnly || ciText.includes("release-check --ci-simulate --json"), "github-actions-ci-release-check"),
    result(installedPluginOnly || ciText.includes("git diff --check"), "github-actions-ci-whitespace")
  ];
  return checks;
}

function checkPrompts(root) {
  const { pluginRoot } = resolveLayout(root);
  const promptDir = path.join(pluginRoot, "prompts");
  const prompts = fs.readdirSync(promptDir).filter((name) => name.endsWith(".md"));
  const partialPrompts = new Set(["assisted-review.md", "scorecard.md", "taskset.md"]);
  return prompts.map((prompt) => {
    const text = fs.readFileSync(path.join(promptDir, prompt), "utf8");
    const hasTask = text.includes("<task>") || partialPrompts.has(prompt);
    const hasTemplateVariable = text.includes("{{") || prompt === "assisted-review.md";
    return result(hasTask && hasTemplateVariable, `prompt-${prompt}`);
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
  const tmp = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), "cfc-release-check-"));
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
  if (install.status !== 0) {
    return [result(!options.requireRemoteInstall, "remote-install-smoke", `skipped: ${install.stderr || install.error || "install failed"}`)];
  }
  const list = spawnSync("codex", ["plugin", "list", "--json"], {
    env,
    encoding: "utf8",
    timeout
  });
  if (list.status !== 0) {
    return [
      result(true, "remote-install-smoke", `installed ref=${releaseRef}`),
      result(!options.requireRemoteInstall, "remote-install-plugin-list-schema", `skipped: ${list.stderr || list.error || "plugin list failed"}`)
    ];
  }
  const installed = validateCodexInstalledClaudePlugin(list.stdout ?? "");
  return [
    result(true, "remote-install-smoke", `installed ref=${releaseRef}`),
    result(installed.ok || !options.requireRemoteInstall, "remote-install-plugin-list-schema", installed.detail)
  ];
}

export function runReleaseCheck(root, options = {}) {
  const checks = [
    ...checkManifest(root),
    ...checkVersionSurfacesCurrent(root),
    ...checkNoStaleReleaseRef(root),
    ...checkHooks(root),
    ...checkDocs(root),
    ...checkClaudeNativeUniversality(root),
    ...checkNativeReleaseAssets(root),
    ...checkReadOnlyIsolation(root),
    ...longRunningLifecycleChecks(resolveLayout(root).pluginRoot),
    ...resourceGovernorChecks(resolveLayout(root).pluginRoot),
    ...checkSecrets(root),
    ...checkSkills(root),
    ...checkSubagentReviewDocs(root),
    ...checkNaturalLanguageRouting(root),
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
