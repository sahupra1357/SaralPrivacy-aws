# editorial — status: static-clean
Updated: 2026-09-18

Inventory: `docs/build/inventory/editorial.md` (13 original routes + 6 new read endpoints,
1 cron, 2 manual CLIs; every row has a test). No test run. No mutating git (one read-only
`git show HEAD:` to recover the original revalidate route for `_backup/`).

## Files created
Backend
- app/models/editorial.py — `Briefing` (app.briefings_meta), `BlogPost` (ops.blog_posts); columns exactly as 0001
- app/crud/editorial.py
- app/api/routes/briefings.py — /briefings (public list), /briefings/by-slug/{slug}, /briefings/admin/all,
  GET+POST /briefings/generate, GET /briefings/approve, POST /briefings/send, DELETE /briefings/delete, GET /briefings/today
- app/api/routes/blog.py — /blog (public list), /blog/by-slug/{slug}, /blog/admin/all, POST+PATCH /blog/save,
  /blog/validate, /blog/revise, /blog/infographic, GET /blog/{id}
- app/editorial/{__init__,config,docs,taxonomy,briefings,emails,blog_ai,infographic}.py, app/editorial/fonts/Inter-{Regular,Bold}.ttf (copied)
- app/editorial/pipeline/{__init__,roadmap,research,content,image,publish}.py
- app/jobs/editorial.py — `run_daily_briefing`, `run_pipeline`, `backfill_taxonomy`, `revert_taxonomy`, CLI
- app/email-templates/editorial/briefing_approval.html (extends forms/_base.html)
- app/tests/editorial/{__init__,conftest,test_crud,test_briefings_routes,test_blog_routes,test_logic,test_pipeline}.py

Frontend
- tests/unit/editorial/{admin-pages.test.tsx,api-revalidate.test.ts,data-sources.test.ts,blog-edit-page.test.tsx,call-sites.test.ts}

