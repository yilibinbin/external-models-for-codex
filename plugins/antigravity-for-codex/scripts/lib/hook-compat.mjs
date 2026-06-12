const SUPPORTED_HOOKS = {
  SessionStart: "Tracks session lifecycle state without invoking a model.",
  SessionEnd: "Tracks session lifecycle state without invoking a model.",
  UserPromptSubmit: "Surfaces unread background results at the next user prompt.",
  Stop: "Runs the opt-in review gate at stop time, after git context is available."
};

const UNSUPPORTED_HOOKS = {
  PreToolUse: "Not used because Antigravity for Codex does not intercept or rewrite tool calls; reviews run against git state instead.",
  PostToolUse: "Not used because post-tool review would add per-tool latency while duplicating the stop-time git review.",
  PermissionRequest: "Not used because this plugin must not grant, deny, or proxy Codex permission decisions.",
  Notification: "Not used because unread-result delivery is handled by UserPromptSubmit without unsolicited notification noise."
};

function supportedHookEntries() {
  return Object.entries(SUPPORTED_HOOKS).map(([event, behavior]) => ({
    event,
    behavior,
    failOpen: true
  }));
}

function unsupportedHookEntries() {
  return Object.entries(UNSUPPORTED_HOOKS).map(([event, reason]) => ({
    event,
    reason
  }));
}

function hookLookup(supported, unsupported) {
  return Object.fromEntries(
    [
      ...supported.map((item) => [item.event, {
        supported: true,
        behavior: item.behavior,
        failOpen: item.failOpen,
        reason: item.behavior
      }]),
      ...unsupported.map((item) => [item.event, {
        supported: false,
        reason: item.reason
      }])
    ]
  );
}

export function antigravityHookCompatibility() {
  const supported = supportedHookEntries();
  const unsupported = unsupportedHookEntries();
  const events = hookLookup(supported, unsupported);
  return {
    supportedEvents: Object.keys(SUPPORTED_HOOKS),
    unsupportedEvents: Object.keys(UNSUPPORTED_HOOKS),
    supported,
    unsupported,
    events
  };
}
