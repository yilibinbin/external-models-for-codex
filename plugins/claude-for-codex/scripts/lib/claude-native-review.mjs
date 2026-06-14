import {
  nativeAgentProfiles,
  sdkAgentsFromNativeProfiles
} from "./native-agent-profiles.mjs";

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

export function nativeAgentName(role) {
  const sanitized = roleName(role)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_");
  return `cfc_${sanitized || "reviewer"}`;
}

export function buildNativeReviewAgents(roles, options = {}) {
  const profiles = nativeAgentProfiles((roles || []).map((role) => roleName(role)));
  return sdkAgentsFromNativeProfiles(profiles, options);
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
