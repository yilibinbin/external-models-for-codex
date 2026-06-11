export const CODEX_DISCOVERED_HOOK_EVENTS = Object.freeze([
  "SessionStart",
  "SessionEnd",
  "UserPromptSubmit",
  "Stop"
]);

export const CLAUDE_KNOWN_HOOK_EVENTS = Object.freeze([
  "Setup",
  "SessionStart",
  "UserPromptSubmit",
  "UserPromptExpansion",
  "PreToolUse",
  "PermissionRequest",
  "PermissionDenied",
  "PostToolUse",
  "PostToolUseFailure",
  "PostToolBatch",
  "Notification",
  "MessageDisplay",
  "SubagentStart",
  "SubagentStop",
  "TaskCreated",
  "TaskCompleted",
  "Stop",
  "StopFailure",
  "TeammateIdle",
  "InstructionsLoaded",
  "ConfigChange",
  "CwdChanged",
  "FileChanged",
  "WorktreeCreate",
  "WorktreeRemove",
  "PreCompact",
  "PostCompact",
  "Elicitation",
  "ElicitationResult",
  "SessionEnd"
]);

export function decisionShapeForEvent(eventName) {
  if (eventName === "Stop" || eventName === "SubagentStop" || eventName === "TeammateIdle") {
    return { shape: "top-level-decision", fields: ["decision", "reason"] };
  }
  if (eventName === "PreToolUse" || eventName === "PermissionRequest") {
    return {
      shape: "hook-specific-permission",
      fields: ["hookSpecificOutput.permissionDecision", "hookSpecificOutput.permissionDecisionReason"]
    };
  }
  return { shape: "event-specific", fields: ["hookSpecificOutput"] };
}

export function hookCompatibilityReport({ installedEvents = CODEX_DISCOVERED_HOOK_EVENTS } = {}) {
  const unsupportedInstalledEvents = installedEvents.filter((event) => !CLAUDE_KNOWN_HOOK_EVENTS.includes(event));
  return {
    codexSubset: installedEvents.every((event) => CODEX_DISCOVERED_HOOK_EVENTS.includes(event)),
    supportedEvents: [...CODEX_DISCOVERED_HOOK_EVENTS],
    knownClaudeEvents: [...CLAUDE_KNOWN_HOOK_EVENTS],
    installedEvents: [...installedEvents],
    unsupportedInstalledEvents,
    decisionShapes: Object.fromEntries(CLAUDE_KNOWN_HOOK_EVENTS.map((event) => [event, decisionShapeForEvent(event)]))
  };
}
