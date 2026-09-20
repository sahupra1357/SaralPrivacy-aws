# assessments inventory

Source read once: `frontend/app/api/assessment/route.ts`,
`frontend/lib/{email.ts,email-templates.ts,subscribers.ts,abuseGuard.ts,utils.ts,db/*}`,
`frontend/app/(backoffice)/report/[token]/page.tsx`, the 13 assessment clients under
`frontend/app/[locale]/assessment/**`, `frontend/lib/data/industry-assessment/**`,
`frontend/lib/data/dpdpa-assessment.ts`, `_backup/supabase/migrations/0001_initial_schema.sql`.

`frontend/vercel.json` has **no cron** touching this module.
No `maxDuration` is declared on `app/api/assessment/route.ts` (Vercel default).

## Routes

| # | Method + path | Auth / rate limit | Request (fields, validation, limits) | Side effects | Response (shape, status, exact error strings) | Callers (file:line) | Test written |
|---|---|---|---|---|---|---|---|
| A1 | `POST /api/assessment` → **`POST /api/v1/assessments`** | public. `rateLimit("assessment:<ip>", 8, 60_000)` → `RateLimit("assessment", 8, 60)`. IP = rightmost `x-forwarded-for` hop (`getClientIp`) | JSON body. `email` **required** (only validation). Honeypot `hp_url` (non-empty string ⇒ silent accept). Legacy shape: `industry`, `riskLevel`, `scores{applicability,maturity,risk,urgency,overall}`. General/industry shape: `name`, `business`, `mobile`, `report_type`, `answers`, `result{rawScore,finalScore,verdictBand,verdictDescription,dataExposure,controlMaturity,operationalReadiness,categoryScores,redFlagsTriggered,immediateActions,thirtyDayActions}`, `consentReport`, `consentNewsletter`, `consentFollowup`, optional `city`. New (rebuild): optional `answerSummary[{question,answer}]` — see "Design decisions" | 1. insert `app.assessments` (row map below). 2. insert `ops.consent_log` (`source="assessment"`, `consent_type="data_processing"`, `consent_value=true`, `privacy_version=PRIVACY_NOTICE_VERSION` = `"1.0.0"`), fire-and-forget. 3. `sendAssessmentAlert` → admin email, fire-and-forget. 4. if `consentNewsletter && email`: `upsertSubscriber(source="assessment_form")` → `ops.subscribers` + a second `ops.consent_log` row (`consent_type="email_marketing"`), fire-and-forget. 5. if `consentReport && email`: `sendSurveyResultEmail` (awaited); on success `update app.assessments set email_sent_at=now, email_sent_by='auto'` | 200 `{"success":true,"reportToken":"<uuid4>"}`. Honeypot: 200 `{"success":true}` (nothing stored, no token). 400 `Email is required.` 429 `Too many requests. Please wait a moment and try again.` + `Retry-After`. 500 `Failed to save assessment.` | `app/[locale]/assessment/SurveyClient.tsx:670`; `ca-firms/CAAssessmentClient.tsx:263`; `clinics-diagnostic-labs/ClinicAssessmentClient.tsx:253`; `d2c-brands/D2CAssessmentClient.tsx:251`; `fintech-nbfc/FintechNbfcAssessmentClient.tsx:261`; `gyms-salons-spas/GymsSalonsSpasAssessmentClient.tsx:261`; `hotels-travel/HotelsTravelAssessmentClient.tsx:261`; `law-firms/LawFirmAssessmentClient.tsx:262`; `pharmacies/PharmaciesAssessmentClient.tsx:265`; `real-estate/RealEstateAssessmentClient.tsx:260`; `recruitment/RecruitmentAssessmentClient.tsx:249`; `schools-colleges/SchoolAssessmentClient.tsx:259`; `training-institutes/TrainingAssessmentClient.tsx:261` | yes |
| A2 | **new** `GET /api/v1/assessments/report/{token}` | public, token is the secret. `RateLimit("assessment_report", 60, 60)` | path `token` (the `report_token` uuid) | none (read only) | 200 = the assessment document, Appwrite-era field names (`created_at` mapped back from `created_at_attr`), `id` as string. 404 `Report not found.` when no row matches | replaces `findOneBy("assessments","report_token",token)` in `app/(backoffice)/report/[token]/page.tsx:…` | yes |

