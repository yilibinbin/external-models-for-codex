const DEFAULT_MULTI_REVIEW_ROLES = Object.freeze([
  "correctness",
  "security",
  "tests",
  "release",
  "adversarial"
]);

export const REVIEW_ROLES = Object.freeze({
  correctness: "Find bugs, regressions, edge cases, and behavioral contract breaks.",
  security: "Review read-only safety, secrets exposure, injection risks, and unsafe command or path handling.",
  tests: "Find missing, brittle, or overfit tests and release validation gaps.",
  release: "Review install, marketplace, versioning, documentation, and upgrade risks.",
  adversarial: "Challenge assumptions, simpler alternatives, hidden costs, and failure modes."
});

export const BUILT_IN_ROLE_PACKS = Object.freeze({
  default: Object.freeze({
    description: "Default Antigravity multi-review team.",
    roles: DEFAULT_MULTI_REVIEW_ROLES
  }),
  security: Object.freeze({
    description: "Security-focused Antigravity review team.",
    roles: Object.freeze(["security", "correctness", "adversarial"])
  }),
  release: Object.freeze({
    description: "Release-readiness Antigravity review team.",
    roles: Object.freeze(["release", "tests", "correctness", "security"])
  })
});

function roleObject(name) {
  if (!Object.hasOwn(REVIEW_ROLES, name)) {
    const validRoles = Object.keys(REVIEW_ROLES).sort().join(", ");
    throw new Error(`Unknown review role "${name}". Valid roles: ${validRoles}.`);
  }
  return { name, brief: REVIEW_ROLES[name] };
}

export function resolveRoles({ roles, rolePack } = {}) {
  if (roles?.length && rolePack) {
    throw new Error("--role-pack conflicts with --roles.");
  }
  if (rolePack) {
    const packName = String(rolePack).trim();
    if (!Object.hasOwn(BUILT_IN_ROLE_PACKS, packName)) {
      const validPacks = Object.keys(BUILT_IN_ROLE_PACKS).sort().join(", ");
      throw new Error(`Unknown role pack "${packName}". Valid packs: ${validPacks}.`);
    }
    return BUILT_IN_ROLE_PACKS[packName].roles.map(roleObject);
  }
  return (roles?.length ? roles : DEFAULT_MULTI_REVIEW_ROLES).map(roleObject);
}
