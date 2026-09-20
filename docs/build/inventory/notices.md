# notices inventory

Source read (2026-09-18): `frontend/app/api/notice/{capture,events,pdf}/route.ts`,
`frontend/lib/notice-pack/{render,engine,data,types,track,profile,category-overlays}.ts`,
`frontend/lib/{abuseGuard,email,email-templates,db/supabase}.ts`,
`frontend/app/[locale]/tools/dpdpa-privacy-notice-generator/*`,
`_backup/supabase/migrations/0001_initial_schema.sql`, `frontend/vercel.json`.

## Routes

| # | Method + path | Auth / rate limit | Request (fields, validation, limits) | Side effects | Response (shape, status, exact error strings) | Callers (file:line) | Test written |
|---|---|---|---|---|---|---|---|
| N1 | `POST /api/notice/capture` → `POST /api/v1/notices/capture` | public. `rateLimit("notice-capture:<ip>", 8, 60_000)` | JSON. `email` required, must be an email. `name` ≤120 → `""`. `business_name` ≤160 → `""`. `sector` ≤80 → `""`. `readiness_score` int 0–100 optional. `export_type` one of `pdf`\|`copy`\|`pack`, optional. `source` ≤60 → `"notice-generator"`. `consent` bool → `false`. Honeypot field `hp_url` (any non-blank string) ⇒ silent success, nothing stored. Unknown keys ignored. | insert 1 row `app.notice_captures` (`name` falls back to the local part of the email; `readiness_score` defaults 0; `export_type` defaults `""`; `ip_address` = client IP; `city` = `decodeURIComponent(x-vercel-ip-city)`; `country` = `x-vercel-ip-country`; the app-side `created_at` ISO string is written to column `created_at_attr`, per `RENAMES` in `lib/db/supabase.ts`). Then a **fire-and-forget** founder alert email to `ADMIN_EMAIL` from `RESEND_FROM_NOREPLY`; failures are logged and never fail the request. | 200 `{"success": true}` (also for honeypot). 400 `{"error":"Invalid email or fields."}`. 429 `{"error":"Too many requests. Please wait a moment and try again."}` + `Retry-After`. 500 `{"error":"An unexpected error occurred. Please try again."}` | `app/[locale]/tools/dpdpa-privacy-notice-generator/NoticePackClient.tsx:213` (response ignored) | yes |
| N2 | `POST /api/notice/events` → `POST /api/v1/notices/events` | public. `rateLimit("notice-events:<ip>", 60, 60_000)`; **over limit is dropped silently with 200 `{"ok":true}`, not 429** | JSON. `name` must be one of the 14 event names below. `session_id` ≤64 → `""`. `sector` ≤80 optional. `score` int 0–100 optional. `context` ≤60 optional. Anything invalid is dropped, not rejected. | insert 1 row `app.notice_events` (`payload` = compact JSON of the remaining parsed fields in schema order `sector, score, context`, absent keys omitted; app-side `created_at` ISO string → column `created_at_attr`). Every failure path (rate limit, validation, DB, JSON) returns success — analytics must never break UX. | always 200 `{"ok": true}` | `lib/notice-pack/track.ts:39` (`keepalive`, response ignored) | yes |
| N3 | `POST /api/notice/pdf` → `POST /api/v1/notices/pdf` | public. `rateLimit("notice-pdf:<ip>", 6, 60_000)`. `runtime = "nodejs"`, **`maxDuration = 60`** | JSON = the whole `NPState` plus optional `effIso` (ISO datetime). Clamps: `org`≤160, `website`≤200, `sector`≤80, `data` ≤80 items ×≤120 chars, `contexts` ≤40×≤60, `purpose` record of ≤400-char strings, `consentVia` ≤20×≤80, `withdrawMethod` ≤20×≤80, `withdrawContact`≤200, `vendors` ≤40×≤120, `noVendors` bool → false, `children` ∈ `""`\|`Yes`\|`No`\|`Not sure`, `childWhy` ≤20×≤120, `retention`≤60, `cName`≤120, `cEmail`≤160, `cPhone`≤40, `regAddress`≤240, `slug`≤80, `lang` ∈ `en`\|`hi` → `en`. All strings default `""`, all arrays default `[]`. | none in the DB. Renders `noticeDocumentHtml(state, {effIso})` and prints it to PDF: A4, `printBackground`, `displayHeaderFooter` with `PDF_HEADER`/`PDF_FOOTER`, margins `{top:18mm, bottom:20mm, left:16mm, right:16mm}`. Browser closed in `finally`. | 200 `application/pdf`, `Content-Disposition: attachment; filename="dpdpa-privacy-notice-<slug>.pdf"` (slug = lowercased org, non-alnum → `-`, trimmed of leading/trailing `-`, first 60 chars, else `your-business`), `Cache-Control: no-store`. 400 `{"error":"Invalid request body."}` (body is not JSON) or `{"error":"Invalid notice data."}` (schema fails). 429 `{"error":"Too many requests. Please wait a moment and try again."}` + `Retry-After`. 500 `{"error":"Could not generate the PDF. Please try the print fallback."}` | `NoticePackClient.tsx:159` (checks `res.ok`, reads blob, falls back to browser print on any failure) | yes |