### Row written to `app.assessments` (A1) — exact mapping

`created_at` in the old payload is the **renamed** column `created_at_attr`
(`lib/db/supabase.ts` RENAMES). Everything else is a literal column name.

| Column | Value |
|---|---|
| `email` | `email` |
| `industry` | `answers.q1_sector \|\| industry \|\| "general"` |
| `risk_level` | `result.verdictBand \|\| riskLevel \|\| ""` |
| `applicability_score` | `scores.applicability ?? 0` |
| `maturity_score` | `scores.maturity ?? 0` |
| `risk_score` | `scores.risk ?? 0` |
| `urgency_score` | `scores.urgency ?? 0` |
| `overall_score` | `result.finalScore ?? scores.overall ?? 0` |
| `raw_score` | `result.rawScore ?? 0` |
| `final_score` | `result.finalScore ?? 0` |
| `verdict_band` | `result.verdictBand ?? ""` |
| `data_exposure` | `result.dataExposure ?? 0` |
| `control_maturity` | `result.controlMaturity ?? 0` |
| `operational_readiness` | `result.operationalReadiness ?? 0` |
| `red_flags_json` | `JSON.stringify(result.redFlagsTriggered ?? [])` |
| `q11_blocker` | `answers.q11_blocker ?? ""` |
| `q12_resource` | `answers.q12_resource ?? ""` |
| `immediate_actions_json` | `JSON.stringify(result.immediateActions ?? [])` |
| `thirty_day_actions_json` | `JSON.stringify(result.thirtyDayActions ?? [])` |
| `report_type` | `report_type ?? "quick"` |
| `name` | `name ?? ""` |
| `business_name` | `business ?? ""` |
| `mobile` | `mobile ?? ""` |
| `consent_report` / `consent_newsletter` / `consent_followup` | `?? false` |
| `created_at_attr` | now (ISO) |
| `ip_address` | leftmost `x-forwarded-for`, else `x-real-ip`, else `""` **(note: the alert/geo IP, NOT the rate-limit IP — the old route used two different rules)** |
| `city` | `body.city` (trimmed, if a non-empty string) else `decodeURIComponent(x-vercel-ip-city)` else `""` |
| `country` | `x-vercel-ip-country` else `""` |
| `region` | `x-vercel-ip-country-region` else `""` |
| `report_token` | `randomUUID()` |
| `report_token_expires_at` | now + 90 days, ISO string (**text column**) |
| `answers_json` | `JSON.stringify(answers ?? {})` |
| `category_scores_json` | `JSON.stringify(result.categoryScores ?? {})` |
| `email_sent_at` / `email_sent_by` | set only after a successful report email: ISO now / `"auto"` |

### Emails

| Name | To | From | Subject (verbatim) |
|---|---|---|---|
| assessment alert | `ADMIN_EMAIL` (`dilip.sahu@gmail.com` default) | noreply | `Assessment Completed — {industry} \| Risk: {risk_level}` |
| survey result | the submitter | briefings | band in {Not Started, Early Stage}: `Your DPDPA Score: {score}/100 — Here's exactly why and what to do first`; band = Operationally Strong: `Your DPDPA Score: {score}/100 — Strong start. Here's what to protect`; otherwise: `Your DPDPA Score: {score}/100 — You're building. Here's the path to 70+` |

