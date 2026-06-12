import fs from "node:fs";
import path from "node:path";

const DEFAULT_PLUGIN_ID = "claude-for-codex@external-models-for-codex";
const DEFAULT_PLUGIN_NAME = "claude-for-codex";
const MARKETPLACE_NAME = "external-models-for-codex";

function parseJson(text, fallback) {
  try {
    return JSON.parse(String(text ?? ""));
  } catch {
    return fallback;
  }
}

function manifestVersion(pluginRoot = "") {
  if (!pluginRoot) {
    return "";
  }
  const manifest = path.join(pluginRoot, ".codex-plugin", "plugin.json");
  const parsed = parseJson(fs.existsSync(manifest) ? fs.readFileSync(manifest, "utf8") : "", {});
  return typeof parsed.version === "string" ? parsed.version : "";
}

function sourcePath(entry) {
  return typeof entry?.source?.path === "string" ? entry.source.path : "";
}

function installedEntry(pluginListJson, { pluginId, pluginName = DEFAULT_PLUGIN_NAME, marketplaceName = MARKETPLACE_NAME } = {}) {
  const parsed = typeof pluginListJson === "string" ? parseJson(pluginListJson, {}) : pluginListJson;
  return (parsed?.installed ?? []).find((entry) => {
    if (entry?.pluginId === pluginId) {
      return true;
    }
    return entry?.name === pluginName && entry?.marketplaceName === marketplaceName;
  }) ?? null;
}

function commandList(pluginId = DEFAULT_PLUGIN_ID) {
  const marketplace = pluginId.includes("@") ? pluginId.split("@").pop() : MARKETPLACE_NAME;
  return [
    `codex plugin marketplace upgrade ${marketplace}`,
    `codex plugin add ${pluginId}`
  ];
}

export function installConsistencyReport({
  pluginRoot = "",
  pluginListJson = "",
  pluginId = DEFAULT_PLUGIN_ID,
  pluginName = DEFAULT_PLUGIN_NAME,
  marketplaceName = MARKETPLACE_NAME,
  pluginListAvailable = true
} = {}) {
  const runningVersion = manifestVersion(pluginRoot);
  if (!pluginListAvailable || !pluginListJson) {
    return {
      ok: true,
      status: "unknown",
      pluginId,
      runningVersion,
      installedVersion: "",
      cacheVersion: "",
      enabled: false,
      problems: [{ code: "plugin-list-unavailable", message: "codex plugin list --json was unavailable." }],
      recommendedCommands: []
    };
  }

  const entry = installedEntry(pluginListJson, { pluginId, pluginName, marketplaceName });
  const installedVersion = typeof entry?.version === "string" ? entry.version : "";
  const cacheVersion = entry ? manifestVersion(sourcePath(entry)) : "";
  const enabled = entry ? Boolean(entry.enabled) : false;
  const problems = [];
  let versionUnknown = false;

  if (!entry) {
    problems.push({ code: "plugin-not-installed", message: `${pluginId} is not installed.` });
  }
  if (entry && !enabled) {
    problems.push({ code: "plugin-disabled", message: `${pluginId} is installed but disabled.` });
  }
  if (entry && !installedVersion) {
    versionUnknown = true;
    problems.push({ code: "installed-version-unavailable", message: `${pluginId} was found, but Codex did not report an installed version.` });
  }
  if (runningVersion && installedVersion && runningVersion !== installedVersion) {
    problems.push({
      code: "stale-installed-version",
      message: `${pluginId} installed version ${installedVersion} differs from running plugin version ${runningVersion}.`
    });
  }

  const attentionProblems = problems.filter((problem) => problem.code !== "installed-version-unavailable");
  return {
    ok: attentionProblems.length === 0,
    status: attentionProblems.length ? "attention" : versionUnknown ? "unknown" : "ok",
    pluginId,
    runningVersion,
    installedVersion,
    cacheVersion,
    enabled,
    problems,
    recommendedCommands: attentionProblems.length ? commandList(pluginId) : []
  };
}
