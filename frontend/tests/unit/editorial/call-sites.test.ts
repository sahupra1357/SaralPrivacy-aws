/**
 * Migration guard: editorial surfaces call the backend through the core proxy, never the
 * retired /api/blog/* and /api/briefings/* route handlers or /api/admin/data.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = path.resolve(__dirname, "../../..");
const read = (p: string) => readFileSync(path.join(ROOT, p), "utf8");

describe("editorial call sites", () => {
  it("BlogEditor posts validate/revise/infographic/save to the proxy", () => {
    const src = read("components/admin/BlogEditor.tsx");
    for (const r of ["validate", "revise", "infographic", "save"]) {
      expect(src).toContain(`fetch("/api/proxy/api/v1/blog/${r}"`);
      expect(src).not.toContain(`fetch("/api/blog/${r}"`);
    }
  });

  it("admin list pages no longer use /api/admin/data or /api/briefings/*", () => {
    for (const f of ["app/(backoffice)/admin/briefings/page.tsx", "app/(backoffice)/admin/blog/page.tsx"]) {
      const src = read(f);
      expect(src).not.toContain('fetch("/api/admin/data');
      expect(src).not.toContain('fetch("/api/briefings/');
    }
  });

  it("server data sources read the backend, not lib/db", () => {
    for (const f of [
      "lib/data/briefings-archive.ts",
      "lib/data/briefings-source.ts",
      "app/[locale]/briefings/[slug]/page.tsx",
      "app/[locale]/blog/page.tsx",
      "app/[locale]/blog/[slug]/page.tsx",
      "app/sitemap.ts",
    ]) {
      const src = read(f);
      expect(src).toContain("@/lib/api");
      expect(src).not.toMatch(/from ["']@\/lib\/db["']/);
    }
  });
});
