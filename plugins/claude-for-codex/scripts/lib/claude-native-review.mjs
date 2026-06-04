const READ_ONLY_TOOLS = Object.freeze(["Read", "Grep", "Glob"]);
const WRITE_DENY_TOOLS = Object.freeze(["Edit", "Write", "MultiEdit", "Bash", "Agent"]);

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

function nativeAgentPrompt(role) {
  return [
    "You are a read-only Claude for Codex review subagent.",
    "Inspect repository files and git context only. Do not edit files, run shell commands, spawn agents, or request write-capable tools.",
    "Use only Read, Grep, and Glob when tool access is needed.",
    "Return concise findings for your assigned role as JSON-compatible content.",
    "",
    `Role: ${roleName(role)}`,
    `Focus: ${rolePrompt(role)}`
  ].join("\n");
}

export function buildNativeReviewAgents(roles, { model, effort } = {}) {
  const agents = {};
  for (const role of roles || []) {
    const name = nativeAgentName(role);
    const definition = {
      description: roleDescription(role),
      prompt: nativeAgentPrompt(role),
      tools: [...READ_ONLY_TOOLS],
      disallowedTools: [...WRITE_DENY_TOOLS],
      permissionMode: "dontAsk",
      maxTurns: 4
    };
    if (model) {
      definition.model = model;
    }
    if (effort) {
      definition.effort = effort;
    }
    agents[name] = definition;
  }
  return agents;
}

export function nativeReviewTeamPrompt(roles, gitContext, focusText = "") {
  const roleLines = (roles || [])
    .map((role) => `- ${nativeAgentName(role)}: ${roleName(role)} - ${roleDescription(role)}`)
    .join("\n");
  const focus = focusText ? `\n\nFocus:\n${focusText}` : "";
  return [
    "You are coordinating a Claude for Codex native SDK subagent review.",
    "Invoke every listed role agent exactly once. Do not skip, duplicate, rename, or replace any listed agent.",
    "After all role agents return, respond with only valid JSON using this shape:",
    "{\"role_results\":[{\"agent\":\"cfc_role\",\"role\":\"role name\",\"result\":{}}]}",
    "",
    "Role agents:",
    roleLines || "- cfc_reviewer: reviewer - reviewer",
    "",
    "Git context:",
    gitContext || "(no git context provided)",
    focus
  ].join("\n");
}
