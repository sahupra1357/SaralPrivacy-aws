// Export the typed knowledge the FastAPI chat module needs into backend/app/data/.
//
// The authoring source stays in TypeScript — lib/chat/site-routing.ts is still the
// only place link targets are declared, components/glossary/glossaryData.ts is still
// the only place DPDPA terms are written, and lib/data/compliance-checklist.ts is
// still the checklist. The Python side never re-authors any of it; it reads these
// generated files, exactly as it reads the generated public/chat-index.json.
//
// Run after editing any of those three files, or after `npm run build:chat-index`:
//
//   node --experimental-strip-types scripts/export-chat-kb.mjs
//
// Writes (all git-tracked, all generated — never hand-edit):
//   backend/app/data/site-routes.json
//   backend/app/data/glossary.json
//   backend/app/data/checklist.json
//   backend/app/data/chat-index.json   (copy of public/chat-index.json)

import { copyFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, "..");
const out = join(frontend, "..", "backend", "app", "data");

const routing = await import("../lib/chat/site-routing.ts");
const glossary = await import("../components/glossary/glossaryData.ts");
const checklist = await import("../lib/data/compliance-checklist.ts");

mkdirSync(out, { recursive: true });

writeFileSync(
  join(out, "site-routes.json"),
  JSON.stringify({
    routes: routing.ROUTES,
    excludeFromAuthority: routing.EXCLUDE_FROM_AUTHORITY,
    neverSurfacePrefixes: routing.NEVER_SURFACE_PREFIXES,
    industrySlugs: routing.INDUSTRY_SLUGS,
  })
);

writeFileSync(join(out, "glossary.json"), JSON.stringify(glossary.TERMS));

writeFileSync(
  join(out, "checklist.json"),
  JSON.stringify(
    [...checklist.part1Sections, ...checklist.part2Sections].map((s) => ({
      part: s.part,
      sectionId: s.sectionId,
      title: s.title,
      items: s.items.map((it) => ({
        id: it.id,
        subsection: it.subsection,
        requirement: it.requirement,
        guardrail: it.guardrail,
        reference: it.reference,
      })),
    }))
  )
);

copyFileSync(join(frontend, "public", "chat-index.json"), join(out, "chat-index.json"));

console.log(
  `exported: ${routing.ROUTES.length} routes, ${glossary.TERMS.length} glossary terms, ` +
    `${checklist.part1Sections.length + checklist.part2Sections.length} checklist sections`
);
