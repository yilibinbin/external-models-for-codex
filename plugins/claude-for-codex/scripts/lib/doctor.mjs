import fs from "node:fs";
import path from "node:path";
import { backendCapabilities } from "./claude-backend.mjs";
import { hookCompatibilityReport } from "./hook-compat.mjs";
import { installConsistencyReport } from "./install-consistency.mjs";
import { semanticCapabilities } from "./semantic-context.mjs";

function hookFiles(pluginRoot = "") {
  return {
    hooksJson: Boolean(pluginRoot) && fs.existsSync(path.join(pluginRoot, "hooks", "hooks.json")),
    stopGate: Boolean(pluginRoot) && fs.existsSync(path.join(pluginRoot, "hooks", "claude-review-gate.mjs"))
  };
}

function doctorOk(report) {
  return Boolean(
    report.checks.hookFiles.hooksJson &&
    report.checks.hookFiles.stopGate &&
    report.checks.hooks.unsupportedInstalledEvents.length === 0
  );
}

export function doctorReport({
  cwd = process.cwd(),
  pluginRoot = "",
  env = process.env,
  capabilities = {},
  state = {},
  release = {},
  installConsistency = {}
} = {}) {
  const claude = capabilities.claude ?? {};
  const hookEvents = capabilities.hooks?.events ?? [];
  const checks = {
    claude,
    modelAliases: claude.modelAliases ?? {},
    fallbackModel: {
      supported: Boolean(claude.fallbackModel),
      listSupported: Boolean(claude.fallbackModelList)
    },
    backend: capabilities.backend ?? backendCapabilities(env, cwd),
    hooks: hookCompatibilityReport({ installedEvents: hookEvents.length ? hookEvents : undefined }),
    hookFiles: hookFiles(pluginRoot),
    reviewGate: state.reviewGate ?? {},
    semanticProviders: capabilities.semanticContext ?? semanticCapabilities(cwd, env),
    installConsistency: installConsistency.report ?? installConsistencyReport({
      pluginRoot,
      pluginListJson: installConsistency.pluginListJson ?? "",
      pluginListAvailable: installConsistency.pluginListAvailable ?? Boolean(installConsistency.pluginListJson)
    }),
    release
  };
  const report = {
    ok: false,
    cwd,
    pluginRoot,
    checks
  };
  report.ok = doctorOk(report);
  return report;
}

export function renderDoctorText(report) {
  const aliases = Object.entries(report.checks.modelAliases ?? {})
    .filter(([, value]) => Boolean(value))
    .map(([key]) => key)
    .join(", ");
  return [
    `Claude for Codex doctor: ${report.ok ? "ok" : "attention"}`,
    `pluginRoot: ${report.pluginRoot}`,
    `claude: ${report.checks.claude.available ? "available" : "missing"}`,
    `model aliases: ${aliases || "unknown"}`,
    `sdk: ${report.checks.backend?.claudeSdk?.available ? "available" : "missing"}`,
    `hooks: ${(report.checks.hooks.installedEvents ?? []).join(", ")}`,
    `review gate: ${report.checks.reviewGate.enabled ? "enabled" : "disabled"}`,
    `install: ${report.checks.installConsistency?.ok === false ? "attention" : report.checks.installConsistency?.status || "unknown"}`
  ].join("\n");
}
