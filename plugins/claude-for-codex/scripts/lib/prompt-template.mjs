import fs from "node:fs";
import path from "node:path";

export function loadPromptTemplate(rootDir, name) {
  const promptPath = path.join(rootDir, "prompts", `${name}.md`);
  try {
    return fs.readFileSync(promptPath, "utf8");
  } catch (error) {
    const message = error?.code === "ENOENT"
      ? `Prompt template not found: prompts/${name}.md`
      : `Unable to read prompt template prompts/${name}.md: ${error.message || String(error)}`;
    throw new Error(message);
  }
}

export function interpolateTemplate(template, variables) {
  return template.replace(/\{\{([A-Z0-9_]+)\}\}/g, (_match, key) => {
    if (!Object.prototype.hasOwnProperty.call(variables, key)) {
      throw new Error(`Missing prompt template variable: ${key}`);
    }
    return String(variables[key] ?? "");
  });
}

export function renderPromptTemplate(rootDir, name, variables) {
  return interpolateTemplate(loadPromptTemplate(rootDir, name), variables)
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
