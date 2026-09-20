# auth — status: static-clean
Updated: 2026-09-18

Inventory: `docs/build/inventory/auth.md` — every row implemented and ticked.
No test was executed. No git command that changes anything was run.

## Files created

### Backend
- `backend/app/models/auth.py` — `User`, `AuthSession` (table `sessions`), `LoginAttempt`,
  `AuditLog`, `InviteToken`; all schema `app`.
- `backend/app/crud/auth.py` — includes the three names core already imports:
  `get_user(session, user_id)`, `get_live_session(session, session_id)`,
  `ensure_first_admin(session)`.
- `backend/app/api/routes/login.py` — `/auth/login`, `/auth/logout`, `/auth/me`,
  `/auth/invite`, `/auth/recover`, `/auth/set-password`. Also exports
  `get_current_session` / `CurrentSession` / `AdminUser` / `UserOut` / `to_user_out`
  for `routes/users.py`.
- `backend/app/api/routes/mfa.py` — `/auth/mfa/enroll`, `/auth/mfa/verify` (the only
  place an access token is minted) + `qr_svg_data_url`.
- `backend/app/api/routes/users.py` — `GET /users`, `PATCH /users/{id}`,
  `POST /users/{id}/revoke-sessions`, `POST /users/{id}/reset-totp` (admin only).
- `backend/app/email-templates/auth_invite.html` — invite / admin-invite / recovery copy,
  carried verbatim from `webapp/lib/email-templates.ts` § `bloggerInviteTemplate`.
- `backend/app/tests/auth/{__init__,fixtures,conftest}.py`
- `backend/app/tests/auth/test_{login,mfa,set_password,invite,users,crud}.py` — 6 files.

### Frontend
- `frontend/lib/auth/session.ts` — HS256 verification with Web Crypto + `SECRET_KEY`,
  cookie names/lifetimes, `verifyAccessToken`, `getSession`, `requireRole`, `cookieOptions`.
- `frontend/app/api/auth/_backend.ts` — shared forwarder; flattens FastAPI's
  `{"detail": …}` back into the `{error, step}` the pages have always read.
- `frontend/app/api/auth/login/route.ts`
- `frontend/app/api/auth/mfa/enroll/route.ts`
- `frontend/app/api/auth/mfa/verify/route.ts`
- `frontend/app/api/auth/me/route.ts`
- `frontend/app/api/auth/logout/route.ts`
- `frontend/app/api/auth/set-password/route.ts`
- `frontend/tests/unit/auth/{session,api-login,api-mfa,api-session,proxy-auth}.test.ts`
- `frontend/tests/unit/auth/{login-page,set-password-page}.test.tsx` — 7 files.

## Files changed
- `backend/app/core/security.py` — **filled in** as BUILD_PLAN §3 assigns to auth: added
  `TOTP_ISSUER`, `TOTP_INTERVAL`, `TOTP_DRIFT_STEPS`, `new_totp_secret`, `totp_uri`,
  `totp_step`, `verify_totp`. Nothing existing was changed; core's tests still apply.
- `frontend/proxy.ts` (auth section only) — HMAC check → `verifyAccessToken` /
  `ACCESS_TOKEN_COOKIE`; blogger prefix `"/api/admin/set-password"` → `"/api/auth/set-password"`.
  Locale routing, matcher, casing redirects and `ADMIN_PUBLIC_PATHS` untouched.
- `frontend/lib/adminSession.ts` — **intentional rewrite**: now a compatibility shim
  re-exporting `lib/auth/session.ts` under the old names (`ADMIN_SESSION_COOKIE`,
  `ADMIN_SESSION_MAX_AGE`, `verifyAdminSessionToken`, `getAdminSession`, `requireRole`,
  `AdminRole`, `AdminSession`). `createAdminSessionToken` is gone — its only two callers
  moved to `_backup`. The original is preserved at `_backup/webapp/lib/adminSession.ts`
  (partial-file rule: `cp`, then edit in place). See "Needs from orchestrator".
- `frontend/app/(backoffice)/admin/login/page.tsx` — three fetch targets
  `/api/admin/*` → `/api/auth/*`, plus one stale comment. No markup, copy or styling change.
- `frontend/app/(backoffice)/admin/set-password/page.tsx` — reads `?token=` (falls back
  to `?token_hash=` for links already in inboxes), posts `{token, type, password}` to
  `/api/auth/set-password`. No markup, copy or styling change.

## Files deliberately left live (not superseded yet)
- `frontend/lib/adminSession.ts` — 17 files owned by the admin, editorial and outreach
  modules still import `requireRole` / `verifyAdminSessionToken` from it. Moving it now
  would break the shared tree. The shim keeps them compiling against real JWT verification.
- `frontend/lib/auth/adminAuth.ts` — untouched; still imported by
  `app/api/admin/bloggers/route.ts`, `app/api/admin/bloggers/[id]/route.ts` (admin module,
  wave 2) and `tools/auth/provision.ts`. Its behaviour is replaced by `POST /auth/invite`
  and `POST /auth/recover`; the file moves when the admin module lands.

## Moved to _backup (also in `_backup/LEDGER.md`)
- `webapp/app/api/admin/login/route.ts`
- `webapp/app/api/admin/mfa/enroll/route.ts`
- `webapp/app/api/admin/mfa/verify/route.ts`
- `webapp/app/api/admin/set-password/route.ts`
- `webapp/lib/auth/supabaseAuth.ts`
- `webapp/lib/auth/pending.ts`
- `webapp/lib/auth/auth.test.ts`
- `webapp/lib/adminSession.ts` (partial: `cp`, live file rewritten as the shim above)

