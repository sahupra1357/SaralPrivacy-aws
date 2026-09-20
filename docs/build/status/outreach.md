# outreach — status: static-clean
Updated: 2026-09-18

Inventory: `docs/build/inventory/outreach.md` — 7 routes ported + 1 new admin list route, 2 crons → jobs.
Every inventory row is implemented and has a test.

## Files created

Backend
- `backend/app/models/outreach.py` — `OutreachContact` (`ops.outreach_contacts`), `EmailSendLog`
  (`ops.email_send_log`), both on `TimestampMixin`; status/type constants mirroring the CHECKs
- `backend/app/crud/outreach.py` — contact/send-log/subscriber queries; `app.briefings_meta`
  read and updated with parameterised `text()` SQL (`to_jsonb(b)` → dict), no import of editorial's model
- `backend/app/api/routes/outreach.py` — `router` (`/outreach/{import,stats,contacts,subscribe,unsubscribe}`)
  and `cron_router` (`/cron/{outreach-send,briefing-send}`, GET, `Authorization: Bearer CRON_SECRET`)
- `backend/app/api/routes/webhooks.py` — `POST /webhooks/resend`, pure-Python svix verification
  (HMAC-SHA256 over `id.timestamp.body`, base64 key after `whsec_`, any `v1,` sig, ±5 min tolerance)
- `backend/app/jobs/outreach.py` — `run_outreach_send`, `run_briefing_send` (+ `outreach_send` /
  `briefing_send` shared with the HTTP triggers), and the ported sendGateway / briefing-send policy:
  `eligible_subscribers`, `build_unsubscribe_url`, `render_briefing_email`, `render_outreach_email`,
  `send_subscriber_briefing`, `send_briefing_to_subscribers`
- `backend/app/email-templates/outreach/` — `briefing.html`, `outreach_briefing.html`,
  `outreach_briefing.txt`, `_checklist_rows.html` (copy verbatim from lib/email-templates.ts §7, §9)
- `backend/app/tests/outreach/` — `__init__.py`, `helpers.py`, `test_import.py`, `test_stats.py`,
  `test_subscribe.py`, `test_unsubscribe.py`, `test_outreach_send.py`, `test_briefing_send.py`,
  `test_webhooks.py`, `test_crud.py`, `test_email_policy.py` (9 test files)

Frontend tests
- `frontend/tests/unit/outreach/` — `magic-subscribe-client.test.tsx`,
  `outreach-unsubscribe-client.test.tsx`, `admin-outreach-page.test.tsx` (3 files)

## Files changed (frontend call sites)
Only fetch URLs and the error field read (`detail` before `error`); markup/copy untouched.
- `app/[locale]/subscribe/MagicSubscribeClient.tsx` → `/api/proxy/api/v1/outreach/subscribe`
- `app/[locale]/unsubscribe/outreach/OutreachUnsubscribeClient.tsx` → `/api/proxy/api/v1/outreach/unsubscribe`
- `app/(backoffice)/admin/outreach/page.tsx` → `/outreach/stats`, `/outreach/import`, and
  `/outreach/contacts?limit=200[&status=]` (was `/api/admin/data?collection=outreach_contacts`)

## Moved to _backup (also in _backup/LEDGER.md)
7 files, plain `mv` to `_backup/webapp/...`: `app/api/outreach/{import,stats,subscribe,unsubscribe}/route.ts`,
`app/api/cron/{outreach-send,briefing-send}/route.ts`, `app/api/webhooks/resend/route.ts`.
Emptied directories removed with `rmdir`. No git command run.
NOT moved (still imported by editorial `api/briefings/{generate,approve,send}` and admin
`api/admin/{send-report,bloggers}`): `lib/email.ts`, `lib/email-templates.ts`, `lib/sendGateway.ts`,
`lib/resendClient.ts`. Whoever removes the last consumer moves them.

## Needs from orchestrator
- include_router — `backend/app/api/main.py`:
  ```python
  from app.api.routes import outreach, webhooks
  api_router.include_router(outreach.router)
  api_router.include_router(outreach.cron_router)
  api_router.include_router(webhooks.router)
  ```
  (`cron_router` has prefix `/cron`; if admin also defines a `/cron` router both can be included.)
- models import — `backend/app/models/__init__.py`:
  ```python
  from app.models.outreach import EmailSendLog, OutreachContact  # noqa: F401
  ```
  and add `"EmailSendLog", "OutreachContact"` to `__all__`.
- jobs — `backend/app/jobs/__init__.py`:
  ```python
  from app.jobs import outreach as _outreach
  register("outreach-send", "0 4 * * *", _outreach.run_outreach_send)
  register("briefing-send", "30 4 * * *", _outreach.run_briefing_send, timeout_seconds=600)
  ```
- dependencies: backend none (svix done in stdlib; python-multipart already present).
  Frontend: `svix` (and eventually `resend`) can be dropped from `frontend/package.json` once
  editorial/admin retire `lib/resendClient.ts`; `svix` has no importer left now.
