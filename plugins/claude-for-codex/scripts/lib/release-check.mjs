import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const SECRET_PATTERNS = [
  { name: "private-key", pattern: /BEGIN (RSA|OPENSSH|EC|DSA)? ?PRIVATE KEY/ },
  { name: "github-token", pattern: /gh[pousr]_[A-Za-z0-9_]{20,}/ },
  { name: "openai-key", pattern: /sk-[A-Za-z0-9_-]{20,}/ },
  { name: "aws-access-key", pattern: /AKIA[0-9A-Z]{16}/ }
];

const SECRET_ASSIGNMENT_PATTERN = /\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*["']([A-Za-z0-9_./+=:-]{16,})["']/i;

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

function commandExists(name) {
  const check = spawnSync(name, ["--version"], { encoding: "utf8", timeout: 5000 });
  return check.status === 0;
}

function checkManifest(root) {
  const manifest = readJson(path.join(root, "plugins", "claude-for-codex", ".codex-plugin", "plugin.json"));
  const changelog = fs.readFileSync(path.join(root, "plugins", "claude-for-codex", "CHANGELOG.md"), "utf8");
  const checks = [
    result(manifest.version === "0.9.0", "manifest-version", `version=${manifest.version}`),
    result(changelog.includes("## 0.9.0"), "changelog-version", "CHANGELOG contains 0.9.0"),
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
  const fixtureDir = path.join(root, "plugins", "claude-for-codex", "fixtures", "semantic");
  const safe = readJson(path.join(fixtureDir, "safe-provider.json"));
  const unsafe = readJson(path.join(fixtureDir, "unsafe-provider.json"));
  return [
    result(semanticFixtureSafe(safe), "semantic-fixture-safe"),
    result(!semanticFixtureSafe(unsafe), "semantic-fixture-unsafe")
  ];
}

function checkHooks(root) {
  const hooksFile = path.join(root, "plugins", "claude-for-codex", "hooks", "hooks.json");
  const parsed = readJson(hooksFile);
  const events = Object.keys(parsed.hooks ?? {}).sort();
  return [
    result(JSON.stringify(events) === JSON.stringify(["SessionEnd", "SessionStart", "Stop", "UserPromptSubmit"].sort()), "hook-events", events.join(",")),
    result(Object.values(parsed.hooks ?? {}).every((entries) => Array.isArray(entries)), "hook-shapes")
  ];
}

function checkDocs(root) {
  const docs = [
    "README.md",
    "docs/README.en.md",
    "docs/README.zh-CN.md",
    "plugins/claude-for-codex/README.md"
  ];
  const checks = [];
  for (const file of docs) {
    const text = fs.readFileSync(path.join(root, file), "utf8");
    checks.push(result(text.includes("external-models-for-codex"), `docs-marketplace-${file}`));
    checks.push(result(!text.includes("external-models-for-codex-local"), `docs-no-old-marketplace-${file}`));
    checks.push(result(!/\/Users\/fanghao/.test(text), `docs-no-local-path-${file}`));
  }
  return checks;
}

function checkSecrets(root) {
  const checks = [];
  for (const file of listFiles(root, ["README.md", "docs", "plugins/claude-for-codex"])) {
    if (file.includes(`${path.sep}docs${path.sep}superpowers${path.sep}`)) {
      continue;
    }
    const text = fs.readFileSync(file, "utf8");
    const relative = path.relative(root, file);
    if (text.includes("release-check allowlist")) {
      continue;
    }
    for (const { name, pattern } of SECRET_PATTERNS) {
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
  const skillsDir = path.join(root, "plugins", "claude-for-codex", "skills");
  const skills = fs.readdirSync(skillsDir).filter((name) => fs.existsSync(path.join(skillsDir, name, "SKILL.md")));
  return [
    result(skills.length === 10, "skill-count", String(skills.length)),
    ...skills.map((skill) => {
      const text = fs.readFileSync(path.join(skillsDir, skill, "SKILL.md"), "utf8");
      return result(text.startsWith("---") && text.includes("claude-companion.mjs"), `skill-${skill}`);
    })
  ];
}

function checkPrompts(root) {
  const promptDir = path.join(root, "plugins", "claude-for-codex", "prompts");
  const prompts = fs.readdirSync(promptDir).filter((name) => name.endsWith(".md"));
  return prompts.map((prompt) => {
    const text = fs.readFileSync(path.join(promptDir, prompt), "utf8");
    return result(text.includes("<task>") && text.includes("{{"), `prompt-${prompt}`);
  });
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
  const add = spawnSync("codex", ["plugin", "marketplace", "add", "yilibinbin/external-models-for-codex", "--ref", "main"], {
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
  return [result(install.status === 0 || !options.requireRemoteInstall, "remote-install-smoke", install.status === 0 ? "installed" : `skipped: ${install.stderr || install.error || "install failed"}`)];
}

export function runReleaseCheck(root, options = {}) {
  const checks = [
    ...checkManifest(root),
    ...checkHooks(root),
    ...checkDocs(root),
    ...checkSecrets(root),
    ...checkSkills(root),
    ...checkPrompts(root),
    ...checkSemanticFixtures(root),
    ...remoteInstallSmoke(root, options)
  ];
  return {
    ok: checks.every((check) => check.ok),
    checks
  };
}
