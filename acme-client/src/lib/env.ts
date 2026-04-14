import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "dotenv";

let hasLoadedLayeredEnv = false;

export function loadLayeredEnv(): void {
  if (hasLoadedLayeredEnv) return;
  hasLoadedLayeredEnv = true;

  const projectRoot = resolveProjectRoot();
  const repoRoot = resolve(projectRoot, "..");

  const shared = readEnv(resolve(repoRoot, ".env.shared"));
  const local = readEnv(resolve(projectRoot, ".env"));

  Object.assign(process.env, {
    ...shared,
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