Event names accepted by N2 (exact, in order):
`notice_builder_started`, `business_type_selected`, `data_categories_confirmed`,
`purpose_matrix_completed`, `collection_contexts_selected`, `notice_preview_generated`,
`notice_score_calculated`, `notice_lead_captured`, `notice_pdf_downloaded`,
`notice_html_copied`, `mini_notice_copied`, `consent_block_copied`, `dsar_cta_clicked`,
`notice_evidence_record_created`.

## Server-rendered pages reading these tables

| Page | Query it needs | Cache/ISR setting |
|---|---|---|
| — | none. `app/[locale]/tools/dpdpa-privacy-notice-generator/page.tsx` is a static marketing shell that renders the client wizard; it reads no table. | static |

The admin dashboard does not read `notice_captures` (`grep` over `app/(backoffice)` finds
no reference) — the captures are a founder-email + analytics store only.

## Jobs / crons

| Name | Schedule (UTC) | What it does | Idempotency |
|---|---|---|---|
| — | — | `vercel.json` declares no cron in this module. | — |

## Library logic to rewrite (not routes)

| Source file | Behaviour to keep | Notes |
|---|---|---|
| `lib/notice-pack/render.ts` → `backend/app/notice_pack/render.py` | `noticeDocumentHtml()` (full standalone `<html>` with `DOC_CSS` inlined), `PDF_HEADER`, `PDF_FOOTER`. Byte-for-byte the same document as the client's Copy-HTML / print fallback. | **Stays in the frontend too** — the client uses it for Copy HTML and the print fallback. Partial supersession: the file is copied to `_backup/` and kept live. |
| `lib/notice-pack/engine.ts` → partially, `backend/app/notice_pack/render.py` | `escHtml` (escapes `&`, `"`, `<` — **not** `>`), `slugify`, `buildNotice`, `vclause`, `sharingHtml` + `VENDOR_PURPOSE`, `retText`. | `score`, `band`, `flags`, `evidence`, `dsarLink`, `rightsBlock`, `miniFor`, `newNoticeId`, `sha256`, `isVague`, `hasWithdrawalChannel` are **client-only** (wizard scoring UI) and stay in TS. |
| `lib/notice-pack/data.ts` → partially, `backend/app/notice_pack/data.py` | `SECTORS` (key → label, vclause), `CONTEXTS` (key → label), `VENDOR_CLAUSE`. | The rest (`DATA_GROUPS`, `SENSITIVE`, `DATA_PURPOSE`, `VAGUE`, `USE_CASES`, `VENDORS`, `CHILD_WHY`, `consentBlocks`, `SCORE_WEIGHTS`, `SCORE_BANDS`, `CONTEXTS[].mini`) is wizard UI data — stays in TS. |
| `lib/notice-pack/{types,track,profile,category-overlays}.ts` | unchanged | client-only. `track.ts` only changes its fetch URL. |
| `lib/email-templates.ts` → `backend/app/email-templates/notice_lead_alert.html` | `noticeLeadAlertTemplate()` subject + HTML (`baseLayout`, `badge`, `divider`, `labelRow`, `escapeHtml`). | The TS file itself is **owned by the outreach module** — not moved by notices. |
| `lib/abuseGuard.ts` | `rateLimit`, `getClientIp`, `isHoneypotTripped` (`hp_url`). | Replaced by core's `RateLimit` / `client_ip` and a local honeypot check. File owned by core. |
| `lib/db` `insertDocument` + `RENAMES` | `notice_captures.created_at → created_at_attr`, `notice_events.created_at → created_at_attr`. | Owned by core. |

