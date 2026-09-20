# core — status: static-clean (integrated by the orchestrator, wave 0)
Updated: 2026-09-18

## Files created
- backend/: pyproject.toml, uv.lock, Dockerfile, .dockerignore, alembic.ini
- backend/app/main.py, api/main.py, api/deps.py, api/routes/utils.py
- backend/app/core/{config,db,security,ratelimit}.py
- backend/app/services/{storage,email,llm,pdf,revalidate}.py
- backend/app/jobs/{__init__,__main__,registry,runner}.py
- backend/app/models/{__init__,base}.py
- backend/app/alembic/{env.py,script.py.mako,README}, versions/20260918_0000_0001_core_schemas_and_uuid_v7.py
- backend/app/email-templates/base.html
- backend/app/{backend_pre_start,initial_data}.py, scripts/{prestart,test,lint,format}.sh
- backend/app/tests/conftest.py, tests/core/test_{security,ratelimit,deps,services,jobs}.py
- backend/db/init/01_schemas.sql
- frontend/app/api/proxy/[...path]/route.ts, frontend/lib/api.ts
- frontend/vitest.config.ts, frontend/tests/setup.ts, frontend/openapi-ts.config.ts
- frontend/tests/unit/core/{proxy-route,api}.test.ts
- docker-compose.yml, docker-compose.override.yml, Makefile, .env.example, _backup/LEDGER.md

## Files changed
- `webapp/` renamed to `frontend/` (plain mv). Every file kept.
- frontend/package.json: scripts test:unit, test:lib, typecheck, generate-client; devDependencies
  vitest, jsdom, @vitejs/plugin-react, @testing-library/{react,jest-dom,user-event},
  @hey-api/{openapi-ts,client-fetch}. `npm install` run once (needed for tsc).
- .gitignore: rebuild entries appended.

## Moved to _backup
- none (rename only; ledger has the note)

## Contracts available to modules
- deps: SessionDep, CurrentUser, PendingUser, require_role(...), RateLimit(key, limit, window, by, message), client_ip
- security: hash/verify_password, password_ok, create_pending_token, create_access_token, decode_token,
  new_opaque_token, hash_opaque_token, encrypt_secret, decrypt_secret
- services: storage.put/get_url/presigned_download_url/exists/delete/get_bytes; email.render/send;
  llm.complete/stream; pdf.render_html; revalidate.tag/path
- jobs: register(name, cron, fn); JobResult
- models.base.TimestampMixin; migration 0001 creates schemas, uuid_generate_v7, app.login_attempts
- test fixtures: session, client, mock_email, mock_storage, mock_llm, mock_pdf, mock_revalidate

## Needs from orchestrator (wave 3)
- .github/workflows/deploy-aws.yml and infra Dockerfile references: `webapp` → `frontend`; add backend ECS service.
- frontend/Dockerfile → Alpine once notices (PDF) and editorial (infographic) modules are integrated.
- `frontend/app/api/health/route.ts` stays (compose + ALB health check).

## Open questions
- deps.py expects the auth module to provide `app.crud.auth.get_user`, `get_live_session`, `ensure_first_admin` — auth builder must implement exactly these names.
- Static checks not run inside Docker (Docker Desktop not running); ruff/compileall/tsc run on host.
