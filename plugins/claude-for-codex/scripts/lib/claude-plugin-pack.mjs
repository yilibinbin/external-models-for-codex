import fs from "node:fs";
import path from "node:path";
import {
  nativeAgentProfiles,
  renderNativeAgentMarkdown,
  validateNativeAgentMarkdown
} from "./native-agent-profiles.mjs";

const AGENT_FILE_PATTERN = /^cfc-[a-z0-9-]+-reviewer\.md$/;
const ALLOWED_TOP_LEVEL_ENTRIES = new Set([".claude-plugin", "agents", "README.md"]);
const IGNORED_TOP_LEVEL_ENTRIES = new Set([".DS_Store", "Thumbs.db"]);
const FORBIDDEN_SURFACES = Object.freeze(["hooks", "skills", "commands", ".mcp.json", "settings.json"]);

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function safeStat(file) {
  try {
    return fs.statSync(file);
  } catch (error) {
    return { error };
  }
}

function statErrorMessage(error) {
  return error?.message || String(error);
}

function normalizeNewlines(value) {
  return String(value ?? "").replace(/\r\n/g, "\n");
}

export function claudeCodePluginPackPath(pluginRoot) {
  return path.join(pluginRoot, "claude-code-plugin");
}

export function validateClaudeCodePluginPack(pluginRoot) {
  const packPath = claudeCodePluginPackPath(pluginRoot);
  const errors = [];
  const rootManifestPresent = fs.existsSync(path.join(pluginRoot, ".claude-plugin", "plugin.json"));
  if (rootManifestPresent) {
    errors.push("root .claude-plugin manifest must not exist");
  }

  const packStat = safeStat(packPath);
  if (packStat.error) {
    errors.push("claude-code-plugin pack directory must exist");
  } else if (!packStat.isDirectory()) {
    errors.push("claude-code-plugin pack path must be a directory");
  } else {
    try {
      for (const entry of fs.readdirSync(packPath)) {
        if (IGNORED_TOP_LEVEL_ENTRIES.has(entry)) {
          continue;
        }
        if (!ALLOWED_TOP_LEVEL_ENTRIES.has(entry)) {
          errors.push(`unexpected top-level entry ${entry}`);
        }
      }
    } catch (error) {
      errors.push(`pack directory read failed: ${statErrorMessage(error)}`);
    }
  }

  const claudePluginDir = path.join(packPath, ".claude-plugin");
  const manifestPath = path.join(packPath, ".claude-plugin", "plugin.json");
  const codexManifestPath = path.join(pluginRoot, ".codex-plugin", "plugin.json");
  let manifest = {};
  let codexManifest = {};
  const claudePluginStat = safeStat(claudePluginDir);
  if (claudePluginStat.error) {
    errors.push(".claude-plugin directory must exist");
  } else if (!claudePluginStat.isDirectory()) {
    errors.push(".claude-plugin path must be a directory");
  }
  const manifestStat = safeStat(manifestPath);
  if (manifestStat.error) {
    errors.push(`Claude plugin manifest read failed: ${statErrorMessage(manifestStat.error)}`);
  } else if (!manifestStat.isFile()) {
    errors.push("Claude plugin manifest path must be a regular file");
  } else {
    try {
      manifest = readJson(manifestPath);
    } catch (error) {
      errors.push(`Claude plugin manifest read failed: ${statErrorMessage(error)}`);
    }
  }
  try {
    codexManifest = readJson(codexManifestPath);
  } catch (error) {
    errors.push(`Codex plugin manifest read failed: ${statErrorMessage(error)}`);
  }
  if (manifest.name !== "claude-for-codex-native-review") {
    errors.push("bad Claude plugin name");
  }
  if (manifest.version !== codexManifest.version) {
    errors.push("Claude plugin version must match Codex plugin version");
  }

  for (const surface of FORBIDDEN_SURFACES) {
    if (fs.existsSync(path.join(packPath, surface))) {
      errors.push(`pack must not include ${surface}`);
    }
  }

  const agentsDir = path.join(packPath, "agents");
  let agentFiles = [];
  const agentsStat = safeStat(agentsDir);
  if (agentsStat.error) {
    errors.push("agents directory must exist");
  } else if (!agentsStat.isDirectory()) {
    errors.push("agents path must be a directory");
  } else {
    try {
      agentFiles = fs.readdirSync(agentsDir).filter((name) => name.endsWith(".md")).sort();
    } catch (error) {
      errors.push(`agents directory read failed: ${statErrorMessage(error)}`);
    }
  }
  if (agentFiles.length !== 5) {
    errors.push(`expected 5 agent files, found ${agentFiles.length}`);
  }

  const expectedMarkdownByFile = new Map(nativeAgentProfiles().map((profile) => [
    `${profile.markdownAgentName.replaceAll("_", "-")}.md`,
    renderNativeAgentMarkdown(profile)
  ]));
  for (const expectedFile of expectedMarkdownByFile.keys()) {
    if (!agentFiles.includes(expectedFile)) {
      errors.push(`missing agent file ${expectedFile}`);
    }
  }

  for (const file of agentFiles) {
    if (!AGENT_FILE_PATTERN.test(file)) {
      errors.push(`unexpected agent file ${file}`);
    }
    const agentPath = path.join(agentsDir, file);
    const agentStat = safeStat(agentPath);
    if (agentStat.error) {
      errors.push(`${file}: agent file read failed: ${statErrorMessage(agentStat.error)}`);
      continue;
    }
    if (!agentStat.isFile()) {
      errors.push(`${file}: agent path must be a regular file`);
      continue;
    }
    let markdown;
    try {
      markdown = fs.readFileSync(agentPath, "utf8");
    } catch (error) {
      errors.push(`${file}: agent file read failed: ${statErrorMessage(error)}`);
      continue;
    }
    const validation = validateNativeAgentMarkdown(markdown, file);
    errors.push(...validation.errors.map((error) => `${file}: ${error}`));
    if (expectedMarkdownByFile.has(file) && normalizeNewlines(markdown) !== normalizeNewlines(expectedMarkdownByFile.get(file))) {
      errors.push(`${file}: does not match renderNativeAgentMarkdown output`);
    }
  }

  return {
    ok: errors.length === 0,
    errors,
    packPath,
    rootManifestPresent,
    agentCount: agentFiles.length,
    agentFiles
  };
}
