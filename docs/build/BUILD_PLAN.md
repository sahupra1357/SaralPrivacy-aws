# Rebuild Plan — Next.js frontend, FastAPI backend, Postgres

Status: approved 2026-09-17. No application code changed yet.
Owner: orchestrator (main Claude Code session). Progress: `MODULE_STATUS.md`.

## 1. Goal

Split today's single Next.js deployment (`webapp/`) into `frontend/` (Next.js, pages
only), `backend/` (FastAPI + SQLModel + Alembic + APScheduler) and Postgres 16, run
with Docker Compose and Make, with **no functionality lost**, every superseded file
preserved under `_backup/`, and a unit test for every route, service, job, page and
component. Auth is rebuilt with no third-party identity or rate-limit service.

**Git rule:** the whole rebuild is uncommitted work in the working tree. No branches,
worktrees, commits, `git mv`, or stashes until the user has manually tested the running
app (wave 5). HEAD on `main` is the untouched fallback.

## 2. Architecture

```
browser ──▶ frontend (Next.js :3000)
              ├─ pages, next-intl, ISR
              ├─ app/api/auth/*            cookie handling only
              └─ app/api/proxy/[...path]   ──▶ backend (FastAPI :8000, /api/v1)
                                                 ├─ routes/<module>.py
                                                 ├─ services: storage(MinIO/S3), email(SMTP), llm(Anthropic), pdf(Playwright)
                                                 └─ Postgres 16 (:5432, host 5434)
            worker (same image, `python -m app.jobs`) ──▶ crons + daily briefing pipeline
```

## 3. Module map and file boundaries

Each module owns exactly these paths. Nothing else may be edited by its builder.

| Module | Backend owns | Replaces (→ `_backup/`) | Frontend owns |
|---|---|---|---|
| **core** (wave 0, orchestrator) | `app/core/*`, `app/api/deps.py`, `app/services/*`, `app/models/base.py`, `app/jobs/registry.py`, `app/api/routes/utils.py`, tests for each | `webapp/lib/{db,appwrite.ts,abuseGuard.ts,email.ts,resendClient.ts,sendGateway.ts}` (moved when the last consumer is gone) | `app/api/proxy/[...path]`, `src/client/`, `lib/api.ts` (server-side fetch helper), `tests/unit/core/` |
| **auth** | `routes/login.py`, `routes/mfa.py`, `routes/users.py`, `models/auth.py`, `crud/auth.py`, `core/security.py` (fill in), `tests/auth/` | `webapp/app/api/admin/{login,mfa,set-password}`, `webapp/lib/auth/*`, `webapp/lib/adminSession.ts` | `app/api/auth/*`, `proxy.ts` (auth section only), `app/(backoffice)/admin/login`, `admin/set-password`, `tests/unit/auth/` |
| **forms** | `routes/forms.py`, `models/forms.py`, `crud/forms.py`, `tests/forms/` | `api/{contact,subscribe,subscribers/unsubscribe,survey/submit,template-download,templates/download,white-paper}`, `lib/{subscribers,suppression,templates}` | call sites in the matching components, `tests/unit/forms/` |
| **assessments** | `routes/assessments.py`, `models/assessments.py`, `crud/assessments.py`, `tests/assessments/` | `api/assessment`, server parts of `lib/data/industry-assessment` | `app/[locale]/assessment/**`, `app/report/[token]`, `tests/unit/assessments/` |
| **notices** | `routes/notices.py`, `models/notices.py`, `crud/notices.py`, `services/pdf.py` usage, `tests/notices/` | `api/notice/{capture,events,pdf}`, `lib/notice-pack` (server parts) | notice-pack components, `tests/unit/notices/` |
| **editorial** | `routes/briefings.py`, `routes/blog.py`, `models/editorial.py`, `crud/editorial.py`, `jobs/editorial.py` (daily pipeline), `tests/editorial/` | `api/briefings/*`, `api/blog/*`, `api/revalidate`, `lib/content-freshness.ts`, root `tools/*.py`, `run_pipeline.sh` | briefings/blog pages' data fetching, `admin/briefings`, `admin/blog`, `tests/unit/editorial/` |
| **outreach** | `routes/outreach.py`, `routes/webhooks.py`, `models/outreach.py`, `crud/outreach.py`, `jobs/outreach.py`, `tests/outreach/` | `api/outreach/*`, `api/cron/{outreach-send,briefing-send}`, `api/webhooks/resend`, `lib/email-templates.ts` | outreach admin pages, `tests/unit/outreach/` |
| **chat** | `routes/chat.py`, `services/llm.py` usage, `services/retrieval.py`, `models/chat.py`, `tests/chat/` | `api/chat/*`, `lib/chat/*` (server parts; keep pure client helpers) | chat widget data layer, `tests/unit/chat/` |
| **admin** | `routes/admin.py`, `routes/seo.py`, `routes/aeo.py`, `jobs/aeo.py`, `models/admin.py`, `tests/admin/` | `api/admin/{data,bloggers,aeo-panel-run,seo-inspect,send-report}`, `api/cron/aeo-panel`, `lib/{aeo,seo}` | `app/(backoffice)/admin/**` except login/blog/briefings, `tests/unit/admin/` |

