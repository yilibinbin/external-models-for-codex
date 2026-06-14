import { configuredWriteDenyTools } from "./claude-backend.mjs";
import { normalizeModelSelection } from "./model-registry.mjs";
import { DEFAULT_MULTI_REVIEW_ROLES, REVIEW_ROLES } from "./role-packs.mjs";

const READ_ONLY_TOOLS = Object.freeze(["Read", "Grep", "Glob"]);
const NESTED_AGENT_DENY = "Agent";
const PROFILE_NAME_PATTERN = /^[a-z][a-z0-9-]{0,63}$/;
const DEFAULT_NATIVE_ROLE_ORDER = DEFAULT_MULTI_REVIEW_ROLES;
const DEFAULT_SDK_AGENT_MAX_TURNS = 4;
const STRONG_SDK_AGENT_MAX_TURNS = 8;
const MAX_SDK_AGENT_MAX_TURNS = 64;
const ABSOLUTE_SDK_AGENT_MAX_TURNS = 64;
const TOOL_GRANT_FIELDS = Object.freeze([
  "tools",
  "tool",
  "allowedtool",
  "allowedtools",
  "allowed_tool",
  "allowed_tools",
  "allowed-tool",
  "allowed-tools"
]);
const FORBIDDEN_FRONTMATTER_TOOLS = Object.freeze([
  "Agent",
  "Task",
  "Workflow",
  "Bash",
  "Edit",
  "Write",
  "MultiEdit",
  "NotebookEdit",
  "TodoWrite",
  "WebFetch"
]);

function roleDirective(roleName) {
  const role = REVIEW_ROLES[roleName];
  if (!role) {
    throw new Error(`Unknown native review role "${roleName}".`);
  }
  return role.directive;
}

function sdkAgentNameForRole(roleName) {
  return `cfc_${String(roleName).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/_+/g, "_")}`;
}

function markdownAgentNameForRole(roleName) {
  return `${sdkAgentNameForRole(roleName)}_reviewer`;
}

