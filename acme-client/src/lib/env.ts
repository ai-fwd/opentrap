import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "dotenv";

let hasLoadedProjectEnv = false;

export function loadProjectEnv(): void {
  if (hasLoadedProjectEnv) return;
  hasLoadedProjectEnv = true;

  const projectRoot = resolveProjectRoot();
  const local = readEnv(resolve(projectRoot, ".env"));

  Object.assign(process.env, {
    ...local,
    ...process.env,
  });
}

function readEnv(filePath: string): Record<string, string> {
  if (!existsSync(filePath)) {
    return {};
  }

  return parse(readFileSync(filePath, "utf-8"));
}

function resolveProjectRoot(): string {
  const cwd = process.cwd();

  if (existsSync(resolve(cwd, "package.json"))) {
    return cwd;
  }

  const nested = resolve(cwd, "acme-client");
  if (existsSync(resolve(nested, "package.json"))) {
    return nested;
  }

  return cwd;
}