Shared files edited **only by the orchestrator**: `backend/app/api/main.py`,
`backend/app/models/__init__.py`, `backend/app/jobs/__init__.py`, Alembic versions,
`backend/pyproject.toml`, `frontend/package.json`, `frontend/next.config.ts`,
compose files, `Makefile`, `.env.example`, `MODULE_STATUS.md`.
Each builder reports through its own file `docs/build/status/<module>.md` (created by
the builder), so parallel builders never write the same file.

## 4. Waves

| Wave | Work | Parallel? |
|---|---|---|
| 0 | `mv webapp frontend` (plain move, no git); compose skeleton; backend scaffold; core module; schema bootstrap; contracts in `deps.py` and `services/` | No. One agent or the orchestrator. Everything else depends on it. |
| 1 | auth, forms, assessments, notices, chat | Yes, one builder each, same working tree, disjoint paths |
| 2 | editorial, outreach, admin | Yes. Start after wave 1 is integrated so they build on real `require_role` and email service |
| 3 | orchestrator: registrations, migrations, `make client`, data export handled by the user, remove supabase-js/node-appwrite/@vercel/*, Alpine frontend image | No |
| 4 | `/verify-build` once; fix; second `/verify-build`; write `docs/build/MANUAL_TEST_CHECKLIST.md` from the inventories | No |
| 5 | **User** runs `make up` and walks the manual checklist page by page and route by route against today's site. Only after sign-off does the user commit. | User |

## 5. Definition of done for a module

- `docs/build/inventory/<module>.md` exists and every row is ticked.
- Backend routes, models, crud, tests written. Frontend call sites switched. Tests written
  for every page/component touched.
- Originals moved to `_backup/` with plain `mv`; nothing deleted, no git commands.
- Static checks (`ruff check`, `compileall`, `tsc --noEmit`) run **once** and clean.
- `docs/build/status/<module>.md` written: status, files created, files moved, new
  dependencies needed, shared-file changes needed as exact lines, open questions.
- No test executed (unless the user asked). No git command that changes anything.

## 6. Data and environment

Schema source: `_backup/supabase/migrations/0001_initial_schema.sql` (19 tables, schemas `ops`
and `app`). Remove: `extensions` schema reference, anon/service_role grants, storage
bucket migration, `auth.users` references and RLS policies. Add: `users`, `sessions`,
`login_attempts`, `audit_log`, `invite_tokens`, `files`.

Data export: for every module whose `DATA_BACKEND_<MODULE>` flag is `supabase` in Vercel,
`pg_dump --data-only` the tables; otherwise use `webapp/tools/migrate-supabase` output.
Data migration is done by the user (2026-09-19); the repo has no seed folder.

`.env.example` keys: POSTGRES_*, SECRET_KEY, TOTP_ENCRYPTION_KEY, ADMIN_EMAIL,
FIRST_ADMIN_EMAIL, FIRST_ADMIN_PASSWORD, FRONTEND_HOST, BACKEND_URL,
PUBLIC_ASSET_BASE_URL, S3_ENDPOINT/S3_BUCKET/S3_ACCESS_KEY/S3_SECRET_KEY, SMTP_HOST/PORT/USER/PASSWORD/TLS,
EMAILS_FROM_*, RESEND_WEBHOOK_SECRET, ANTHROPIC_API_KEY, PINECONE_API_KEY, OPENROUTER_API_KEY,
CRON_SECRET, EMAIL_LINK_SECRET, CHAT_HISTORY_SECRET, GITHUB_TOKEN/OWNER/REPO,
GSC_SERVICE_ACCOUNT_JSON, OUTREACH_DAILY_CAP, NEXT_PUBLIC_SITE_URL, NEXT_PUBLIC_SHOW_HINDI.

## 7. Testing policy (summary; details in write-tests and verify-build skills)

Write with the code, run once at the end. Coverage rule per module: each route has at
least one success test and one failure test (validation, auth, or rate limit); each
service and job function has a test with externals mocked; each page has a render test;
each component with branching logic has a behaviour test.