Date formatting inside `buildNotice`: `new Date(effIso ?? now).toLocaleDateString(lang === "hi" ? "hi-IN" : "en-IN", {day:"numeric", month:"long", year:"numeric"})` — e.g. `18 September 2026` / `18 सितंबर 2026`.

## Strings to copy verbatim

Error / user-facing:
- `Too many requests. Please wait a moment and try again.`
- `Invalid email or fields.`
- `An unexpected error occurred. Please try again.`
- `Invalid request body.`
- `Invalid notice data.`
- `Could not generate the PDF. Please try the print fallback.`

Email (founder alert):
- subject `Notice Pack lead — <business_name || email>[ (<sector>)]`
- `ADMIN ALERT`, `Notice Pack generated`, badge `Notice Pack Lead`, rows `Email`, `Business`, `Sector`, `Readiness score` (`<n> / 100`), `Export`, missing value `—`, footer line `Captured at the export gate of the Notice Pack Builder.`

Notice document (DPDPA legal text — never rewritten). English headings
`1. Who we are` … `12. Updates to this notice`; the Hindi equivalents; `Effective date`
/ `प्रभावी तिथि`; the intro, rights, children, board and disclaimer paragraphs; the four
`VENDOR_CLAUSE` texts; the `VENDOR_PURPOSE` map; the `retText` retention map (including
the `[set a retention period]` placeholder); `We do not share your personal data with
third parties, except where required by law.`; `— not specified yet —`; `— add
purposes —`; `SP-NOTICE-2026-(draft) · v1.0`; the PDF header `SaralPrivacy` /
`DPDPA Privacy Notice` and footer `Generated by SaralPrivacy — a practical draft, not
legal advice. Review before publishing.` — all reproduced exactly in
`backend/app/notice_pack/{data,render}.py`.

## Tables

Used by these routes (defined in `backend/app/models/notices.py`):
`app.notice_captures`, `app.notice_events`.

Owned by this module per BUILD_PLAN §3 but **not touched by any notices route** and
therefore not modelled here (see `docs/build/status/notices.md` for the hand-off):
`app.notice_runs`, `app.business_profiles`, `app.dsar_requests` (each `id, legacy_id,
created_at, updated_at, payload jsonb`) and `ops.chat_feedback` (route lives in the chat
module).

## Out of scope / kept in frontend

- The whole wizard: `app/[locale]/tools/dpdpa-privacy-notice-generator/{page.tsx,NoticePackClient.tsx,notice-pack.css}` — only its three fetch URLs change.
- `lib/notice-pack/{data,engine,types,track,profile,category-overlays}.ts` and the client half of `render.ts` (Copy HTML + print fallback need `noticeDocumentHtml` in the browser).
- GA `trackEvent.notice` in `lib/analytics` — unchanged.
- `?` The Vercel geo headers `x-vercel-ip-city` / `x-vercel-ip-country` do not exist behind the new proxy (it forwards only `accept`, `accept-language`, `user-agent`, `x-forwarded-for`), so `city`/`country` will be stored empty until the proxy forwards a geo header. Recorded as an open question.