function descriptionForRole(roleName) {
  return `Claude for Codex ${roleName} read-only reviewer.`;
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

function nativeAgentPrompt(profile, { structuredJson = false, maxTurns } = {}) {
  const outputContract = structuredJson
    ? structuredReviewContract()
    : "Return concise findings for your assigned role as JSON-compatible content.";
  const budgetInstruction = maxTurns
    ? `You have at most ${maxTurns} tool turns. Inspect the highest-risk files first, stop investigating before the final turn, and reserve your final response for the required verdict.`
    : "";
  return [
    "You are a read-only Claude for Codex native review agent.",
    "You run in a fresh isolated context and must inspect only repository files, git context, and prompt-provided plan text.",
    "Do not edit files, run shell commands, spawn agents, invoke workflow tools, or request write-capable tools.",
    "Use only Read, Grep, and Glob when tool access is needed.",
    "Do not invoke Agent, Task, Workflow, Bash, Edit, Write, MultiEdit, or notebook mutation tools.",
    ...(budgetInstruction ? [budgetInstruction] : []),
    outputContract,
    "",
    `Role: ${profile.role}`,
    `Focus: ${profile.directive}`
  ].join("\n");
}

function boundedPositiveInteger(value, fallback) {
  const numeric = Number(value);
  if (!Number.isInteger(numeric) || numeric <= 0) {
    return fallback;
  }
  return Math.min(numeric, ABSOLUTE_SDK_AGENT_MAX_TURNS);
}

function sdkAgentMaxTurns({ maxTurns, quality, qualityPolicy } = {}) {
  if (maxTurns !== undefined) {
    return boundedPositiveInteger(maxTurns, DEFAULT_SDK_AGENT_MAX_TURNS);
  }
  const resolvedQuality = String(qualityPolicy?.resolvedQuality || quality || "").trim().toLowerCase();
  if (resolvedQuality === "max") {
    return MAX_SDK_AGENT_MAX_TURNS;
  }
  if (resolvedQuality === "strong") {
    return STRONG_SDK_AGENT_MAX_TURNS;
  }
  return DEFAULT_SDK_AGENT_MAX_TURNS;
}

function frontmatterValue(value) {
  return JSON.stringify(String(value ?? ""));
}

function frontmatterFromMarkdown(markdown) {
  const text = String(markdown ?? "");
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  return match ? match[1] : null;
}

function frontmatterField(frontmatter, name) {
  const match = frontmatter.match(new RegExp(`^${name}:\\s*(.*?)\\r?$`, "m"));
  return match ? match[1].trim() : "";
}

function frontmatterEntries(frontmatter) {
  return String(frontmatter ?? "")
    .split(/\r?\n/)
    .map((line) => {
      const match = line.match(/^([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*$/);
      if (!match) {
        return null;
      }
      return {
        key: match[1],
        normalizedKey: match[1].toLowerCase(),
        value: match[2]
      };
    })
    .filter(Boolean);
}

function expectedMarkdownNameForFile(fileName) {
  const normalized = String(fileName ?? "").replaceAll("\\", "/").split("/").pop().replaceAll("_", "-");
  if (!/^cfc-[a-z0-9-]+-reviewer\.md$/.test(normalized)) {
    return "";
  }
  return normalized.slice(0, -".md".length).replaceAll("-", "_");
}

export function nativeAgentProfile(roleName) {
  if (typeof roleName !== "string" || !PROFILE_NAME_PATTERN.test(roleName)) {
    throw new Error(`Invalid native review role "${roleName}".`);
  }
  return {
    role: roleName,
    agentName: sdkAgentNameForRole(roleName),
    markdownAgentName: markdownAgentNameForRole(roleName),
    description: descriptionForRole(roleName),
    directive: roleDirective(roleName),
    tools: [...READ_ONLY_TOOLS]
  };
}

export function nativeAgentProfiles(roleNames = DEFAULT_NATIVE_ROLE_ORDER) {
  return roleNames.map((roleName) => nativeAgentProfile(roleName));
}

export function renderNativeAgentMarkdown(profile) {
  const validated = nativeAgentProfile(profile.role);
  return [
    "---",
    `name: ${validated.markdownAgentName}`,
    `description: ${frontmatterValue(validated.description)}`,
    "tools: Read, Grep, Glob",
    "---",
    "",
    nativeAgentPrompt(validated, { structuredJson: true }),
    ""
  ].join("\n");
}

export function sdkAgentsFromNativeProfiles(profiles, { model, effort, disallowedTools, structuredJson = false, quality, qualityPolicy, maxTurns } = {}) {
  const writeDenyTools = disallowedTools ?? configuredWriteDenyTools(process.env);
  const nativeDenyTools = Array.from(new Set([...writeDenyTools, "Bash", "Edit", "Write", "MultiEdit", NESTED_AGENT_DENY]));
  const agentMaxTurns = sdkAgentMaxTurns({ maxTurns, quality, qualityPolicy });
  const agents = {};
  for (const profile of profiles || []) {
    const validated = nativeAgentProfile(profile.role);
    agents[validated.agentName] = {
      description: validated.description,
      prompt: nativeAgentPrompt(validated, { structuredJson, maxTurns: agentMaxTurns }),
      tools: [...READ_ONLY_TOOLS],
      disallowedTools: nativeDenyTools,
      model: nativeAgentModel(model),
      maxTurns: agentMaxTurns
    };
    if (effort) {
      agents[validated.agentName].effort = effort;
    }
  }
  return agents;
}

export function validateNativeAgentMarkdown(markdown, fileName = "") {
  const errors = [];
  const frontmatter = frontmatterFromMarkdown(markdown);
  if (frontmatter === null) {
    errors.push("missing complete frontmatter");
  } else {
    const entries = frontmatterEntries(frontmatter);
    const toolGrantEntries = entries.filter((entry) => TOOL_GRANT_FIELDS.includes(entry.normalizedKey));
    const name = frontmatterField(frontmatter, "name");
    const toolsEntries = toolGrantEntries.filter((entry) => entry.normalizedKey === "tools");
    const alternateToolGrantEntries = toolGrantEntries.filter((entry) => entry.normalizedKey !== "tools");
    if (!/^cfc_[a-z0-9_]+_reviewer$/.test(name)) {
      errors.push("missing cfc agent name");
    }
    if (fileName) {
      const expectedName = expectedMarkdownNameForFile(fileName);
      if (!expectedName) {
        errors.push(`unexpected native agent file name ${fileName}`);
      } else if (name && name !== expectedName) {
        errors.push(`frontmatter name ${name} does not match file name ${fileName}`);
      }
    }
    if (toolsEntries.length !== 1) {
      errors.push("frontmatter must contain exactly one tools field");
    }
    if (alternateToolGrantEntries.length > 0) {
      errors.push(`frontmatter contains alternate tool grant field ${alternateToolGrantEntries[0].key}`);
    }
    if (toolsEntries[0]?.value !== "Read, Grep, Glob") {
      errors.push("tools must be exactly Read, Grep, Glob");
    }
    for (const forbidden of FORBIDDEN_FRONTMATTER_TOOLS) {
      const forbiddenTool = new RegExp(`(^|[,\\s])${forbidden}([,\\s]|$)`, "i");
      if (forbiddenTool.test(frontmatter)) {
        errors.push(`frontmatter grants forbidden tool ${forbidden}`);
      }
    }
  }
  return { ok: errors.length === 0, errors };
}
