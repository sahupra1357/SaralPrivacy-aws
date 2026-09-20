# SaralPrivacy

India's practical DPDPA readiness platform — [saralprivacy.com](https://saralprivacy.com).

| Part | Stack | Folder |
|---|---|---|
| Frontend | Next.js 16, React 19, Tailwind 4, next-intl (English + Hindi) | `frontend/` |
| Backend | FastAPI, SQLModel, Alembic, Playwright (PDFs), Python 3.12, uv | `backend/` |
| Worker | Same image as the backend, APScheduler (5 scheduled jobs) | `backend/app/jobs/` |
| Database | PostgreSQL 16 (schemas `app` and `ops`) | `backend/db/init/`, `backend/app/alembic/` |
| Infra | Terraform for AWS: CloudFront, ALB, ECS Fargate, RDS, S3 | `infra/` |

## Run it locally

Requirements: Docker Desktop, Make. Node 22 and uv only if you run tests or tools on the host.

```bash
cp .env.example .env            # then set SECRET_KEY, POSTGRES_PASSWORD, FIRST_ADMIN_*
make up                         # = docker compose up -d --wait
```

| What | Where |
|---|---|
| Website | http://localhost:3000 |
| Admin | http://localhost:3000/admin/login (first admin from `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD`; TOTP enrolment on first login) |
| API docs | http://localhost:8000/docs |
| Local inbox (all outgoing mail) | http://localhost:1080 |
| File storage console (MinIO) | http://localhost:9001 |
| Postgres | `localhost:5434` |

Hot reload: `make watch`. Stop: `make down`. Wipe data: `make clean`.

## Everyday commands

```bash
make migrate                 # alembic upgrade head
make revision M="message"    # autogenerate a migration
make client                  # regenerate frontend/src/client from the API schema
make lint                    # ruff + mypy + tsc
make test                    # backend pytest + frontend vitest + node tests
make test-backend MODULE=forms
make logs | make sh-api | make sh-web
```

## How requests flow

Browser → Next.js (`frontend/`) → `/api/proxy/api/v1/...` → FastAPI (`backend/`) → Postgres.
The session is an HttpOnly `access_token` cookie (JWT, 8 h, TOTP required); `frontend/proxy.ts`
verifies it before any `/admin` page renders. The only route handlers left in Next.js are
`app/api/auth/*`, the proxy, `app/api/revalidate` and `app/api/health`.

## Where things are documented

- `CLAUDE.md` — rules and conventions for working in this repo (read first).
- `docs/build/` — rebuild plan, module status, per-module behaviour inventories.
- `frontend/docs/specs/` — product and feature specs cited by the frontend code.
- `infra/AWS_DEPLOYMENT_PLAN.md` — AWS architecture and exact deployment steps.
- `_backup/supabase/` — the original Supabase DDL the models were copied from (reference only).
- `_backup/LEDGER.md` — every file moved out of the live tree during the rebuild, and why.
