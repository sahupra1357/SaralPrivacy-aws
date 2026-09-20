# forms inventory

Source read once: `frontend/app/api/{contact,subscribe,subscribers/unsubscribe,survey/submit,template-download,templates/download,white-paper}/route.ts`
and `frontend/lib/{subscribers.ts,suppression.ts,templates/*}`, plus the helpers those
import (`lib/abuseGuard.ts`, `lib/db`, `lib/email.ts`, `lib/email-templates.ts`,
`lib/sendGateway.ts`, `lib/utils.ts` `PRIVACY_NOTICE_VERSION = "1.0.0"`,
`lib/data/guide-languages.ts`).

Shared facts carried into every row:

- Honeypot field is `hp_url` (`abuseGuard.isHoneypotTripped`: tripped when the value is a
  non-empty string after trim). Tripped ⇒ pretend success, store nothing.
- Client IP: `x-forwarded-for` first entry (routes) / `abuseGuard.getClientIp` (rate-limit
  keys); geo from `x-vercel-ip-city` (URL-decoded), `x-vercel-ip-country`,
  `x-vercel-ip-country-region`; `user-agent` stored on consent rows.
- `lib/db` renames the app field `created_at` to the Postgres column `created_at_attr`
  for `leads`, `subscribers`, `survey_responses`, `template_downloads` (see
  `lib/db/supabase.ts` line 53-63). `consent_log` and `downloads` are not renamed.
- No cron / job in this module. No `maxDuration` export on any of the seven routes.

## Routes

