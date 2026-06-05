import fs from "node:fs";
import path from "node:path";

export function loadPromptTemplate(rootDir, name) {
  const filePath = path.join(rootDir, "prompts", `${name}.md`);
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch (error) {
    throw new Error(`Unable to load prompt template "${name}": ${error.message || String(error)}`);
  }
}

export function renderPromptTemplate(template, values) {
  return template.replace(/\{\{([A-Z0-9_]+)\}\}/g, (match, key) => {
    if (!Object.hasOwn(values, key)) {
      throw new Error(`Missing prompt template value: ${key}`);
    }
    return String(values[key] ?? "");
  });
}
