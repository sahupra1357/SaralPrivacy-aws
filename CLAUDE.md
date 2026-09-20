# SaralPrivacy — Project Instructions

India-first DPDPA readiness platform (saralprivacy.com). This file governs the
**rebuild**: Next.js frontend + Python (FastAPI) backend + Postgres, run locally with
Docker Compose and Make, deployable to AWS via `infra/terraform`.

Read this file first. Then read the skill named for the task you are doing.
Skills live in `.claude/skills/<name>/SKILL.md`; agents in `.claude/agents/`.

## Current state vs target

| | Today (`webapp/`) | Target |
|---|---|---|
| Frontend | Next.js 16, React 19, Tailwind 4, next-intl, App Router | Same code, moved to `frontend/`, pages only |
| Backend | 43 Next.js API route handlers + `webapp/lib/*` server code | `backend/` FastAPI, SQLModel, Alembic, uv |
| Data | Supabase (PostgREST) + legacy Appwrite behind `DATA_BACKEND_*` flags | Postgres 16 in Docker, own schema |
| Auth | Supabase Auth + TOTP, custom HMAC cookie | Own users table, JWT, TOTP via pyotp, sessions table (see auth-module skill) |
| Files | Supabase Storage + Vercel Blob | MinIO locally, S3 in AWS, one storage service |
| Crons | Vercel crons + GitHub Actions Python pipeline | APScheduler worker container |
| Superseded files | | Moved to `_backup/` at their original path, never deleted |
| Git | `main` at HEAD | **Untouched until the finished app is manually tested.** All work is uncommitted in the working tree |

The full proposal, module map, waves and definition of done are in
`docs/build/BUILD_PLAN.md`. Progress is tracked in `docs/build/MODULE_STATUS.md`.

## Target layout

```
frontend/   Next.js app (today's webapp/). app/api/auth/* and app/api/proxy/[...path] are the
            ONLY route handlers that remain. Browser calls go through the proxy or src/client/.
backend/    app/api/routes/<module>.py, app/api/main.py, app/api/deps.py,
            app/core/{config,db,security,ratelimit}.py, app/models/<module>.py,
            app/crud/<module>.py, app/services/{storage,email,llm,pdf,revalidate}.py,
            app/jobs/, app/alembic/, app/email-templates/, app/tests/<module>/
docker-compose.yml, docker-compose.override.yml, Makefile, .env.example
docs/build/ BUILD_PLAN.md, MODULE_STATUS.md, inventory/<module>.md
frontend/docs/specs/  product/feature specs cited by frontend code (MULTILINGUAL_SPEC, LANDING_PAGE_*, SEO_*, ...)
backend/app/editorial/data/roadmap/  roadmap CSVs; 90_day_roadmap.csv is the pipeline's fallback when Sheets is down
_backup/    mirror of the original tree for everything superseded
```

## Non-negotiable rules

1. **No functionality lost.** Every route, validation, side effect, email, and response
   shape in the inventory for a module must exist in the rewrite. The inventory is the
   acceptance checklist.
2. **Rewrite, do not transliterate.** Read the TypeScript, write down the behaviour,
   write idiomatic Python. Do not port line by line.
3. **Backup, never delete.** A superseded file goes to `_backup/<original path>` with a
   plain `mv`. See the backup-move skill.
4. **Stay inside your module's file boundary** (BUILD_PLAN.md §Module map). Shared
   files are edited only by the orchestrator: `backend/app/api/main.py`,
   `backend/app/models/__init__.py`, Alembic migrations, `backend/pyproject.toml`,
   `frontend/package.json`, `frontend/next.config.ts`, compose files, Makefile.
   If you need a new dependency or a shared change, record it under your module in
   `docs/build/MODULE_STATUS.md` and continue; do not edit the shared file.
5. **Every route, service, job, page and component with logic gets a unit test**, written
   in the same task as the code. Externals (Anthropic, Pinecone, SMTP, S3, Playwright,
   Resend) are always mocked.
6. **Tests are written, not run, during development.** See "Token economy" below.
7. **Never invent DPDPA legal content.** Copy strings from the existing code verbatim.
8. Secrets never in code or committed files. `.env` is git-ignored; `.env.example` lists keys.
9. **No git changes until the user says so.** The committed HEAD is the only safety net,
   so nothing may alter it or the index. Forbidden for everyone, including agents:
   `git add`, `commit`, `stash`, `checkout`, `switch`, `restore`, `reset`, `clean`,
   `mv`, `rm`, `branch`, `merge`, `rebase`, `worktree`, `push`. Allowed, read-only:
   `git status`, `git diff`, `git log`, `git show`. The user commits after the manual
   acceptance test in wave 5.