| # | Method + path | Auth / rate limit | Request (fields, validation, limits) | Side effects | Response | Callers (file:line) | Test written |
|---|---|---|---|---|---|---|---|
| 1 | POST `/api/contact` → `/api/v1/forms/contact` | none; `rateLimit("contact:<ip>", 6, 60_000)` | JSON `fullName, workEmail, mobileNumber?, companyName, industry?, companySize?, issueSummary, preferredContact?, preferredTime?, consentContact, hp_url?`. Honeypot first. Required: `fullName, workEmail, companyName, issueSummary, consentContact` (truthy) | insert `ops.leads` (source `"consultation"`, `consent_version` 1.0.0, `risk_level` `""`, ip/city/country/region, `created_at_attr` now); fire-and-forget insert `ops.consent_log` (source `contact`, consent_type `data_processing`, value true); fire-and-forget admin email `consultationAlertTemplate` to ADMIN_EMAIL from FROM_NOREPLY | 200 `{success:true, message:"Your consultation request has been received. We will respond within one business day."}`; honeypot 200 `{success:true, message:"Your request has been received."}`; 400 `{error:"Required fields are missing."}`; 429 `{error:"Too many requests. Please wait a moment and try again."}` + `Retry-After`; 500 `{error:"An unexpected error occurred. Please try again."}` | `app/[locale]/contact/ContactContent.tsx:66` | yes |
| 2 | POST `/api/subscribe` → `/api/v1/forms/subscribe` | none; `rateLimit("subscribe:<ip>", 6, 60_000)` | JSON `name?, email, industry?, frequency?, consentEmail, hp_url?`. Honeypot first. Required `email` + `consentEmail`; email must match `/\S+@\S+\.\S+/` | find `ops.subscribers` by email; if found and `status != "active"` → update `status="active"`, `consent_version` 1.0.0; if absent → insert (name defaults to email local-part, `frequency` default `"daily"`, `consent_source="manual"`, `status="active"`, user_agent, ip/city/country/region, `created_at_attr` now); fire-and-forget `ops.consent_log` (source `subscribe`, type `email_marketing`); fire-and-forget welcome email `welcomeEmailTemplate` from FROM_BRIEFINGS | 200 `{success:true, message:"Subscription successful. Check your inbox for confirmation."}`; honeypot 200 `{success:true, message:"Subscription successful."}`; 400 `{error:"Email and email consent are required."}`; 400 `{error:"Invalid email address."}`; 429 same string as row 1; 500 `{error:"An unexpected error occurred. Please try again."}` | `components/home/NewsletterSection.tsx:27`, `components/briefings/BriefingSubscribeCard.tsx:20` | yes |
| 3 | POST `/api/subscribers/unsubscribe` → `/api/v1/forms/subscribers/unsubscribe` | none; rate limit **only when the HMAC sig is absent/invalid**: `rateLimit("unsub:<ip>", 5, 3_600_000)` | JSON `email` (required), `sig?`. Email normalised `trim().toLowerCase()`. `sig` = hex HMAC-SHA256 of the normalised email with `EMAIL_LINK_SECRET`; constant-time compare; no secret ⇒ never valid | if subscriber absent: nothing; else update `ops.subscribers` `status="unsubscribed"`, `unsubscribed_at=now` | 200 `{success:true}`; 200 `{success:true, already_removed:true}` when the email is unknown; 400 `{error:"email required"}`; 429 `{error:"Too many requests. Try again later or email privacy@saralprivacy.com."}` + `Retry-After`; 500 `{error:"<exception message>"}` | `app/[locale]/unsubscribe/page.tsx:20`, `app/[locale]/consent-preferences/ConsentPreferencesContent.tsx:24` | yes |
| 4 | POST `/api/survey/submit` → `/api/v1/forms/survey/submit` | none; `rateLimit("survey:<ip>", 8, 60_000)` | JSON `answers` (object), `score` (`{score, band, summary, recommendations?, riskFlags?}`), `hp_url?`. Honeypot first. Both `answers` and `score` must be truthy | insert `ops.survey_responses` (**non-fatal** — a failure is logged, the response is still 200). `data_types`, `data_storage`, `controls_in_place` stored as JSON strings; `consent_given=true`, `consent_version="v1.0"` (note: literal, *not* PRIVACY_NOTICE_VERSION), `consent_timestamp`/`created_at_attr` = now ISO. `wants_report = answers.want_detailed_report === "Yes, send it to me"`. If `wants_report && answers.work_email`: `sendSurveyResultEmail` (`surveyResultEmailTemplate`, from FROM_BRIEFINGS) with `answerSummary: []`, no `categoryScores`, no `reportToken` — failure logged, not fatal | 200 `{success:true}`; honeypot 200 `{success:true}`; 400 `{error:"Missing answers or score"}`; 429 same string as row 1; 500 `{error:"Submission failed"}` | none in-repo (public JSON endpoint; the survey UI was retired — kept for parity) | yes |
| 5 | POST `/api/template-download` → `/api/v1/forms/template-download` | none; `rateLimit("tmpl:<ip>", 8, 60_000)` | JSON `businessName, employees, contactName, phone, consentContact?, consentBriefings?, templateName?, reportToken?, email?, source?, inventoryCsv?, nicheName?, hp_url?`. Rate limit runs **before** the honeypot. Required: `businessName, contactName, phone, employees` | insert `ops.template_downloads` (`source` default `"report_page"`, `report_token` default `""`, `consent_contact` default false, ip/city/country, `created_at_attr` now); if `consentBriefings && email` → `upsertSubscriber(source="template_form")`; if `source === "discovery"`: when `email && inventoryCsv` send the inventory email (subject `Your DPDPA personal data inventory — <nicheName>`, CSV attachment `dpdpa-personal-data-inventory.csv`) and always send the admin alert (subject `New Data Discovery lead — <businessName>`). All three are fire-and-forget | 200 `{success:true}`; honeypot 200 `{success:true}`; 400 `{error:"Required fields missing."}`; 429 same string as row 1; 500 `{error:"<exception message>"}` | `app/[locale]/discovery/components/ResultPanel.tsx:144`, `components/TemplateGateModal.tsx:72`, `components/ResourceTemplateGate.tsx:111` | yes |
| 6 | POST `/api/templates/download` → `/api/v1/forms/templates/download` | none; honeypot **first**, then `rateLimit("template-download:<ip>", 5, 600_000)` | JSON validated by `TemplateDownloadFormSchema` (zod): `email` (email, "Please enter a valid email address"), `contactPersonName` (2-100, "Name must be at least 2 characters" / "Name too long"), `businessName` (2-200, "Business name must be at least 2 characters" / "Business name too long"), `templateSelected` ∈ 5 ids ("Please select a template"), `phoneNumber` valid IN number ("Please enter a valid Indian phone number (+91 format)"), `consentContact` bool, `consentBriefings` bool | resolve download URL — **Vercel Blob is gone**: always `${NEXT_PUBLIC_SITE_URL}/templates/<id><ext>`; insert `ops.template_downloads` via `buildTemplateLeadDocument` (`source` = referer or `"direct"`, truncated to 64 chars; `template_name` = display name; phone in E.164) — **non-fatal**; if `consentBriefings` → `upsertSubscriber(source="template_download")` (non-fatal); send the template email (**required**, subject `Your "<templateName>" is ready — download now`, from `RESEND_FROM_NOREPLY` / `EMAILS_FROM_NOREPLY`); if `consentContact` → Twilio WhatsApp (optional, degrades: missing `TWILIO_*` ⇒ `{success:false, error:"Twilio not configured"}`, never fatal) | 200 `{success:true, message:"Template sent successfully", downloadUrl, email:true, whatsapp:<bool>}`; honeypot 200 `{success:true}`; 400 `{message:"Invalid form data", errors:{<field>:[msgs]}}`; 429 `{error:"Too many requests. Please try again later."}` + `Retry-After`; 500 `{message:"Failed to send template email. Please try again."}`; 500 `{message:"Internal server error. Please try again."}` | `components/TemplateDownloadForm.tsx:90` | yes |
| 7 | POST `/api/white-paper` → `/api/v1/forms/white-paper` | none; honeypot **first**, then `rateLimit("white-paper:<ip>", 5, 600_000)` | JSON `fullName, workEmail, companyName, industry, companySize, phone?, language?, consentEmail?, consentPhone?, consentWebinars?, hp_url?`. All five of fullName/workEmail/companyName/industry/companySize required. `language` resolved through `getLanguage()` → falls back to `"en"` for anything unknown | insert `ops.downloads` (with `language`; the TS retried once without `language` on failure — the Postgres column exists, so the retry is dropped); one `ops.consent_log` row per checked consent (`email_marketing` / `phone_contact` / `webinars`, source `download`) fire-and-forget; fire-and-forget admin alert `downloadAlertTemplate`; if `consentEmail` → `upsertSubscriber(source="whitepaper_form")` | 200 `{success:true, downloadUrl, language, partial:<bool>, message:"Download ready."}`; honeypot 200 `{success:true}`; 400 `{error:"Required fields are missing."}`; 429 `{error:"Too many requests. Please try again later."}` + `Retry-After`; 500 `{error:"An unexpected error occurred."}` | `app/[locale]/white-paper/WhitePaperContent.tsx:75` | yes |

