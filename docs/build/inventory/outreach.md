# outreach inventory

Sources: `frontend/app/api/outreach/{import,stats,subscribe,unsubscribe}/route.ts`,
`frontend/app/api/cron/{outreach-send,briefing-send}/route.ts`,
`frontend/app/api/webhooks/resend/route.ts`, `frontend/lib/{email-templates.ts (briefingEmailTemplate,
outreachBriefingEmail), sendGateway.ts, resendClient.ts, email.ts (sendBriefingToSubscribers /
sendSubscriberBriefing)}`, `frontend/vercel.json`.

Env read: `CRON_SECRET`, `OUTREACH_DAILY_CAP` (TS default 50; settings default 200 — see status Q),
`NEXT_PUBLIC_SITE_URL` (default `https://saralprivacy.com`), `RESEND_API_KEY` (→ SMTP now),
`RESEND_FROM_BRIEFINGS` (default `briefings@saralprivacy.com` → `settings.EMAILS_FROM_BRIEFINGS`),
`RESEND_WEBHOOK_SECRET`, `EMAIL_LINK_SECRET`.

## Routes
| # | Method + path | Auth / rate limit | Request | Side effects | Response | Callers | Test written |
|---|---|---|---|---|---|---|---|
| 1 | POST /api/outreach/import → `/api/v1/outreach/import` | admin (401 `Unauthorized`) | multipart `file`; missing → 400 `No file uploaded.`; > 2 MB → 413 `File too large (max 2 MB). Export as CSV.`; name not `.csv` → 400 `Only CSV files are accepted. In Excel: File → Save As → CSV.`; < 2 grid rows → 400 `Spreadsheet is empty.`; no email column → 400 ``Cannot detect email column. Columns found: ${headers}. Rename your email column to "Email".``; ≤ 10 000 rows; header aliases for email/name/company/industry | inserts `ops.outreach_contacts` (source `excel_import_v1`, status `pending`, 32-byte base64url `magic_token`, `created_at_attr` now, name/company/industry only when non-empty); skips invalid (regex `^[^\s@]+@[^\s@]+\.[^\s@]+$`) and duplicates (existing + in-file, lowercased) ; per-row insert failures counted, not fatal | 200 `{success:true,total,inserted,duplicates,invalid}`; 500 `{error}` | admin/outreach/page.tsx:66 | tests/outreach/test_import.py |
| 2 | GET /api/outreach/stats → `/api/v1/outreach/stats` | admin | — | read | `{total,pending,sent,subscribed,bounced,unsubscribed,complained}` | admin/outreach/page.tsx:33 | test_stats.py |
| 3 | POST /api/outreach/subscribe → `/api/v1/outreach/subscribe` | public | `{token}` non-empty string else 400 `Invalid token.` | unknown token → 404 `Link not recognised or already used.`; status `subscribed` → no writes; else if no `ops.subscribers` row for email: insert (frequency daily, consent_source `intro_email_one_click`, status active, consent_version `PRIVACY_NOTICE_VERSION`, empty ip/geo/UA) + best-effort `ops.consent_log` (source `outreach_magic_link`, consent_type `email_marketing`, value true); contact → `subscribed`, `subscribed_at` now | `{success:true,already:bool,name}` | [locale]/subscribe/MagicSubscribeClient.tsx:14 | test_subscribe.py |
| 4 | POST /api/outreach/unsubscribe → `/api/v1/outreach/unsubscribe` | public | `{token}` else 400 `Invalid token.` | unknown → 404 `Link not recognised.`; already → none; else status `unsubscribed` | `{success:true,already:bool}` | [locale]/unsubscribe/outreach/OutreachUnsubscribeClient.tsx:14 | test_unsubscribe.py |
| 5 | GET /api/cron/outreach-send → `/api/v1/cron/outreach-send` (+ job) | `Authorization: Bearer CRON_SECRET` else 401 `Unauthorized` | — | briefing: oldest approved/sent with `outreach_used_at` null by `scheduled_for` asc, else newest approved/sent (none → 404 `No approved briefing found. Approve a briefing first.`); up to DAILY_CAP pending contacts by created_at asc (none → 200 `{message:"No pending contacts. Campaign complete.",briefing_used}`); per contact: ensure token, render outreach email, send from `SaralPrivacy <briefings@news.saralprivacy.com>` reply-to `privacy@saralprivacy.com`; failure → contact `failed`; success → contact `sent` + `intro_sent_at`, `email_send_log` (intro, message id, sent, `one_time_dpdpa_sensitization`); if briefing was unused and sent>0 → set `outreach_used_at` | `{sent,failed,remaining,briefing_used,briefing_id}` | vercel cron `0 4 * * *` | test_outreach_send.py |
| 6 | GET /api/cron/briefing-send → `/api/v1/cron/briefing-send` (+ job) | Bearer CRON_SECRET | — (maxDuration 300) | newest `approved` briefing by scheduled_for desc (none → 404 same string); audience = sendGateway (all subscribers, not unsubscribed/bounced/complained, deduped lowercased); weekly subscribers only on Monday (UTC); none → `{message:"No eligible subscribers today.",briefing_used}`; per subscriber: signed unsubscribe URL, send from FROM_BRIEFINGS with `List-Unsubscribe: <url>`, 100 ms pause; log `briefing_daily`/`briefing_weekly`, `explicit_consent`; then briefing → `sent`, `sent_at`, `subscriber_count` | `{sent,failed,total_eligible,briefing_used,briefing_id}` | vercel cron `30 4 * * *` | test_briefing_send.py |
| 7 | POST /api/webhooks/resend → `/api/v1/webhooks/resend` | svix signature with `RESEND_WEBHOOK_SECRET` | raw body; headers svix-id/-timestamp/-signature | no secret → 500 `Webhook not configured`; bad sig → 401 `Invalid signature`; email = `data.to[0]` ?? `data.email` lowercased, missing → 400 `No email in payload`; log event in `email_send_log`; `email.bounced` → outreach_contacts + subscribers status `bounced`; `email.complained` → `complained`; `email.delivery_delayed` → log only | `{received:true,event,email}` | Resend | test_webhooks.py |
| 8 (new) | GET `/api/v1/outreach/contacts?status=&limit=` | admin | limit ≤ 500 default 200 | read, created desc | `{documents:[...], total}` — replaces this page's use of `/api/admin/data?collection=outreach_contacts` | admin/outreach/page.tsx:41 | test_stats.py |