## Files changed (frontend call sites etc.)
- app/api/revalidate/route.ts — GET kept; POST `{tag}|{path}` with Bearer CRON_SECRET added (what services/revalidate.py calls)
- lib/data/briefings-archive.ts, lib/data/briefings-source.ts — apiGet `/briefings` (tag briefings, 3600)
- app/[locale]/briefings/[slug]/page.tsx — apiGet by-slug + related pool (tag briefings, 1800)
- app/[locale]/blog/page.tsx, app/[locale]/blog/[slug]/page.tsx — apiGet `/blog`, `/blog/by-slug` (tag blog-posts)
- app/sitemap.ts — getBlogSlugs via apiGet `/blog?order=updated` (only that function)
- app/(backoffice)/admin/briefings/page.tsx, admin/blog/page.tsx — proxy paths (admin/all, send, generate)
- app/(backoffice)/admin/blog/[id]/edit/page.tsx — apiGet with the editor's bearer; lib/auth/session instead of the adminSession shim
- app/(backoffice)/admin/blog/new/page.tsx — lib/auth/session instead of the shim
- components/admin/BlogEditor.tsx — 4 fetch URLs → /api/proxy/api/v1/blog/* (no markup change)
- lib/content-freshness.ts — kept unchanged (pure constants used by sitemap/Byline)
- home BriefingsSection needs no change (reads getArchive)

## Moved to _backup (also in _backup/LEDGER.md)
- webapp/app/api/briefings/{approve,delete,generate,send,today}/route.ts
- webapp/app/api/blog/{[id],infographic,revise,save,validate}/route.ts
- webapp/app/api/revalidate/route.ts — PARTIAL (original from HEAD, live file edited)
- tools/{read_roadmap,research,generate_content,generate_infographic,publish_to_webapp,briefing_taxonomy,backfill_briefing_taxonomy}.py
- run_pipeline.sh, .github/workflows/daily-briefing.yml, .github/workflows/backfill-taxonomy.yml

## Needs from orchestrator
- include_router (backend/app/api/main.py): add `blog, briefings` to the routes import and
  `api_router.include_router(briefings.router)` / `api_router.include_router(blog.router)`
- models import: `from app.models.editorial import BlogPost, Briefing  # noqa: F401`
- jobs (app/jobs/__init__.py):
  `from app.jobs.editorial import run_daily_briefing`
  `register("editorial_daily_briefing", "30 3 * * *", run_daily_briefing, timeout_seconds=900)`
- settings (core/config.py + .env.example; code reads them with defaults via app/editorial/config.py until added):
  `BRIEFING_CRON_SECRET: str = ""`, `GOOGLE_SHEET_ID: str = ""`, `GOOGLE_CREDENTIALS_JSON: str = ""`,
  `GOOGLE_CREDENTIALS_PATH: str = ""`, `ROADMAP_CSV_PATH: str = ""`, `SERP_API_KEY: str = ""`,
  `KIE_API_KEY: str = ""`, `NANO_BANANA_MODEL: str = "nano-banana-2"`, `CLAUDE_MODEL: str = "claude-sonnet-4-6"`,
  `CLAUDE_MAX_TOKENS: int = 4096`, `CLAUDE_TEMPERATURE: float = 0.3`
- dependencies: backend `gspread>=6`, `google-auth>=2.30` (lazy-imported; Pillow already via qrcode[pil];
  Playwright Chromium reused for infographic PNGs). Frontend: remove `@resvg/resvg-js`; next.config.ts
  `outputFileTracingIncludes["/api/blog/infographic"]` entry can go, then `frontend/lib/fonts/` → _backup.
- **REQUIRED next.config.ts rewrite** (external callers keep old URLs: n8n GETs `saralprivacy.com/api/briefings/today`
  with Bearer secret; admin approval emails in inboxes link to `/api/briefings/approve`; the proxy route drops
  both the incoming Authorization header and the Location header, so it cannot serve these):
  `{ source: "/api/briefings/:path*", destination: `${process.env.BACKEND_URL}/api/v1/briefings/:path*` }`
- test root conftest: the editorial conftest re-exports the auth fixtures, fine either way.

## Open questions
- `ops.ai_citations`: the admin module already defines `app.models.admin.AiCitation`; not duplicated here.
- Subscriber broadcast for approve/send reuses `app.jobs.outreach.{eligible_subscribers, send_briefing_to_subscribers}`
  (per orchestrator). The old `/api/briefings/send` emailed the first 1000 subscriber rows with no suppression
  filter; it now uses the eligible (suppression-filtered) audience like approve.
- `GET /blog/{id}` had no auth before (drafts readable by anyone); now admin/blogger. Only caller is the edit page.
- Bloggers can still publish via the save API (UI-only restriction before) — preserved; decide if the backend should block.
- Delete route accepts `BRIEFING_CRON_SECRET`, falling back to `CRON_SECRET` when the former is unset.
- Auto-generate path: model `claude-opus-4-5` kept verbatim; the opening role line is sent as `system` because
  `llm.complete` always sends one (text otherwise identical).
- Blog validator: AI SDK structured output replaced by a JSON-schema instruction appended to the user message + Pydantic validation.
- KIE failure: the old SVG fallback was never uploaded, so the briefing now just publishes without an image (same visible result).
- Standalone root tools not part of the scheduled pipeline stay live: tools/{send_email,send_confirmation,send_user_guide,html_builder,log_entry,utils}.py, requirements.txt, roadmap/*.csv, workflows/n8n_*.json.
- Pipeline CSV fallback needs `ROADMAP_CSV_PATH` pointing at a file mounted into the worker (roadmap/ is at the repo root).
- Admin briefings page copy still says "Vercel Cron triggers GET /api/briefings/generate" (copy unchanged by rule).
- Static checks: ruff + compileall clean on editorial files; `tsc` shows only stale `.next/types/validator.ts` references to moved routes (regenerated on next build).