## Server-rendered pages reading these tables

None. Every consumer is a client-side `fetch`. (`/admin/leads`, `/admin/downloads`,
`/admin/consent`, `/admin/consultations` read `ops.leads` / `ops.downloads` /
`ops.consent_log` / `ops.subscribers`, but those pages belong to the **admin** module.)

## Jobs / crons

None. `vercel.json` has no cron entry for any forms path.

## Library logic to rewrite (not routes)

| Source file | Behaviour to keep | Notes |
|---|---|---|
| `lib/subscribers.ts` | `upsertSubscriber`: normalise email; **if a row already exists, do nothing at all** (no consent_log either); else insert `ops.subscribers` (`frequency="daily"`, `status="active"`, `consent_version` 1.0.0, geo/UA, `created_at_attr` now) plus a fire-and-forget `ops.consent_log` row (`consent_type="email_marketing"`, `source` = the caller's string) | `consent_source` was never set by this helper (a NOT NULL column in the new schema) — the rewrite sets `"manual"`, the same value the `/subscribe` route uses. The caller's `source` keeps going to `consent_log.source` verbatim. Still imported by `app/api/assessment/route.ts` (assessments module) at the time of writing |
| `lib/suppression.ts` | `isSuppressed(email)`: true when a `subscribers` **or** `outreach_contacts` row for the normalised email has status in `["unsubscribed","bounced","complained"]` | No importer left in `frontend/` — dead code today, but the outreach/editorial send paths need it in Python. Ported as `crud.forms.is_suppressed` |
| `lib/templates/validation.ts` | Template ids, display names, file extensions, Indian phone validation/E.164 formatting, the zod schema and its messages | Still imported by `components/TemplateDownloadForm.tsx` (client-side form validation) ⇒ stays live in the frontend; the server half is re-expressed as Pydantic |
| `lib/templates/lead.ts` | `buildTemplateLeadDocument`: fixed key set, `source = (referer ?? "direct").slice(0, 64)` | Only the route imported it ⇒ superseded |
| `lib/templates/contact-storage.ts` | localStorage contact memory + `sp_rg_v1` unlock flag | Pure client, untouched |
| `lib/sendGateway.ts` `verifyUnsubscribeSig` | hex HMAC-SHA256 over the normalised email with `EMAIL_LINK_SECRET`, constant-time compare, no secret ⇒ false | Ported as a private helper in `routes/forms.py`; the file itself belongs to the outreach module |

## Strings to copy verbatim

Error / success strings — all listed per row above. Also:

- Template display names: `Privacy Notice Template`, `Consent Language Examples`,
  `Data Inventory Register`, `DSR & Grievance SOP`, `Vendor Data Sharing Register`.
- Template email subject: `Your "<templateName>" is ready — download now`; body headings
  `SaralPrivacy` / `DPDPA Compliance for Indian Businesses`, CTA `⬇ Download Template`,
  note `What's in this template?` / "This is a ready-to-use DPDPA compliance document.
  Review it with your legal team before publishing or sharing externally.", the
  briefings line "You're subscribed to daily DPDPA briefings — expect your first one
  tomorrow morning.", footer "You received this because you downloaded a template from
  saralprivacy.com."
- WhatsApp body: ``Hi <name>! 👋\n\nYour *<templateName>* from SaralPrivacy is ready.\n\n⬇ Download here:\n<url>\n\n— SaralPrivacy DPDPA Templates``
- Admin alert subjects: `New Consultation Request — <name> from <company>`,
  `White Paper Downloaded — <name> from <company>`,
  `New Data Discovery lead — <businessName>`.
- Subscriber subjects: `Welcome to SaralPrivacy Daily Briefings`; survey report subject
  varies by band — `Your DPDPA Score: <n>/100 — Here's exactly why and what to do first`
  (Not Started / Early Stage), `… — Strong start. Here's what to protect`
  (Operationally Strong), `… — You're building. Here's the path to 70+` (otherwise).
- Discovery inventory subject: `Your DPDPA personal data inventory — <nicheName>`.
- Survey-report disclaimer: "**Disclaimer:** Information on this report is for
  educational purposes only and does not constitute formal legal advice. Consult a
  qualified professional for legal guidance." (copied into the Jinja template verbatim,
  together with every band CTA headline/body/label).
