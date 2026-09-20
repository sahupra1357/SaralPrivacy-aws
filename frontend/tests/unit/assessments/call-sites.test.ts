/**
 * Migration guard: no assessment surface may call the retired Next.js route handler.
 * The 13 browser call sites in the inventory go through the core proxy instead.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const ASSESSMENT_DIR = path.resolve(__dirname, "../../../app/[locale]/assessment");
const PROXY_PATH = "/api/proxy/api/v1/assessments";

function tsxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) return tsxFiles(full);
    return entry.endsWith(".tsx") ? [full] : [];
  });
}

const submitters = tsxFiles(ASSESSMENT_DIR).filter((f) =>
  readFileSync(f, "utf8").includes("fetch(")
);

describe("assessment call sites", () => {
  it("finds all 13 submitting clients", () => {
    expect(submitters).toHaveLength(13);
  });

  it.each(submitters.map((f) => [path.basename(f), f]))("%s posts to the proxy", (_name, file) => {
    const source = readFileSync(file, "utf8");

    expect(source).toContain(`fetch("${PROXY_PATH}"`);
    expect(source).not.toContain('fetch("/api/assessment"');
  });

  it("no longer ships the retired route handler", () => {
    const retired = path.resolve(__dirname, "../../../app/api/assessment/route.ts");

    expect(() => statSync(retired)).toThrow();
  });
});
