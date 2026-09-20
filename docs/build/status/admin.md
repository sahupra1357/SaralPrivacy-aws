# admin — status: static-clean
Updated: 2026-09-18
Inventory: docs/build/inventory/admin.md (14 route rows, 2 jobs, lib rows). Every row is implemented and has a test.
No test was executed. No git command that changes anything was run.

## Files created
Backend
- backend/app/models/admin.py — BloggerAccount (ops.blogger_accounts), AiCitation (ops.ai_citations), SeoRun, SeoInspection, SeoIndexRequest (0005 tables, exact columns, no mixin since they have no legacy_id/updated_at), AdminTask (NEW ops.admin_tasks: status of background runs)
- backend/app/crud/admin.py — allowlisted generic reader (text() SQL, identifiers from a fixed TARGETS map only), row→Appwrite-doc mapping, blogger/assessment/citation/seo/task queries, delete_auth_user
- backend/app/api/routes/admin.py — GET /admin/data, GET /admin/dashboard, GET/POST /admin/bloggers, PATCH/DELETE /admin/bloggers/{id}, POST /admin/send-report, GET /admin/tasks/{id}; start_task/task_session background helper
- backend/app/api/routes/aeo.py — lib/aeo port + POST /admin/aeo-panel-run (202 + task)
- backend/app/api/routes/seo.py — lib/seo port (verdict, GSC client via google-auth, sitemap, run, persistence) + POST /admin/seo-inspect (202 + task), GET /admin/seo, POST /admin/seo/index-requests
- backend/app/jobs/admin.py — run_aeo_panel, run_seo_inspect
- backend/app/tests/admin/: __init__, conftest, test_data, test_bloggers, test_send_report, test_aeo, test_seo_verdict, test_seo, test_jobs, test_crud (9 test files)

Frontend
- frontend/app/(backoffice)/admin/_lib/{backend.ts,pollTask.ts,answerSummary.ts,signOut.ts}
- frontend/app/(backoffice)/admin/seo/types.ts
- frontend/tests/unit/admin/: data-pages, answer-summary, poll-task, bloggers-client, run-buttons, server-pages, server-actions, assessments-send-report, layout (9 files)

## Files changed (frontend call sites)
- admin/{leads,subscribers,downloads,discovery,consent,consultations,survey-responses,assessments}/page.tsx → /api/proxy/api/v1/admin/data
- admin/assessments/page.tsx send-report → /api/proxy/api/v1/admin/send-report, sends answerSummary, reads `detail` first
- admin/bloggers/BloggersClient.tsx → /api/proxy/api/v1/admin/bloggers[/id], reads `detail` first; bloggers/page.tsx → adminGet
- admin/page.tsx (dashboard) → adminGet("/admin/dashboard")
- admin/citations/page.tsx → adminGet data route; citations/RunButton.tsx → start + poll
- admin/seo/page.tsx → adminGet("/admin/seo"), local types; seo/actions.ts → backend; seo/RunButton.tsx → start + poll
- admin/layout.tsx → lib/auth/session (not the shim); Sign Out now revokes the backend session and deletes `access_token` (old action deleted the dead `admin_session` cookie)
Markup/copy unchanged except the poll-timeout/failed messages that replace the Vercel-504 branch.

## Moved to _backup (also in _backup/LEDGER.md) — 14 files
webapp/app/api/admin/{data,bloggers,bloggers/[id],aeo-panel-run,seo-inspect,send-report}/route.ts,
webapp/app/api/cron/aeo-panel/route.ts, webapp/lib/aeo/{citation-detector,engines,openrouter-client,prompts,runner,types}.ts,
webapp/lib/auth/adminAuth.ts. Empty dir frontend/app/api/admin removed with rmdir.

Kept live deliberately: frontend/lib/seo/*.ts — still imported by tools/seo/inspect.ts and tools/seo/verdict.test.ts (outside my boundary).

## Needs from orchestrator
- include_router (backend/app/api/main.py):
  `from app.api.routes import admin, aeo, seo` then
  `api_router.include_router(admin.router)`, `api_router.include_router(aeo.router)`, `api_router.include_router(seo.router)`
- models import (backend/app/models/__init__.py):
  `from app.models.admin import AdminTask, AiCitation, BloggerAccount, SeoIndexRequest, SeoInspection, SeoRun  # noqa: F401` (+ add to __all__)
- jobs (backend/app/jobs/__init__.py):
  `from app.jobs import admin as admin_jobs`
  `register("aeo-panel", admin_jobs.AEO_PANEL_CRON, admin_jobs.run_aeo_panel)`  # "30 3 * * 1"
  `register("seo-inspect", admin_jobs.SEO_INSPECT_CRON, admin_jobs.run_seo_inspect, timeout_seconds=900)`  # "0 4 * * 1"
- dependencies: backend `"google-auth[requests]>=2.30,<3.0"` (GSC token; imported lazily inside seo.fetch_access_token). Frontend none.
- migration: ops.blogger_accounts, ops.ai_citations (+3 indexes) as 0001; ops.seo_runs / seo_inspections / seo_index_requests as 0005 (check constraints, unique(run_id,url), FK on delete cascade, `run_at desc` indexes); NEW ops.admin_tasks.
- backup moves outside my boundary (their replacement exists): tools/auth/provision.ts (→ /auth/invite + /auth/recover; currently the ONLY tsc error: cannot find ../../lib/auth/adminAuth.ts), tools/seo/{inspect.ts,verdict.test.ts,README.md,fixtures/}, .github/workflows/seo-inspect.yml (→ job seo-inspect), then frontend/lib/seo/*.ts (5 files, admin-owned — move together with the tools), vercel.json aeo-panel cron entry.
- root conftest `pytest_plugins = ["app.tests.auth.fixtures"]` (tests/admin/conftest.py re-imports anyway).

## Static checks (once, admin files only)
- ruff check (routes admin/aeo/seo, models/crud/jobs admin, tests/admin) → All checks passed
- python -m compileall -q backend/app → clean
- npx tsc --noEmit → only tools/auth/provision.ts (above) plus stale generated .next/types entries

## Open questions / decisions
1. Blogger on an admin route now gets 403 "Access denied." (core require_role); the TS answered 401 "Unauthorized" for both.
2. Long runs are background tasks (ALB/proxy timeouts): POST returns 202 {ok, task_id}; GET /admin/tasks/{id} returns the old payload as `result`. Stored in the new ops.admin_tasks table so it works across workers. A worker crash leaves a task "running"; the button times out after 330 s.
3. send-report: QUESTIONS is frontend data, so the page sends `answerSummary`; the backend does not re-derive it.
4. SEO: GSC key only from GSC_SERVICE_ACCOUNT_JSON (no file path fallback). "unconfigured" state on /admin/seo is unreachable; a 500 from /admin/seo shows the old "tables missing" notice. CLI flags (--dry-run, --offline, --budget, --no-db, JSON report files) not ported; `python -m app.jobs run seo-inspect` replaces the Action. renderSummary (GH step summary) not ported; job logs each run line instead.
5. Crawl-time comparisons (data sanity, diff) compare instants, not strings (GSC `Z` vs Postgres `+00:00`).
6. PATCH/DELETE on an unknown blogger id is a no-op success, as the TS effectively was. DELETE also deletes the app.users row plus its sessions/invite tokens.
7. Citations page "No data yet" help text still shows the old curl to /api/cron/aeo-panel (copy kept verbatim); should become `python -m app.jobs run aeo-panel`.
8. Blogger invite reuses login.send_link_email / set_password_url / INVITE_TTL_HOURS (24 h) and keeps its own 409 message.