## Token economy — when things may be executed

Running tests, builds, or containers after every edit burns tokens for no signal. Rules:

- **During development: do not run** `pytest`, `vitest`, `node --test`, `playwright`,
  `next build`, `npm run build`, `tsc`, `docker compose up/build`, `alembic upgrade`.
- **Once, when a module is complete**, you may run these cheap static checks on the
  files you touched only: `ruff check <paths>`, `python -m compileall -q <dir>`,
  `npx tsc --noEmit -p frontend/tsconfig.json`. Fix what they report, then stop.
- **Tests run in exactly two situations**: the final verification pass (`/verify-build`,
  run by the orchestrator or the `test-runner` agent after all modules merge), or when
  the user explicitly asks. Use `make test` (whole suite) or `make test-backend
  MODULE=<name>` / `make test-frontend MODULE=<name>` for a scoped run when asked.
- Do not re-read a file you just wrote. Read only the line ranges you need. Do not
  print large files into the conversation.

## Commands (Makefile — see compose-skeleton skill for the definitions)

```
make up            start db, backend, worker, frontend, mailcatcher, minio (health-checked)
make watch         same, with hot reload (docker compose watch)
make down / clean  stop / stop and drop volumes
make migrate       alembic upgrade head inside the backend container
make revision M="msg"  autogenerate a migration (orchestrator only)
make client        regenerate frontend/src/client from the backend OpenAPI
make lint          ruff + mypy + tsc (static only)
make test          full test suite: backend pytest, frontend vitest + node:test
make test-backend MODULE=forms
make test-frontend MODULE=forms
make logs / sh-api / sh-web
```

## Conventions in one screen

- **Backend**: FastAPI routers under `/api/v1`, one router file per module, SQLModel
  models, `crud/<module>.py` for queries, Pydantic request/response models next to the
  route, `deps.py` provides `SessionDep`, `CurrentUser`, `require_role(...)`,
  `RateLimit(key, limit, window)`. Config via `settings` in `core/config.py` reading the
  root `.env`. Python 3.12, uv, ruff, mypy strict. Errors: `HTTPException` with the same
  user-facing messages the TypeScript used.
- **Frontend**: keep Next 16 / React 19 / Tailwind 4 / next-intl. Browser → backend only
  via `app/api/proxy/[...path]` or `src/client/` (generated, never hand-edited). Server
  components fetch `${BACKEND_URL}/api/v1/...`. Auth cookie is HttpOnly `access_token`;
  `proxy.ts` verifies its signature with Web Crypto before rendering `/admin`.
- **Tests**: backend `app/tests/<module>/test_*.py` with pytest, a real Postgres test
  database, transaction rollback per test. Frontend pages/components: vitest +
  Testing Library under `frontend/tests/unit/<module>/`. Existing `lib/**/*.test.ts`
  files keep Node's test runner. Route handlers in `frontend/app/api/*`: vitest.
- **Email**: SMTP via `services/email.py` (mailcatcher locally, Resend SMTP in prod);
  Jinja2 templates in `app/email-templates/`.
- **Storage**: `services/storage.py` (boto3; MinIO locally). Public URLs built from
  `settings.PUBLIC_ASSET_BASE_URL`.
- **Jobs**: functions in `app/jobs/<module>.py` registered in `app/jobs/registry.py`
  with a cron expression; a Postgres advisory lock prevents double runs.

## Multi-agent build

The main session is the orchestrator and follows the `build-orchestrator` skill. It
spawns one `module-builder` agent per module, all working **in the same working tree
with no git isolation**. Safety comes from the file boundaries in BUILD_PLAN.md §3: a
builder writes only inside its own paths and its own status file
`docs/build/status/<module>.md`. Modules in the same wave run in parallel; when a
builder finishes, the orchestrator adds router, model and job registrations, generates
the migration, and updates MODULE_STATUS.md. Tests run once at the end via the
`test-runner` agent, then the user tests manually, then the user commits.

## House rules carried over

- `MEMORY.md` at the repo root is the project black box. Append a dated entry after
  each merged module. Never erase history.
- `infra/` (Terraform, AWS plan) is unchanged by this build except that the backend
  becomes a second ECS service; do not edit it during module work.