8 ledger lines appended.

## Needs from orchestrator

### `backend/app/api/main.py`
```python
from app.api.routes import login, mfa, users
api_router.include_router(login.router)
api_router.include_router(mfa.router)
api_router.include_router(users.router)
```

### `backend/app/models/__init__.py`
```python
from app.models.auth import AuditLog, AuthSession, InviteToken, LoginAttempt, User
```
(and add `"AuditLog", "AuthSession", "InviteToken", "LoginAttempt", "User"` to `__all__`)

### `backend/app/tests/conftest.py` — one line, at module level
```python
pytest_plugins = ["app.tests.auth.fixtures"]
```
This publishes `admin_headers`, `blogger_headers`, `admin_user`, `blogger_user` and
`make_test_user` to every module's tests. `app/tests/auth/conftest.py` re-imports the same
names so the auth tests work either way (a `pytest_plugins` line is not allowed in a
non-root conftest).

### Migration (wave 3 autogenerate)
Five tables in schema `app`: `users`, `sessions`, `audit_log`, `invite_tokens`, and
`login_attempts` — the last one already exists from migration `0001`, and is modelled here
only so autogenerate does not emit a drop. Autogenerate must therefore be reviewed for a
spurious `drop_table("login_attempts")` / re-create.

`ops.blogger_accounts` is **not** modelled by this module (the admin module owns it);
`crud.auth` reads and updates it with two narrow `text()` statements, so the table must
exist before `test_login.py`, `test_set_password.py` and `test_crud.py` run.

### Jobs
None. Auth has no cron.

### Dependencies
Backend: none new — `pyotp`, `qrcode[pil]`, `cryptography`, `pyjwt`, `passlib[bcrypt]`
are already in `backend/pyproject.toml`.
Frontend: none new.

### Environment
All keys already in `.env.example`: `SECRET_KEY`, `TOTP_ENCRYPTION_KEY`, `FRONTEND_HOST`,
`FIRST_ADMIN_EMAIL`, `FIRST_ADMIN_PASSWORD`, `BACKEND_URL`.
`SECRET_KEY` must be readable by the **frontend** process too — `proxy.ts` verifies the
same signature. Confirm it is passed to the web container in `docker-compose.yml`.
`ADMIN_SESSION_SECRET` and `SUPABASE_ANON_KEY` are no longer read by any live auth code.

### Wave 2 follow-ups (not auth's to do)
- Admin module: switch `app/api/admin/bloggers/*` to `POST /api/v1/auth/invite`,
  `PATCH /api/v1/users/{id}` and `POST /api/v1/users/{id}/revoke-sessions`; then
  `lib/auth/adminAuth.ts` and `tools/auth/provision.ts` can move to `_backup`.
  The bloggers route keeps its own duplicate message, `"A blogger with this email already
  exists."`; `/auth/invite` answers the generic `"An account with this email already exists."`.
- Admin / editorial / outreach: replace `@/lib/adminSession` imports with
  `@/lib/auth/session`; then the shim moves to `_backup`.
- `lib/auth/adminAuth.deleteAuthUserByEmail()` has no backend replacement by design — with
  our own `users` table the admin module deletes the row directly.

## Static checks (run once, on auth-owned files only)
- `ruff check backend/app/api/routes/{login,mfa,users}.py backend/app/models/auth.py backend/app/crud/auth.py backend/app/core/security.py backend/app/tests/auth` → **All checks passed!**
- `python3 -m compileall -q backend/app` → clean.
- `npx tsc --noEmit -p frontend/tsconfig.json` → clean for every auth-owned file.
  The only remaining output is `.next/types/validator.ts` referencing route handlers this
  rebuild removed (`app/api/admin/{login,mfa/*,set-password}` from auth, plus
  `app/api/{assessment,notice/*}` from other builders). That file is **generated** by
  `next build` / `next dev` and is stale; it regenerates in wave 3. Nothing to fix here.

## Open questions
1. **409 message on `/auth/invite`.** The old copy lived in the bloggers route
   (`"A blogger with this email already exists."`) and is blogger-specific. `/auth/invite`
   serves both roles, so it answers `"An account with this email already exists."` and the
   bloggers route keeps its own string. Flagged `?` in the inventory; change if the admin
   module would rather surface one message.
2. **No `/api/auth/recover` Next.js route.** Nothing in today's UI offers "forgot
   password" — links were generated by `tools/auth/provision.ts`. The backend endpoint
   exists; add the thin Next.js handler when a UI needs it.
3. **`is_active=false` answers 403 `"Access denied."`, not 401.** The auth-module skill
   says so explicitly; Supabase's ban used to surface as 401 `"Invalid credentials."`.
   Following the skill. Worth one line on the manual checklist.
4. **Edge verification cannot see revocation.** `proxy.ts` checks signature, `exp`,
   `purpose` and role only — a just-revoked token can still render the admin shell until
   it expires, though every API call it makes 401s. Documented in `lib/auth/session.ts`.
   A DB round trip in middleware would be the alternative; not taken.
5. **`ops.blogger_accounts.updated_at`** is written by `crud.activate_blogger`; the column
   exists in the Supabase DDL. If the admin module's model drops it, that statement needs
   the column removed.