- Migration: create `ops.outreach_contacts` and `ops.email_send_log` exactly as
  `_backup/supabase/migrations/0001_initial_schema.sql` lines 310–351, including what SQLModel cannot express:
  ```sql
  alter table ops.outreach_contacts add constraint outreach_contacts_status_check
    check (status in ('pending','sent','subscribed','bounced','unsubscribed','complained','failed'));
  alter table ops.email_send_log add constraint email_send_log_email_type_check
    check (email_type in ('intro','briefing_daily','briefing_weekly','report','subscribe_confirmation','unsubscribe_confirmation'));
  alter table ops.email_send_log add constraint email_send_log_status_check
    check (status in ('sent','delivered','opened','clicked','bounced','complained'));
  create index ix_outreach_contacts_created_idx on ops.outreach_contacts (created_at_attr);
  ```
  (unique email / magic_token and the status, recipient, type, sent_at, message-id indexes are
  declared on the models; autogenerate names them differently from the Supabase names — harmless.)
- **Resend webhook URL.** Resend is configured to POST `https://saralprivacy.com/api/webhooks/resend`.
  Either (a) route `/api/v1/webhooks/resend` straight to the backend at the ALB and update the URL in
  the Resend dashboard, or (b) add a `next.config.ts` rewrite `/api/webhooks/resend →
  /api/proxy/api/v1/webhooks/resend` **and** add `svix-id`, `svix-timestamp`, `svix-signature` to
  `PASS_REQUEST_HEADERS` in `frontend/app/api/proxy/[...path]/route.ts` (core) — the proxy currently
  drops them, so every event would fail with 401.
- `frontend/vercel.json` still lists the two crons; it is superseded in wave 3 (worker runs them).
- `settings.OUTREACH_DAILY_CAP` defaults to **200**; the TypeScript defaulted to **50**. Set
  `OUTREACH_DAILY_CAP=50` in `.env.example` (or change the default) to keep today's volume.

## Open questions / decisions made
1. **Webhook updates rows instead of inserting `status = <event type>`.** The TS inserted
   `status: "email.bounced"` etc., which violates the Postgres CHECK on `email_send_log.status`
   (it only "worked" because the insert error was swallowed). Now the event is mapped
   (`email.sent|delivered|opened|clicked|bounced|complained` → same word) and the rows whose
   `resend_message_id` equals `data.email_id` are updated (`status`, `updated_at_attr`); when no row
   matches, one row is inserted with `consent_basis = "webhook_event"`, `email_type = "intro"` as
   before. Unmapped events (`email.delivery_delayed`) write nothing.
2. **Message ids under SMTP.** Sends now go through `app.services.email` (SMTP); `resend_message_id`
   stores the returned SMTP `Message-ID`. Resend's webhook `email_id` is Resend's own id, so via Resend
   SMTP the two will not match and events fall back to the insert path in (1). Bounce/complaint
   suppression works regardless (it keys on the recipient address). If exact correlation matters,
   `services.email.send` would need to return Resend's id (e.g. from the SMTP `250` reply) — a core change.
3. **Briefing `content`.** `app.briefings_meta` has no `content` column; the templates render it only
   if present. Rows are read with `to_jsonb`, so if editorial adds `content` it appears automatically.
   Editorial: `briefing_send`/`outreach_send` read `title, summary, why_it_matters, action_checklist,
   scheduled_for, status, outreach_used_at` and write `status, sent_at, subscriber_count,
   outreach_used_at` — please keep those column names.
4. **For editorial:** import `eligible_subscribers`, `send_briefing_to_subscribers`,
   `build_unsubscribe_url` from `app.jobs.outreach` for approve/send instead of re-porting
   `lib/sendGateway.ts` / `sendBriefingToSubscribers`.
5. **Admin auth errors** are now the shared 401 `Not authenticated` / 403 `Access denied.` instead of
   `{"error":"Unauthorized"}`; the admin page ignores both (it checks `error`/`detail`).
6. **New endpoint** `GET /outreach/contacts` replaces the admin page's generic
   `/api/admin/data?collection=outreach_contacts` call (same `{documents,total}` shape, `$id` kept,
   newest first, limit ≤ 500), so the page no longer depends on the admin module's data route.
7. **CSV import**: Python's `csv` module replaces the hand-written parser (same RFC-4180 semantics,
   blank lines dropped, 10 000-row cap). A UTF-8 BOM (Excel's default CSV export) is now stripped, so
   an `Email` header exported from Excel is detected — the TS failed on it.
8. **CRON_SECRET unset** now rejects every trigger (TS compared against `"Bearer undefined"`).
9. Contact names in the outreach email are HTML-escaped; approved briefing fields stay unescaped
   (they are editorial HTML), as before.
10. Weekday for weekly subscribers and all email dates are computed in UTC (Vercel ran in UTC).

## Static checks (run once, on outreach files only)
- `ruff check` + `ruff format` on routes/outreach.py, routes/webhooks.py, models/outreach.py,
  crud/outreach.py, jobs/outreach.py, tests/outreach → All checks passed
- `mypy` on the five source files → no issues
- `python -m compileall -q backend/app` → clean
- `tsc --noEmit -p frontend/tsconfig.json` → 0 source errors; only stale `.next/types/validator.ts`
  references to the moved route handlers (regenerated by the next `next build`).
No test was executed.
