# auth inventory

Source read (pre-rebuild paths): `webapp/app/api/admin/{login,mfa/enroll,mfa/verify,set-password}/route.ts`,
`webapp/lib/auth/{supabaseAuth,pending,adminAuth}.ts`, `webapp/lib/adminSession.ts`,
`webapp/proxy.ts` (auth section), `webapp/app/(backoffice)/admin/{login,set-password}/page.tsx`,
`webapp/lib/email-templates.ts` § `bloggerInviteTemplate` (invite/recovery copy),
`webapp/app/api/admin/bloggers/route.ts` (the one caller of the invite path).

Environment variables read by the old code: `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
`ADMIN_SESSION_SECRET`, `NEXT_PUBLIC_SITE_URL`, `NODE_ENV`.
New equivalents: `SECRET_KEY`, `TOTP_ENCRYPTION_KEY`, `FRONTEND_HOST`,
`FIRST_ADMIN_EMAIL`, `FIRST_ADMIN_PASSWORD` (all already in `.env.example`).
No `maxDuration` was set on any auth route.

## Routes

| # | Method + path (old → new) | Auth / rate limit | Request (fields, validation, limits) | Side effects | Response (shape, status codes, exact error strings) | Callers (file:line) | Test written |
|---|---|---|---|---|---|---|---|
| 1 | `POST /api/admin/login` → `POST /api/v1/auth/login` | none; `rateLimit("admin-login:<ip>", 5, 15 min)` | `{email, password}`. email trimmed + lower-cased. Both required. | reads `app.users`, `ops.blogger_accounts` | 200 `{success:true, step:"enroll"\|"verify", role, pending_token}`; 400 `"Email and password are required."`; 401 `"Invalid credentials."` (unknown email *and* wrong password — no enumeration); 403 `"Access denied."` (correct password but no role / inactive / blogger not active); 429 `"Too many attempts. Try again later."` + `Retry-After` | `app/(backoffice)/admin/login/page.tsx:77` | `app/tests/auth/test_login.py` |
| 2 | `DELETE /api/admin/login` → dropped (frontend `POST /api/auth/logout`) + `POST /api/v1/auth/logout` | `CurrentUser` | — | sets `sessions.revoked_at`; `audit_log` `logout` | 200 `{success:true}`; 401 `"Session expired. Please sign in again."` | no caller in the old tree (cookie clear only) | `app/tests/auth/test_login.py` |
| 3 | `POST /api/admin/mfa/enroll` → `POST /api/v1/auth/mfa/enroll` | pending token (was the `admin_mfa_pending` cookie); `rateLimit("admin-mfa-enroll", 10, 15 min)` — new; the old route had none but Supabase throttled | no body | writes `users.totp_secret_enc` (Fernet), `totp_confirmed=false`, `last_totp_step=null` | 200 `{factorId, qr (SVG data URL), secret, otpauth_uri}`; 401 `{error:"Session expired. Please sign in again.", step:"login"}`; 403 `"Access denied."`; 409 `{error:"A verification app is already set up for this account.", step:"verify"}`; 429 `"Too many attempts. Try again later."` | `app/(backoffice)/admin/login/page.tsx:59` | `app/tests/auth/test_mfa.py` |
| 4 | `POST /api/admin/mfa/verify` → `POST /api/v1/auth/mfa/verify` | pending token; `rateLimit("admin-mfa:<ip>", 10, 15 min)` | `{code}` must match `^\d{6}$`; `factorId` accepted and ignored (kept for the page's payload) | inserts `app.sessions`; sets `totp_confirmed=true`, `last_totp_step`; `audit_log` `login` | 200 `{success:true, access_token, token_type:"bearer", role, expires_in}`; 400 `"Enter the 6-digit code."`; 401 `"Code did not match. Try again."`; 401 `{error:"Set up your verification app first.", step:"enroll"}`; 401 `{error:"Session expired. Please sign in again.", step:"login"}`; 403 `"Access denied."`; 429 `"Too many attempts. Try again later."` | `app/(backoffice)/admin/login/page.tsx:95` | `app/tests/auth/test_mfa.py` |
| 5 | `POST /api/admin/set-password` → `POST /api/v1/auth/set-password` | none; `rateLimit("set-password:<ip>", 10, 15 min)` | old `{token_hash, type, password}` → new `{token, type, password}`. `type` ∈ `invite\|recovery` else treated as missing. Password 12–128 chars. | consumes `app.invite_tokens` (`used_at`), sets `users.hashed_password`, revokes every `app.sessions` row of that user, `ops.blogger_accounts.active=true` + `invite_token=''` on a blogger **invite** (never on recovery), `audit_log` `set_password` | 200 `{success:true}`; 400 `"All fields are required."`; 400 `"Password must be 12–128 characters."` (en dash, as today); 401 `"This link is invalid or has expired. Ask the admin for a new one."`; 429 `"Too many attempts. Try again later."` | `app/(backoffice)/admin/set-password/page.tsx:48` | `app/tests/auth/test_set_password.py` |
| 6 | `lib/auth/adminAuth.createInvite()` → `POST /api/v1/auth/invite` | `require_role("admin")` | `{email, name, role}`; email + name required; role ∈ `admin\|blogger` (default `blogger`) | inserts `app.users` (unusable password, `is_active=true`), `app.invite_tokens` purpose `invite` 24 h, sends the invite email, `audit_log` `invite` | 200 `{success:true, user_id, invite_url, email_sent, email_error}`; 400 `"Email and name are required."`; 409 `"An account with this email already exists."` (`?` — old copy lived in the bloggers route as `"A blogger with this email already exists."`, which stays there); 403 `"Access denied."` | `app/api/admin/bloggers/route.ts:61` (admin module, wave 2) | `app/tests/auth/test_invite.py` |
| 7 | `lib/auth/adminAuth.createRecovery()` → `POST /api/v1/auth/recover` | none; `RateLimit("auth-recover", 5, 15 min)` | `{email}` | `app.invite_tokens` purpose `recovery` 1 h; sends the reset email; `audit_log` `recover` | always 200 `{success:true, message:"If that account exists, an email has been sent."}` — no enumeration; 429 `"Too many attempts. Try again later."` | none today (links were generated by `tools/auth/provision.ts`) | `app/tests/auth/test_invite.py` |
| 8 | `GET /api/v1/auth/me` (new; replaces reading the HMAC cookie in server components) | `CurrentUser` | — | none | 200 `{id, email, role, name, is_active, totp_confirmed}`; 401 `"Session expired. Please sign in again."` | `frontend/app/api/auth/me/route.ts` | `app/tests/auth/test_login.py` |
| 9 | `lib/auth/adminAuth.setAuthUserBanned()` → `PATCH /api/v1/users/{id}` | `require_role("admin")` | `{is_active?, role?}` | updates `app.users`; revokes all sessions when deactivated; `audit_log` `user.update` | 200 user shape; 400 `"Nothing to update."`; 403 `"Access denied."`; 404 `"Not found."` | admin module (wave 2) | `app/tests/auth/test_users.py` |
| 10 | new `POST /api/v1/users/{id}/revoke-sessions` | `require_role("admin")` | — | revokes every live session; `audit_log` `user.revoke_sessions` | 200 `{success:true, revoked:<n>}`; 403/404 as above | admin module (wave 2) | `app/tests/auth/test_users.py` |
| 11 | new `POST /api/v1/users/{id}/reset-totp` | `require_role("admin")` | — | clears `totp_secret_enc`, `totp_confirmed`, `last_totp_step`; revokes sessions; `audit_log` `user.reset_totp` | 200 `{success:true}`; 403/404 as above | admin module (wave 2) | `app/tests/auth/test_users.py` |

Old `lib/auth/adminAuth.deleteAuthUserByEmail()` has no backend replacement: with our own
`users` table the admin module deletes the row directly. Recorded as a needs-line.

## Frontend route handlers that stay (cookie handling only)

| File | Behaviour | Test |
|---|---|---|
| `frontend/app/api/auth/login/route.ts` | POST → backend `/auth/login`; on success sets `mfa_pending` (HttpOnly, SameSite=Lax, path `/api/auth/mfa`, 600 s, `secure` only on https); returns `{success, step, role}` — never the pending token | `tests/unit/auth/api-login.test.ts` |
| `frontend/app/api/auth/mfa/enroll/route.ts` | reads `mfa_pending`, bearer to backend; 401 `{error:"Session expired. Please sign in again.", step:"login"}` when absent | `tests/unit/auth/api-mfa.test.ts` |
| `frontend/app/api/auth/mfa/verify/route.ts` | same, plus on 200 sets `access_token` (HttpOnly, Lax, path `/`, 28800 s) and clears `mfa_pending`; returns `{success, role}` without the token | `tests/unit/auth/api-mfa.test.ts` |
| `frontend/app/api/auth/me/route.ts` | GET → backend `/auth/me` with the cookie as bearer | `tests/unit/auth/api-session.test.ts` |
| `frontend/app/api/auth/logout/route.ts` | POST → backend `/auth/logout` (best effort), always clears `access_token` and `mfa_pending` | `tests/unit/auth/api-session.test.ts` |
| `frontend/app/api/auth/set-password/route.ts` | POST → backend `/auth/set-password`, no cookies | `tests/unit/auth/api-set-password.test.ts` |

All six flatten the backend's `{"detail": …}` into `{error, step}` so the pages keep
reading `data.error` / `data.step` exactly as they do today.

## Server-rendered pages / middleware reading the session

| Page | What it needs | Notes |
|---|---|---|
| `proxy.ts` (`/admin/**` gate) | verified role from the cookie | HMAC `v1.<payload>.<sig>` → HS256 JWT verified with Web Crypto + `SECRET_KEY`; `ADMIN_PUBLIC_PATHS = ["/admin/login", "/admin/set-password"]` unchanged; blogger prefixes unchanged except `/api/admin/set-password` → `/api/auth/set-password` |
| `app/(backoffice)/admin/layout.tsx`, `admin/blog/new`, `admin/blog/[id]/edit`, `admin/seo/actions.ts`, `app/api/briefings/send`, and every `requireRole(...)` caller | `verifyAdminSessionToken` / `requireRole` | **outside this module's boundary** (admin, editorial, outreach). `lib/adminSession.ts` is kept as a compatibility shim over the new JWT verification so they compile unchanged until wave 2/3. |
| `app/(backoffice)/admin/login/page.tsx` | fetch targets only | markup, copy and styling untouched |
| `app/(backoffice)/admin/set-password/page.tsx` | fetch target + query param | reads `?token=` (falls back to `?token_hash=` for links already in inboxes); markup untouched |

## Jobs / crons

None. `vercel.json` has no auth cron.

## Library logic to rewrite (not routes)

| Source file | Behaviour to keep | Notes |
|---|---|---|
| `lib/adminSession.ts` | 8 h session, roles `admin\|blogger`, `name`, `userId` carried in the token; unforgeable; rotating the secret kills every session; `requireRole` helper | now an HS256 JWT with `jti` = `app.sessions.id`, so revocation works too. Original `cp`-ed to `_backup/`; live file is a shim |
| `lib/auth/pending.ts` | 10-minute, path-scoped carrier between password and TOTP; identity never read from it | now a 10-minute JWT with `purpose:"mfa"` carrying only `sub`; the cookie is still path-scoped to `/api/auth/mfa` |
| `lib/auth/supabaseAuth.ts` | `roleOf` (role only from a server-writable source), `displayNameOf` (trimmed or none), `nextStep` (verified TOTP → verify, else enroll), `isValidTotpCode` (`^\d{6}$`), no persisted client session | role/name are columns on `app.users`; step derives from `totp_confirmed`; TOTP via `pyotp`, issuer `SaralPrivacy`, `valid_window=1` |
| `lib/auth/adminAuth.ts` | invite/recovery link generation `"<site>/admin/set-password?…&type=invite\|recovery"`, links generated then emailed by us | `POST /auth/invite` and `/auth/recover`. **File kept live**: the admin module's bloggers routes and `tools/auth/provision.ts` still import it |
| `lib/abuseGuard.ts` | `rateLimit(key, limit, windowMs)` + `Retry-After` | already replaced by `core/ratelimit.py` (core module) |
| `lib/email-templates.ts` § `bloggerInviteTemplate` | invite / admin-invite / recovery subjects and body copy | copied verbatim into `app/email-templates/auth_invite.html`; the TS file stays (outreach module owns it) |

## Strings to copy verbatim

Route errors:
- `"Email and password are required."`
- `"Invalid credentials."`
- `"Access denied."`
- `"Too many attempts. Try again later."`
- `"Session expired. Please sign in again."`
- `"A verification app is already set up for this account."`
- `"Could not start verification setup."`
- `"Enter the 6-digit code."`
- `"Code did not match. Try again."`
- `"Set up your verification app first."`
- `"All fields are required."`
- `"Password must be 12–128 characters."` (en dash U+2013)
- `"This link is invalid or has expired. Ask the admin for a new one."`
- `"Email and name are required."`
- `"If that account exists, an email has been sent."`

Email subjects (`bloggerInviteTemplate`):
- recovery: `"Reset your SaralPrivacy admin password"`
- admin invite: `"Set up your SaralPrivacy admin account"`
- blogger invite: `"You've been invited to contribute to SaralPrivacy Insights"`

Email body copy carried verbatim into `app/email-templates/auth_invite.html`:
- `"We received a request to reset the password for your <strong>SaralPrivacy</strong> admin account. If you didn't ask for this, ignore this email — nothing changes."`
- `"Your <strong>SaralPrivacy</strong> admin account is ready. Set a password now; on your first sign-in you'll also set up an authenticator app (two-step verification)."`
- `"You have been invited to contribute expert insights to <strong>SaralPrivacy</strong> — India's DPDPA compliance platform."`
- `"As a <strong>Blog Contributor</strong>, you can:"` with the four bullets
  (`"Write and draft DPDPA compliance insights"`, `"Run AI-powered DPDPA guardrail validation on your content"`,
  `"Generate smart infographics from your articles"`, `"Submit posts for admin review and publication"`)
- `"Click the button below to …"` / `"This link is valid for <strong>…</strong> and can be used once."`
- `"If the button does not work, copy this link into your browser:"`
- CTAs `"Choose a New Password"` / `"Set Up My Account"`; headings `"Password reset"` / `"Welcome, <name>!"`

Page copy (unchanged, listed so a reviewer can diff): `"Set up your verification app"`,
`"Two-step verification"`, `"Activate & Sign In"`, `"Sign In"`, `"Start over"`,
`"Use a different account"`, `"Password must be at least 12 characters."`,
`"Passwords do not match."`, `"Account activated!"`, `"Password updated"`.

## Out of scope / kept in frontend

- Both `/admin/login` and `/admin/set-password` pages keep their markup, copy and styling.
- `lib/auth/adminAuth.ts` and `tools/auth/provision.ts` stay live (admin module, wave 2).
- `lib/adminSession.ts` stays live as a shim for the twelve non-auth `requireRole` callers.
- `lib/db/*`, `lib/abuseGuard.ts`, `lib/email.ts`: core / other modules.
- No `/api/auth/recover` Next.js route: nothing in the UI offers "forgot password" today.
