# forms — status: static-clean
Updated: 2026-09-18

Inventory: `docs/build/inventory/forms.md` — 7 routes, 0 jobs, 3 lib files, 10 call sites.
Every inventory row is implemented and has a test.

## Files created

Backend
- `backend/app/models/forms.py` — `Lead`, `Subscriber`, `Download`, `ConsentLog`,
  `SurveyResponse`, `TemplateDownload` (schema `ops`), `SUPPRESSED_STATUSES`
- `backend/app/crud/forms.py` — row writers, `get_subscriber_by_email`,
  `reactivate_subscriber`, `unsubscribe`, `upsert_subscriber` (lib/subscribers.ts),
  `is_suppressed` (lib/suppression.ts), `log_consent`, `PRIVACY_NOTICE_VERSION`
- `backend/app/api/routes/forms.py` — the seven routes plus the ported helpers
  (`verify_unsubscribe_sig`, `render_survey_report`, `send_template_whatsapp`,
  `template_download_url`, `resolve_language`, `guide_pdf_url`,
  `validate_template_download`, `format_phone_number`)
- `backend/app/email-templates/forms/` — `_base.html`, `_row.html`,
  `consultation_alert.html`, `download_alert.html`, `welcome.html`,
  `survey_result.html`, `discovery_inventory.html`, `discovery_lead_alert.html`,
  `template_ready.html`
- `backend/app/tests/forms/` — `__init__.py`, `test_contact.py`, `test_subscribe.py`,
  `test_unsubscribe.py`, `test_survey.py`, `test_template_download.py`,
  `test_templates_download.py`, `test_white_paper.py`, `test_crud.py` (9 files)

Frontend tests
- `frontend/tests/unit/forms/` — `contact-content.test.tsx`,
  `newsletter-section.test.tsx`, `briefing-subscribe-card.test.tsx`,
  `consent-preferences.test.tsx`, `unsubscribe-page.test.tsx`,
  `white-paper-content.test.tsx`, `template-gate-modal.test.tsx`,
  `resource-template-gate.test.tsx`, `template-download-form.test.tsx`,
  `result-panel.test.tsx` (10 files)

## Files changed (frontend call sites)

All ten browser fetches now go through `app/api/proxy/[...path]` at
`/api/proxy/api/v1/forms/...`. Markup, copy and styling untouched; the only other
change is reading the FastAPI error envelope (`data.detail`) *before* the old
`data.error`, so both shapes render the same message.

- `app/[locale]/contact/ContactContent.tsx` → `/forms/contact`
- `app/[locale]/consent-preferences/ConsentPreferencesContent.tsx` → `/forms/subscribers/unsubscribe`
- `app/[locale]/unsubscribe/page.tsx` → `/forms/subscribers/unsubscribe`
- `app/[locale]/white-paper/WhitePaperContent.tsx` → `/forms/white-paper` (+ `detail`)
- `app/[locale]/discovery/components/ResultPanel.tsx` → `/forms/template-download` (+ `detail`)
- `components/TemplateGateModal.tsx` → `/forms/template-download` (+ `detail`)
- `components/ResourceTemplateGate.tsx` → `/forms/template-download` (+ `detail`)
- `components/TemplateDownloadForm.tsx` → `/forms/templates/download` (reads `message`, unchanged)
- `components/home/NewsletterSection.tsx` → `/forms/subscribe` (+ `detail`)
- `components/briefings/BriefingSubscribeCard.tsx` → `/forms/subscribe` (+ `detail`)

Kept live and untouched: `frontend/lib/templates/validation.ts` (the client form's zod
schema, template names and phone formatting) and
`frontend/lib/templates/contact-storage.ts` (localStorage only).

## Moved to _backup (also in _backup/LEDGER.md)

Plain `mv` to `_backup/webapp/<original path>`; 11 files:
`app/api/contact/route.ts`, `app/api/subscribe/route.ts`,
`app/api/subscribers/unsubscribe/route.ts`, `app/api/survey/submit/route.ts`,
`app/api/template-download/route.ts`, `app/api/templates/download/route.ts`,
`app/api/white-paper/route.ts`, `lib/subscribers.ts`, `lib/suppression.ts`,
`lib/templates/lead.ts`, `lib/templates/lead.test.ts`.
Emptied directories under `frontend/app/api/` were removed with `rmdir`.
No git command was run.

## Needs from orchestrator

- include_router — `backend/app/api/main.py`:
  ```python
  from app.api.routes import assessments, forms, login, mfa, notices, users, utils
  api_router.include_router(forms.router)
  ```
- models import — `backend/app/models/__init__.py`:
  ```python
  from app.models.forms import (  # noqa: F401
      ConsentLog,
      Download,
      Lead,
      Subscriber,
      SurveyResponse,
      TemplateDownload,
  )
  ```
  and add `"ConsentLog", "Download", "Lead", "Subscriber", "SurveyResponse", "TemplateDownload"` to `__all__`.
- jobs: none.
- dependencies: backend — add `"phonenumbers>=8.13,<10.0",` to
  `backend/pyproject.toml` `[project].dependencies`. It is the Python port of the same
  libphonenumber metadata `libphonenumber-js` used, so `/forms/templates/download`
  accepts and formats exactly the numbers the old zod refinement did. Frontend — none.