`sendSurveyResultEmail` inputs: `email`, `name`, `businessName`=`business`,
`score`=`result.finalScore ?? 0`, `band`=`result.verdictBand ?? "Early Stage"`,
`summary`=`result.verdictDescription ?? ""`, `recommendations`=`result.immediateActions ?? []`,
`riskFlags`=`result.redFlagsTriggered ?? []`, `answerSummary`, `reportToken`,
`categoryScores` = `result.categoryScores` **unless** `report_type` ∈
`["ca-firm","training","recruit","d2c","clinic","school","law-firm","realty","hotel","pharmacy","fintech","wellness"]`
(industry packs use their own bucket keys, so the email scorecard is skipped),
plus `checklistUrl` / `checklistTitle` from the `report_type` table below.

| `report_type` | checklistUrl | checklistTitle |
|---|---|---|
| ca-firm | `https://saralprivacy.com/templates/ca-firm-dpdpa-starter-checklist.pdf` | `CA Firm DPDPA Starter Checklist` |
| recruit | `.../recruitment-agency-dpdpa-starter-checklist.pdf` | `Recruitment Agency DPDPA Starter Checklist` |
| d2c | `.../d2c-brand-dpdpa-starter-checklist.pdf` | `D2C Brand DPDPA Starter Checklist` |
| training | `.../training-institute-dpdpa-starter-checklist.pdf` | `Training Institute DPDPA Starter Checklist` |
| clinic | `.../clinic-diagnostic-lab-dpdpa-starter-checklist.pdf` | `Clinic & Diagnostic Lab DPDPA Starter Checklist` |
| school | `.../school-college-dpdpa-starter-checklist.pdf` | `School & College DPDPA Starter Checklist` |
| law-firm | `.../law-firm-dpdpa-starter-checklist.pdf` | `Law Firm DPDPA Starter Checklist` |
| realty | `.../real-estate-dpdpa-starter-checklist.pdf` | `Real Estate DPDPA Starter Checklist` |
| hotel | `.../hotels-travel-dpdpa-starter-checklist.pdf` | `Hotels & Travel DPDPA Starter Checklist` |
| pharmacy | `.../pharmacy-dpdpa-starter-checklist.pdf` | `Pharmacy DPDPA Starter Checklist` |
| fintech | `.../fintech-nbfc-dpdpa-starter-checklist.pdf` | `Fintech / NBFC DPDPA Starter Checklist` |
| wellness | `.../gyms-salons-spas-dpdpa-starter-checklist.pdf` | `Gym / Salon / Spa DPDPA Starter Checklist` |
| anything else | — | — |

Report link inside the email: `https://saralprivacy.com/report/{reportToken}`, else
`https://saralprivacy.com/assessment` when there is no token.

## Server-rendered pages reading these tables

| Page | Query it needs | Cache/ISR setting |
|---|---|---|
| `frontend/app/(backoffice)/report/[token]/page.tsx` | assessment by `report_token` | dynamic (no `revalidate`), `robots: index:false, follow:false`, `notFound()` when absent or on error |

The 26 files under `frontend/app/[locale]/assessment/**` are static marketing +
client components; they read no table. `app/(backoffice)/admin/assessments/page.tsx`
lists the table but belongs to the **admin** module.

## Jobs / crons

None.

## Library logic to rewrite (not routes)

| Source file | Behaviour to keep | Notes |
|---|---|---|
| `lib/abuseGuard.ts` | per-IP cap 8/60 s, honeypot `hp_url` | provided by core (`RateLimit`, `client_ip`); honeypot re-implemented locally |
| `lib/email.ts` `sendAssessmentAlert`, `sendSurveyResultEmail` | subjects + HTML above; both swallow errors and return `{success,error}` | ported as Jinja2 templates + `app/services/email.send` |
| `lib/email-templates.ts` `assessmentAlertTemplate`, `surveyResultEmailTemplate`, `baseLayout`, `escapeHtml` | full HTML, band colours, band CTAs, 5-row scorecard, top-3 answers, disclaimer | `lib/email-templates.ts` itself belongs to **outreach**; only these two templates are ported here |
| `lib/subscribers.ts` `upsertSubscriber` | skip if an `ops.subscribers` row with that email exists; else insert (`frequency="daily"`, `status="active"`, `consent_version="1.0.0"`) + an `ops.consent_log` row | file belongs to **forms**; a local copy is used here so wave-1 builders do not share a file |
| `lib/utils.ts` `PRIVACY_NOTICE_VERSION` | `"1.0.0"` | constant copied |
| `lib/db/supabase.ts` RENAMES | `assessments.created_at → created_at_attr` | honoured in the model + response mapping |

