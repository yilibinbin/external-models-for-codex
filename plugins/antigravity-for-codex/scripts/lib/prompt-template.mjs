import fs from "node:fs";
import path from "node:path";

export function readPromptTemplate(pluginRoot, name) {
  const promptPath = path.join(pluginRoot, "prompts", `${name}.md`);
  try {
    return fs.readFileSync(promptPath, "utf8");
  } catch (error) {
    const message = error?.code === "ENOENT"
      ? `Prompt template not found: prompts/${name}.md`
      : `Unable to read prompt template prompts/${name}.md: ${error.message || String(error)}`;
    throw new Error(message);
  }
}
export function renderTemplate(template, values) {
  return String(template).replace(/\{\{([A-Z0-9_]+)\}\}/g, (_match, key) => {
    if (!Object.prototype.hasOwnProperty.call(values, key)) {
      throw new Error(`Missing prompt template variable: ${key}`);
    }
    return String(values[key] ?? "");
  }).replace(/\n{3,}/g, "\n\n").trim();
}