- **`backend/app/services/email.py` needs attachment support** (required by the
  discovery inventory email, which the TypeScript sent with a CSV attachment):
  add `attachments: list[dict[str, Any]] | None = None` to `send(...)` and, for each
  entry, `msg.add_attachment(a["content"], maintype=..., subtype=..., filename=a["filename"])`
  where `a["content_type"]` is e.g. `"text/csv"`. The route already passes
  `attachments=[{"filename", "content", "content_type"}]`; until the keyword exists,
  `_send_safely` catches the `TypeError` and resends without the attachment so the mail
  is never lost.
- **`backend/app/models/base.py` — `TimestampMixin` breaks for more than one table.**
  `created_at` / `updated_at` are declared with a literal `sa_column=Column(...)`, and a
  single SQLAlchemy `Column` instance cannot be attached to two `Table`s; the second
  model importing the mixin raises. `notices` alone already defines two such tables, and
  `assessments` a third. One-line-per-field fix:
  ```python
  created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True),
                               nullable=False, sa_column_kwargs={"server_default": text("now()")})
  ```
  (same for `updated_at`). `forms` does not wait on this: `models/forms.py` defines a
  local `FormsTimestampMixin` that emits identical DDL. Once base.py is fixed, the local
  mixin can be deleted and `TimestampMixin` inherited instead.

## Migration notes for the orchestrator

`ops.leads`, `ops.subscribers`, `ops.downloads`, `ops.consent_log`,
`ops.survey_responses`, `ops.template_downloads` must be created exactly as
`_backup/supabase/migrations/0001_initial_schema.sql` defines them, including the two CHECK
constraints SQLModel cannot express:

```sql
alter table ops.subscribers add constraint subscribers_consent_source_check
  check (consent_source in ('manual','assessment_form','intro_email_one_click','report_email_cta','admin_added'));
alter table ops.subscribers add constraint subscribers_status_check
  check (status in ('active','unsubscribed','bounced','complained'));
create unique index ix_subscribers_unsub_token_unique on ops.subscribers (unsubscribe_token);
create index ix_subscribers_sub_status_idx on ops.subscribers (status);
```

The assessments module writes `ops.subscribers` and `ops.consent_log` with raw SQL and
depends on these two models; both are defined here, so registering `app.models.forms`
also satisfies assessments. `crud.forms.upsert_subscriber` / `log_consent` are the
shared entry points if that raw SQL is collapsed later.

## Static checks (run once)

- `ruff check backend/app/api/routes/forms.py backend/app/models/forms.py backend/app/crud/forms.py backend/app/tests/forms` → **All checks passed**
- `python -m compileall -q backend/app` → clean
- `npx tsc --noEmit -p frontend/tsconfig.json` → **0 errors in source**. The only 15
  errors are in the stale generated `frontend/.next/types/validator.ts`, which still
  references route handlers this build moved (`app/api/contact`, `app/api/subscribe`,
  … and auth's, assessments' and notices' too). It is regenerated by the next
  `next build`; nothing to fix in source.

No test was executed. No git command that changes anything was run.

## Open questions / decisions made

1. **Error envelope.** FastAPI returns `{"detail": ...}`; the TypeScript returned
   `{"error": ...}`. Messages are byte-identical; call sites now read `detail` first and
   fall back to `error`. `/forms/templates/download` keeps `{"message", "errors"}`
   verbatim because `TemplateDownloadForm` reads `data.message`.
2. **`subscribers.consent_source`.** `lib/subscribers.ts` never set it, but the column is
   NOT NULL with a five-value CHECK that none of its `source` strings satisfy
   (`template_form`, `template_download`, `whitepaper_form`). `upsert_subscriber` now
   writes `"manual"` — the value `/subscribe` already used — and still passes the
   caller's `source` verbatim to `consent_log.source`, so the audit trail is unchanged.
   Confirm `"manual"` is the intended acquisition label for these three surfaces.
3. **Guide PDFs stay on their published Vercel Blob URLs.** Only the five *templates*
   moved to `frontend/public/templates` (the task brief). No guide PDF exists under
   `frontend/public/` — only the HTML reading pages — so `GUIDE_PDF_URLS` in
   `routes/forms.py` mirrors `lib/data/guide-languages.ts` verbatim. If those PDFs should
   also be self-hosted, that is a data-only change to that one dict plus the files.
   `lib/data/guide-languages.ts` itself is untouched: it is the single source of truth
   for next-intl routing, the sitemap and hreflang, not a forms file.
4. **`language` retry dropped.** The TypeScript retried the `downloads` insert without
   `language` because the Appwrite attribute might be missing. `ops.downloads.language`
   exists in the Postgres schema, so the retry is gone; the first insert always carries it.
5. **Stored client IP.** Three of the seven routes used `abuseGuard.getClientIp`
   (rightmost `X-Forwarded-For`) and four used the leftmost entry. All seven now use
   `deps.client_ip` — the rightmost, trusted hop — so a client cannot forge the audited
   address. Geo/user-agent capture is unchanged.
6. **`surveyResultEmailTemplate` is shared.** `assessments` and `admin` also send it
   (`/api/assessment`, `/api/admin/send-report`). It is ported here as
   `render_survey_report()` + `forms/survey_result.html`, complete with the
   `categoryScores`, `answerSummary`, `checklistUrl` and `reportToken` branches those two
   callers use. They should import it rather than porting it a second time.
7. `/forms/survey/submit` has no in-repo caller — the survey UI was retired — but the
   public endpoint is kept for parity, as the inventory records.