## Server-rendered pages reading these tables
None.

## Jobs / crons
| Name | Schedule (UTC) | What it does | Idempotency |
|---|---|---|---|
| outreach-send | `0 4 * * *` | row 5 | only `pending` contacts are picked; sent ones become `sent`, so a rerun sends to the next batch only |
| briefing-send | `30 4 * * *` | row 6 | only `approved` briefings; marks it `sent`, so a rerun finds nothing (404 / not ok) |

## Library logic to rewrite
| Source | Behaviour | Notes |
|---|---|---|
| sendGateway.fetchEligibleSubscribers | all subscribers, drop suppressed, dedupe lowercase, default frequency daily | one query in Python, no page cap needed |
| sendGateway.buildUnsubscribeUrl | `${SITE}/unsubscribe?email=<enc>[&sig=<hex hmac>]` | same secret/algorithm as forms `verify_unsubscribe_sig` |
| email-templates.briefingEmailTemplate | subject `DPDPA Daily Brief: ${title}`; date en-IN long | `outreach/briefing.html` |
| email-templates.outreachBriefingEmail | subject = title; `firstName` or `there`; why_it_matters JSON → hook_line1/why/raw; html + text | `outreach/outreach_briefing.{html,txt}` |
| email.ts sendBriefingToSubscribers / sendSubscriberBriefing | used by editorial approve/send | exported as `app.jobs.outreach.send_briefing_to_subscribers` for editorial |
| resendClient | lazy Resend client | replaced by `app.services.email` |

## Strings to copy verbatim
All error strings above; email copy in the two templates (copied byte-for-byte into Jinja).

## Out of scope / kept in frontend
`lib/email.ts`, `lib/email-templates.ts`, `lib/sendGateway.ts`, `lib/resendClient.ts` are still imported
by editorial (`api/briefings/{generate,approve,send}`) and admin (`api/admin/{send-report,bloggers}`)
route handlers, so they stay live until those modules move them.
