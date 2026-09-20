# notices — status: static-clean
Updated: 2026-09-18

Inventory: `docs/build/inventory/notices.md` — all 3 route rows implemented and tested.
Routes moved from `/api/notice/<x>` to `/api/v1/notices/<x>`; every guard, status code
and user-facing string is unchanged. The PDF is rendered by `app.services.pdf.render_html`
(Playwright) from the same HTML, header and footer the browser uses, in `en` and `hi`.

## Files created

Backend
- `backend/app/api/routes/notices.py` — POST `/capture`, `/events`, `/pdf`
- `backend/app/models/notices.py` — `app.notice_captures`, `app.notice_events`
- `backend/app/crud/notices.py` — `create_capture`, `create_event`
- `backend/app/crud/__init__.py` — created because the package had none (docstring only;
  `deps.py` already imports `app.crud.auth`, so the auth builder needs it too)
- `backend/app/notice_pack/{__init__,data,render,state}.py` — the server half of
  `frontend/lib/notice-pack`: the notice document, its sector/context/vendor tables and
  the `NPState` shape with the PDF route's clamps
- `backend/app/email-templates/notice_lead_alert.html` — `noticeLeadAlertTemplate` port
- `backend/app/tests/notices/{__init__,conftest,test_routes,test_render,test_crud,test_email}.py`

Frontend
- `frontend/tests/unit/notices/track.test.ts`
- `frontend/tests/unit/notices/notice-pack-client.test.tsx`

## Files changed (frontend call sites)
- `frontend/app/[locale]/tools/dpdpa-privacy-notice-generator/NoticePackClient.tsx`
  — two fetch URLs only, via new constants `NOTICE_PDF_URL = "/api/proxy/api/v1/notices/pdf"`
  and `NOTICE_CAPTURE_URL = "/api/proxy/api/v1/notices/capture"`. No markup, copy or
  styling touched.
- `frontend/lib/notice-pack/track.ts` — `NOTICE_EVENTS_URL = "/api/proxy/api/v1/notices/events"`.

`lib/notice-pack/{data,engine,render,types,profile,category-overlays}.ts` are unchanged
and stay live: the wizard still scores, flags and renders the document in the browser for
Copy HTML and the print fallback. The server now holds a second copy of the *document*
logic only (`buildNotice`, `escHtml`, `slugify`, `vclause`, `sharingHtml`, `retText`,
`noticeDocumentHtml`, the PDF header/footer and the sector/context/vendor tables). **If
either side's notice wording changes, change both.**

## Moved to _backup (also in _backup/LEDGER.md)
- `webapp/app/api/notice/capture/route.ts`
- `webapp/app/api/notice/events/route.ts`
- `webapp/app/api/notice/pdf/route.ts`

`frontend/app/api/notice/` is now gone. No frontend code calls `/api/notice/*`.

## Needs from orchestrator

- include_router — `backend/app/api/main.py`:
  ```python
  from app.api.routes import notices
  api_router.include_router(notices.router)
  ```
- models import — `backend/app/models/__init__.py`:
  ```python
  from app.models.notices import NoticeCapture, NoticeEvent
  ```
  (add `"NoticeCapture", "NoticeEvent"` to `__all__`)
- jobs: **none** — `vercel.json` declares no cron in this module.
- dependencies: **none new**. Backend uses `playwright`, `jinja2`, `email-validator`,
  all already in `pyproject.toml`.
- **`frontend/package.json` (wave 3):** `"@sparticuz/chromium"` and `"puppeteer-core"`
  now have no importer anywhere in `frontend/` — safe to drop.
- **`backend/app/models/base.py` — blocking bug, affects every module.**
  `TimestampMixin` builds `created_at` / `updated_at` from module-level `Column(...)`
  objects. A `Column` instance may be attached to one `Table` only, so the *second*
  model class that inherits the mixin raises
  `sqlalchemy.exc.ArgumentError: Column object 'created_at' already assigned to Table '<first>'`.
  Reproduced with two trivial models. Fix — replace both `sa_column=Column(...)` fields
  with per-class types:
  ```python
  created_at: datetime = Field(
      default_factory=utcnow,
      sa_type=DateTime(timezone=True),
      nullable=False,
      sa_column_kwargs={"server_default": text("now()")},
  )
  ```
  (same for `updated_at`; `from sqlalchemy import DateTime, text`). Identical DDL.
  Until that lands, `app/models/notices.py` carries a private `_Timestamps` mixin with
  exactly that shape. **After the fix, delete `_Timestamps` and inherit `TimestampMixin`.**
- **Alembic migration**: the models emit the Supabase DDL verbatim — `text` columns,
  `readiness_score bigint`, `id uuid default ops.uuid_generate_v7()`, `legacy_id` unique,
  and the four named indexes `ix_notice_captures_idx_{created,sector,source}` and
  `ix_notice_events_idx_{created,name}`.
