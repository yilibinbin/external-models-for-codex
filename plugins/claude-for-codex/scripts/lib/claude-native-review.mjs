import { configuredWriteDenyTools } from "./claude-backend.mjs";
import { normalizeModelSelection } from "./model-registry.mjs";

const READ_ONLY_TOOLS = Object.freeze(["Read", "Grep", "Glob"]);
const NATIVE_PARENT_DENY_TOOLS = Object.freeze(["Agent"]);

function roleName(role) {
  if (typeof role === "string") {
    return role;
  }
  return role?.name || role?.id || role?.title || "reviewer";
}

function roleDescription(role) {
  if (typeof role === "string") {
    return `${role} reviewer`;
  }
  return role?.description || role?.summary || `${roleName(role)} reviewer`;
}

function rolePrompt(role) {
  if (typeof role === "string") {
    return `Review the change from the ${role} perspective.`;
  }
  return role?.prompt || role?.systemPrompt || role?.instructions || roleDescription(role);
}

export function nativeAgentName(role) {
  const sanitized = roleName(role)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_");
  return `cfc_${sanitized || "reviewer"}`;
}

function nativeAgentModel(model) {
  const normalized = normalizeModelSelection(model || "inherit");
  if (!normalized.valid || normalized.family === "custom" || normalized.family === "default") {
    return "inherit";
  }
  return normalized.model;
}

function structuredReviewContract() {
  return [
    "Return exactly one JSON object and no Markdown.",
    "The JSON object must use this schema:",
    "{",
    '  "verdict": "approve | needs-attention",',
    '  "summary": "short role-specific review judgment",',
    '  "findings": [',
    '    {"severity": "critical|high|medium|low", "title": "issue title", "body": "issue, evidence, and impact", "file": "path", "line_start": 1, "line_end": 1, "confidence": 0.8, "recommendation": "concrete action"}',
    "  ],",
    '  "next_steps": ["concrete next step"]',
    "}",
    "Use verdict approve only when there are no material findings.",
    "Use an empty findings array when there are no findings."
  ].join("\n");
}

function nativeAgentPrompt(role, { structuredJson = false } = {}) {
  const outputInstruction = structuredJson
    ? structuredReviewContract()
    : "Return concise findings for your assigned role as JSON-compatible content.";
  return [
    "You are a read-only Claude for Codex review subagent.",
    "You run in a fresh isolated context. You do not see the parent conversation history, prior tool results, or files the parent already read unless they are included in this prompt.",
    "Inspect repository files and git context only. Do not edit files, run shell commands, spawn agents, or request write-capable tools.",
    "Use only Read, Grep, and Glob when tool access is needed.",
    "Do not invoke Agent, Task, Workflow, Bash, Edit, Write, MultiEdit, or notebook mutation tools.",
    outputInstruction,
    "",
    `Role: ${roleName(role)}`,
    `Focus: ${rolePrompt(role)}`
  ].join("\n");
}

export function buildNativeReviewAgents(roles, { model, effort, structuredJson = false, disallowedTools } = {}) {
  const writeDenyTools = disallowedTools ?? configuredWriteDenyTools(process.env);
  const agents = {};
  for (const role of roles || []) {
    const name = nativeAgentName(role);
    const definition = {
      description: roleDescription(role),
      prompt: nativeAgentPrompt(role, { structuredJson }),
      tools: [...READ_ONLY_TOOLS],
      disallowedTools: [...writeDenyTools, ...NATIVE_PARENT_DENY_TOOLS],
      model: nativeAgentModel(model),
      maxTurns: 4
    };
    if (effort) {
      definition.effort = effort;
    }
    agents[name] = definition;
  }
  return agents;
}

export function nativeReviewTeamPrompt(roles, gitContext, focusText = "", { structuredJson = false } = {}) {
  const roleLines = (roles || [])
    .map((role) => `- ${nativeAgentName(role)}: ${roleName(role)} - ${roleDescription(role)}`)
    .join("\n");
  const focus = focusText ? `\n\nFocus:\n${focusText}` : "";
  const outputShape = structuredJson
    ? "{\"role_results\":[{\"agent\":\"cfc_role\",\"role\":\"role name\",\"result\":{\"status\":\"ok\",\"review\":{\"verdict\":\"approve\",\"summary\":\"summary\",\"findings\":[],\"next_steps\":[]},\"error\":\"\"}},{\"agent\":\"cfc_role\",\"role\":\"role name\",\"result\":{\"status\":\"failed\",\"error\":\"reason\"}}]}"
    : "{\"role_results\":[{\"agent\":\"cfc_role\",\"role\":\"role name\",\"result\":{\"status\":\"ok\",\"text\":\"summary\",\"error\":\"\"}}]}";
  const structuredReminder = structuredJson
    ? "Each result.review must be the exact JSON object returned by that role agent, not a prose summary string."
    : "Each result.text may be a concise prose summary from that role agent.";
  return [
    "You are coordinating a Claude for Codex native SDK subagent review.",
    "Invoke every listed role agent exactly once. Do not skip, duplicate, rename, or replace any listed agent.",
    "After all role agents return, respond with only valid JSON using this shape:",
    outputShape,
    structuredReminder,
    "",
    "Role agents:",
    roleLines || "- cfc_reviewer: reviewer - reviewer",
    "",
    "Git context:",
    gitContext || "(no git context provided)",
    focus
  ].join("\n");
}