## Strings to copy verbatim

- `"Too many requests. Please wait a moment and try again."`
- `"Email is required."`
- `"Failed to save assessment."`
- Email subjects in the table above.
- Band CTA copy, band descriptions, the disclaimer
  (`Disclaimer: Information on this report is for educational purposes only and does not
  constitute formal legal advice. Consult a qualified professional for legal guidance.`)
  and `You received this because you completed the DPDPA Readiness Assessment at
  SaralPrivacy and consented to report delivery.` — all carried into the Jinja2 template.
- `"assessment_form"`, `"assessment"`, `"data_processing"`, `"email_marketing"`,
  `"auto"`, `"quick"`, `"daily"`, `"active"` — stored enum values.

## Out of scope / kept in frontend

- `lib/data/industry-assessment/**` (`core.ts`, `bands.ts`, `index.ts`, 12 packs) —
  **pure TypeScript, zero React/Next imports**, and imported by 12 `app/[locale]/industries/*`
  pages, the 12 industry assessment clients, `app/(backoffice)/admin/assessments/page.tsx`
  and the report page. Scoring runs in the browser and the server never scores anything,
  so nothing here is server behaviour. **Stays in the frontend, unchanged, not backed up.**
- `lib/data/dpdpa-assessment.ts` — same reasoning (scoring + question text for the
  general assessment, used by `SurveyClient.tsx` and the report page).
- `app/(backoffice)/report/[token]/page.tsx` rendering, expiry screen, projection maths,
  `TemplateGateModal` template list — all stay; only the data source changes.
- `components/TemplateGateModal.tsx` and `/api/template-download` — **forms** module.
- `app/api/admin/send-report`, `app/(backoffice)/admin/assessments` — **admin** module.
- `ops.consent_log` / `ops.subscribers` table *ownership* — shared with forms; this
  module writes them through raw SQL so two wave-1 builders never declare the same
  SQLModel table.

## Design decisions (recorded, not invented behaviour)

1. **`answerSummary` moves into the request.** The report email lists the submitter's
   answers in words. Building that server-side would mean copying all 759 lines of
   `lib/data/dpdpa-assessment.ts` question and option text into Python — duplicated legal
   copy with two sources of truth, and the orchestrator's instruction is to keep pure
   scoring data in the frontend. `SurveyClient.tsx` therefore computes the same
   `buildAnswerSummary(...)` it already has and posts it. The backend uses it verbatim
   and tolerates its absence (empty list). The 12 industry clients send nothing, which
   matches today exactly: their answer keys (`q0`…`q10`) never match the general
   question keys (`q1_sector`…), so `buildAnswerSummary` already returned `[]` for them.
2. **Error envelope.** FastAPI `HTTPException` renders `{"detail": "..."}` where the
   route handler rendered `{"error": "..."}`. The user-facing strings and status codes
   are identical and no caller reads the body on failure (all 13 clients branch on
   `res.ok` / `res.status === 429` only).
3. **`source` → `consent_source`.** `lib/subscribers.ts` inserts a field named `source`,
   but `ops.subscribers` has no such column — it has `consent_source` with a CHECK that
   allows exactly `'assessment_form'`. The Python port writes `consent_source`.
4. **Geo headers.** `x-vercel-ip-*` disappear off Vercel and the core proxy does not
   forward them. They are still read when present; `city` keeps its `body.city`
   fallback, so the self-reported city path is unchanged.