- **Tables owned by this module but not modelled** (no notices route touches them; kept
  out per the task brief). They must be created by someone or `make seed` will fail:
  - `app.notice_runs`, `app.business_profiles`, `app.dsar_requests` — each
    `id uuid pk default ops.uuid_generate_v7(), legacy_id text unique, created_at timestamptz
    not null default now(), updated_at timestamptz not null default now(), payload jsonb`
  - `ops.chat_feedback` — the chat module owns the route; columns at
    `_backup/supabase/migrations/0001_initial_schema.sql:451-464` (note the quoted camelCase
    columns `"sessionId"`, `"turnId"`, `"pageUrl"`, `"failureKind"`, `"redactedQuestion"`).
- **Long request**: the PDF route replaces a Vercel function with `maxDuration = 60`.
  Whatever proxy/ALB/uvicorn timeouts wave 3 sets must allow ~60 s for
  `POST /api/v1/notices/pdf`, and the backend image needs Playwright's Chromium.
- `ADMIN_EMAIL` must be set in `.env` — the founder alert is skipped (with a warning log)
  when it is empty. `EMAILS_FROM_NOREPLY` is the sender, as `RESEND_FROM_NOREPLY` was.

## Deliberate differences (all recorded, none user-visible)

1. **Error body shape.** The TypeScript answered `{"error": "<msg>"}`; FastAPI's
   `HTTPException` answers `{"detail": "<msg>"}`. The strings are identical and no caller
   reads the body: the wizard ignores the capture and events responses and only checks
   `res.ok` for the PDF. Following `backend-conventions` rather than adding a handler to
   the shared `main.py`.
2. **Bodies are validated by hand** (`model_validate` inside the route) instead of being
   declared in the signature, so the exact 400 texts and the events route's
   "always answer 200" contract survive; a declared body would answer 422. Cost: the
   generated TS client has no typed body for these three — the wizard calls them through
   the proxy with raw `fetch`, exactly as before.
3. **Malformed JSON on `/capture`** answers 400 `Invalid email or fields.` where the
   TypeScript fell into its catch-all and answered 500. Unreachable from the wizard.
4. **`city` / `country` will be empty.** They came from Vercel's `x-vercel-ip-city` /
   `x-vercel-ip-country`, and `frontend/app/api/proxy/[...path]/route.ts` (core-owned)
   forwards only `accept`, `accept-language`, `user-agent`, `x-forwarded-for`. The route
   still reads those two headers, so the column fills itself the moment the proxy — or a
   CloudFront/ALB geo header — supplies them. **Open question below.**
5. **Rate limiting is now Postgres-backed and shared** across containers rather than
   per-serverless-instance in-memory. Same keys (`notice-capture:<ip>`, `notice-events:<ip>`,
   `notice-pdf:<ip>`), same limits (8, 60, 6 per 60 s), same messages; strictly stronger.
6. **Effective date** is formatted from the UTC calendar day, as the Vercel function did
   (`TZ=UTC` there). Month names are the CLDR `en-IN` / `hi-IN` wide names, hard-coded in
   `notice_pack/data.py` so the output does not depend on the container's ICU data.
7. **Founder-alert HTML escaping** uses Jinja2 autoescape, which emits `&#34;`/`&gt;`
   where the TS `escapeHtml` emitted `&quot;`/`&gt;`. Renders identically; internal mail only.

## Open questions

1. **Geo headers (see difference 4).** Should the core proxy forward a geo header so
   `notice_captures.city` / `.country` keep filling? On AWS that would be a CloudFront
   viewer-country header. I did not touch the core-owned proxy. Until then both columns
   store `""` — the same value the old route stored when the header was absent.
2. **`buildNotice` does not escape the business name in the `<h1>` or the intro
   paragraph** (`t.title(n)` / `t.intro(n)` in `engine.ts`); every other field is escaped.
   I kept the behaviour byte-for-byte because the client renders the same document and the
   two must not drift, and the output is the caller's own answers rendered back to them as
   a downloaded PDF. Worth fixing on **both** sides in one change if the orchestrator
   agrees — it is a one-line `e(...)` on each side.
3. `app/email-templates/` is not assigned to a module in BUILD_PLAN §3. I added one
   uniquely-named file there (`notice_lead_alert.html`) rather than inlining the HTML in
   Python. Move it if the orchestrator wants that directory single-owner.
4. `backend/app/notice_pack/` is a new package outside the four paths in my BUILD_PLAN
   row. Nothing else imports it; the alternative was a ~600-line `routes/notices.py`.
   Rename or relocate freely.

## Static checks (run once, on this module's files only)
- `ruff check backend/app/{api/routes,models,crud}/notices.py backend/app/crud/__init__.py backend/app/notice_pack backend/app/tests/notices` — **All checks passed**
- `python -m compileall -q backend/app` — clean
- `frontend/node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` — no error in any
  notices file. Pre-existing/unrelated output: `lib/auth/session.ts:98` (auth module) and
  eight `.next/types/validator.ts` "Cannot find module .../route.js" lines — stale
  generated artefacts naming route handlers this build removed (three of them mine,
  five auth/assessments); they clear on the next `next build`.
- No test was executed. No git command that changes anything was run.