- `PRIVACY_NOTICE_VERSION = "1.0.0"`; survey rows keep their own literal `"v1.0"`.

## Environment variables read

`EMAIL_LINK_SECRET` (unsubscribe HMAC), `NEXT_PUBLIC_SITE_URL` (template download URLs),
`ADMIN_EMAIL`, `RESEND_FROM_NOREPLY`/`RESEND_FROM_BRIEFINGS` → `EMAILS_FROM_NOREPLY`/
`EMAILS_FROM_BRIEFINGS`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`,
`BLOB_READ_WRITE_TOKEN` (**dropped** — Vercel Blob is gone).

## Out of scope / kept in frontend

- `lib/templates/validation.ts` and `lib/templates/contact-storage.ts` (client form).
- `lib/data/guide-languages.ts` — the single source of truth for next-intl routing,
  sitemap and hreflang; **not** a forms file. The backend mirrors only its `code →
  pdfUrl` table (still the published Vercel-Blob guide URLs; no guide PDF exists under
  `public/`).
- CSV building and the client-side download in `ResultPanel.tsx` (unchanged).
- `lib/email.ts`, `lib/email-templates.ts`, `lib/abuseGuard.ts`, `lib/db`,
  `lib/sendGateway.ts` — owned by core / outreach; read only.
