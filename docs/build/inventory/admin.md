# admin inventory

Sources read: `frontend/app/api/admin/{data,bloggers,bloggers/[id],aeo-panel-run,seo-inspect,send-report}/route.ts`,
`frontend/app/api/cron/aeo-panel/route.ts`, `frontend/lib/aeo/*`, `frontend/lib/seo/*`,
`frontend/lib/auth/adminAuth.ts`, `frontend/lib/db/{index,flags,supabase}.ts` (doc shape),
every page under `frontend/app/(backoffice)/admin/**` except login / set-password / blog / briefings / outreach,
`frontend/vercel.json`, `.github/workflows/seo-inspect.yml`, `_backup/supabase/migrations/0001` + `0005_ops_seo_inspections.sql`.

Auth note: every route used `requireRole(req, ["admin"])` → 401 `{"error":"Unauthorized"}` for
no session **and** for a blogger. The backend uses `require_role("admin")`: no/expired
token → 401, blogger → 403 `"Access denied."` (core contract; recorded as a question).

## Routes
| # | Method + path (old → new, all under `/api/v1`) | Auth / rate limit | Request | Side effects | Response | Callers | Test written |
|---|---|---|---|---|---|---|---|
| 1 | GET `/api/admin/data` → GET `/admin/data` | admin | `collection` (allowlist = the 19 `COLLECTIONS` names), `limit` default 200, capped 500 (`parseInt` of junk → NaN → treated as 200), optional `source`, `status` equality filters | read only; order `created_at desc` | 200 `{documents, total}`; docs carry Appwrite-era names (`created_at_attr`→`created_at`, `updated_at_attr`→`updated_at`) plus `id`, `$id`, `$createdAt`, `$updatedAt`; 400 `"Invalid collection"`; 500 `"Failed to fetch data."` | leads, subscribers, downloads, discovery, assessments, survey-responses, consent, consultations pages (+ editorial briefings/blog, outreach pages — other modules) | ✓ test_data.py |
| 2 | GET `/api/admin/bloggers` → GET `/admin/bloggers` | admin | — | read `ops.blogger_accounts` newest first, limit 100 | 200 `{bloggers:[doc]}`; 500 `{error: msg}` | BloggersClient (refresh after invite/resend), bloggers/page.tsx (SSR, was direct lib/db) | ✓ test_bloggers.py |
| 3 | POST `/api/admin/bloggers` → POST `/admin/bloggers` | admin | `{email, name, bio?}`; email trimmed+lowercased, name trimmed | duplicate check (blogger row OR auth user); creates auth user role `blogger` (now `app.users` + `app.invite_tokens`, 24 h); inserts `ops.blogger_accounts` `{email,name,bio,password_hash:"",active:false,invite_token:"pending",token_expires:"",created_at_attr:now}`; sends invite email (best effort) | 200 `{success, id, inviteUrl, emailSent, emailError}`; 400 `"Email and name are required."`; 409 `"A blogger with this email already exists."`; 500 `"Could not create the invite."` | BloggersClient invite + resend | ✓ |
| 4 | PATCH `/api/admin/bloggers/[id]` → PATCH `/admin/bloggers/{id}` | admin | `{active}` (`=== true`) | row `active`, `invite_token:""`; auth user banned/unbanned (now `users.is_active`; deactivation revokes all sessions) — ban failure only logged | 200 `{success:true}`; 500 `{error}` | BloggersClient toggle | ✓ |
| 5 | DELETE `/api/admin/bloggers/[id]` → DELETE `/admin/bloggers/{id}` | admin | id = uuid or legacy id | deletes auth user (now `app.users` + its sessions / invite tokens) then the blogger row; unknown id = no-op success | 200 `{success:true}`; 500 `{error}` | BloggersClient delete + resend (delete then re-invite) | ✓ |
| 6 | POST `/api/admin/send-report` → POST `/admin/send-report` | admin | `{assessmentId}` (uuid or legacy id) [+ new optional `answerSummary`, computed client-side from `QUESTIONS` — see Library row] | loads `app.assessments`; parses `immediate_actions_json`, `red_flags_json`, `answers_json`, `category_scores_json` (bad JSON → empty); sends survey result email FROM_BRIEFINGS; then sets `email_sent_at` (ISO) + `email_sent_by:"admin"` (failure only logged) | 200 `{success:true}`; 400 `"assessmentId is required"`; 404 `"Assessment not found"`; 400 `"Assessment has no email address"`; 500 email error or `"Email failed to send"` | assessments page | ✓ test_send_report.py |
| 7 | POST `/api/admin/aeo-panel-run` → POST `/admin/aeo-panel-run` (+ GET `/admin/tasks/{id}`) | admin; maxDuration 300 | — | 20 OpenRouter calls, 20 `ops.ai_citations` rows | old: 200 `{ok, totalDurationMs, summary, persisted, dbErrors[≤5]}`; 500 `"OPENROUTER_API_KEY not configured in Vercel env vars"`; 500 `{ok:false, error, durationMs}`. New: 202 `{ok:true, task_id, status:"running"}`, the same payload arrives as `result` of the task | citations/RunButton | ✓ test_aeo.py |
| 8 | GET `/api/cron/aeo-panel` → job `aeo-panel` `30 3 * * 1` | CRON_SECRET bearer (500 `"CRON_SECRET not configured"`, 401 `"Unauthorized"`) → worker | — | same as #7 | job result `{ok, summary}`; `OPENROUTER_API_KEY not configured` → failed JobResult | vercel.json | ✓ test_jobs.py |
| 9 | POST `/api/admin/seo-inspect` → POST `/admin/seo-inspect` (+ GET `/admin/tasks/{id}`) | admin; maxDuration 300 | `{scope?: watchlist\|newcomers\|full (else newcomers), submitSitemap?: bool}` | GSC inspect/analytics/sitemaps; persist `ops.seo_runs` + `ops.seo_inspections` unless suspect | old: 500 `"GSC_SERVICE_ACCOUNT_JSON is not configured in the Vercel env"`, 500 `"ops.seo_* tables missing — apply migration 0005 first"`; 200 `{ok, run_id, persisted, verdict, summary, inspected, errors, shortlist, newcomers, durationMs, log}`; 500 `{ok:false, error, durationMs, log}`. New: 202 + task polling, result payload identical | seo/RunButton | ✓ test_seo.py |
| 10 | server action `markRequested` → POST `/admin/seo/index-requests` | admin | form `url`, must match `^https://saralprivacy\.com/` | upsert `ops.seo_index_requests` (url, today, note `"admin: Request Indexing pressed"`), revalidatePath `/admin/seo` | errors `"Unauthorized"`, `"URL must be on https://saralprivacy.com/"` | seo/page.tsx form | ✓ |
| 11 | SSR `dbFromEnv().listRuns(12)/fetchInspections/fetchLedger` → GET `/admin/seo` | admin | — | read | `{state:"empty", ledger}` or `{state:"ready", runs, latest, rows, ledger}` | seo/page.tsx | ✓ |
| 12 | SSR dashboard `queryDocuments` (counts, recent, risk split) → GET `/admin/dashboard` | admin | — | read counts for leads, subscribers, downloads, assessments, survey_responses, briefings; recent 8/8/8/8/8/5; assessments by `risk_level` green/amber/red/total; each part falls back to 0/[] on error | `{counts, recent, risk}` | admin/page.tsx | ✓ |
| 13 | SSR citations `queryDocuments('ai_citations', 500)` → GET `/admin/data?collection=ai_citations&limit=500` | admin | — | read | same doc shape | citations/page.tsx | ✓ |
| 14 | SSR bloggers `queryDocuments('blogger_accounts', 100)` → GET `/admin/bloggers` | admin | — | read | `{bloggers}` | bloggers/page.tsx | ✓ |

## Server-rendered pages reading these tables
| Page | Query it needs | Cache/ISR setting |
|---|---|---|
| `/admin` | #12 | `force-dynamic` |
| `/admin/bloggers` | #14 | `force-dynamic` |
| `/admin/citations` | #13 | `force-dynamic`, `revalidate = 0` |
| `/admin/seo` | #11 | `force-dynamic`, `revalidate = 0` |
| admin layout | session only (sidebar, role filter, Sign Out) | — |

## Jobs / crons
| Name | Schedule (UTC) | What it does | Idempotency |
|---|---|---|---|
| `aeo-panel` | `30 3 * * 1` (vercel.json) | 5 prompts × 4 engines via OpenRouter, detect saralprivacy citation, insert 20 `ops.ai_citations` rows under one `run_id` | new run_id per firing; advisory lock prevents concurrent double runs (same as Vercel: a second firing = a second run) |
| `seo-inspect` | `0 4 * * 1` (.github/workflows/seo-inspect.yml, scope `full`, no sitemap submit) | GSC watcher run, persisted unless suspect | new run per firing; suspect runs never persisted |

## Library logic to rewrite (not routes)
| Source file | Behaviour to keep | Notes |
|---|---|---|
| lib/aeo/prompts.ts | 5 LOCKED prompts verbatim | `services`-free constant |
| lib/aeo/engines.ts | 4 engines + OpenRouter model ids | |
| lib/aeo/openrouter-client.ts | POST `https://openrouter.ai/api/v1/chat/completions`, headers Authorization / HTTP-Referer `https://saralprivacy.com` / X-Title `SaralPrivacy AEO Panel`, `max_tokens 1500`, `temperature 0.2`; 60 s timeout (message `OpenRouter timeout after 60s for model X`), 75 s hard ceiling (`Hard ceiling 75s exceeded for X`); `OpenRouter {status}: {text[:300]}`, `OpenRouter error: …`, `OpenRouter returned no choices`; citation extraction order citations → annotations → search_results, dedup, 1-based positions | httpx; timeouts via httpx timeout + thread future ceiling |
| lib/aeo/citation-detector.ts | own host `(^|\.)saralprivacy\.com$` → Yes + first position; brand text `\bsaral\s*privacy\b` → Mentioned-no-link; competitors = other hosts, deduped, insertion order | |
| lib/aeo/runner.ts | ISO week number, uuid4 run id, 20 calls in parallel, per-row error capture (`cited:No`, `errorMessage`), snippet 500 chars; `summarizeRun` (clean-total cite rate, byEngine) | ThreadPoolExecutor(20) |
| lib/aeo/types.ts | persisted fields: competitors JSON string, raw_citations JSON string cut to 50 000 chars, error_message or null | |
| lib/seo/watchlist.ts | SITE, BASE, 17 watchlist paths, REQUESTED_INDEXING ledger | verbatim |
| lib/seo/verdict.ts | bucketOf, normalizeCrawlTime, pathOf, toRecord, bucketCounts, weeksBetween, decide (all summary/next strings verbatim), checkDataSanity, suspectVerdict, diffRuns, crawledNotIndexedBreakdown, tierOf, shortlist; thresholds 0.5 / 0.7 / 5 / 10 / 5 / 0.5 / 0.9 | `renderSummary` was only for the CLI / GH step summary — ported for the job log |
| lib/seo/gsc.ts | service-account key from `GSC_SERVICE_ACCOUNT_JSON` (validated: `…: not valid JSON`, `…: expected a Google service-account key (type=service_account with client_email + private_key)`), scope `webmasters`, inspect / searchAnalytics (dimensions page, rowLimit 5000, dataState final) / listSitemaps / submitSitemap; retry 429/5xx/network 4× with 500·2^n ms; `GSC HTTP {status}: {body[:300]} — {hint}` hints verbatim | google-auth for the token |
| lib/seo/run.ts | mergeLedger (DB wins), fetchSitemapUrls (3 attempts, nested sitemapindex depth<2, `no <loc> entries`), resolveSitemap fallback log line, target selection with budget 500 (watchlist never cut), concurrency 3, progress log lines, 28-day analytics window lagged 3 days, sitemap resubmit + list, prevDiscovered, suspect override | log lines verbatim |
| lib/seo/db.ts | fetchPrevRun (newest non-dry run + its records), persist (run row + inspections in pages of 500), listRuns(12), fetchInspections (ordered by path), fetchLedger (requested_at desc, url asc, 1000), addIndexRequest (upsert on url) | SQLModel |
| lib/auth/adminAuth.ts | createInvite / findAuthUserIdByEmail / setAuthUserBanned / deleteAuthUserByEmail | replaced by `crud.auth` + local user delete |
| send-report QUESTIONS lookup | answerSummary: for each question with an answer, option text(s) joined `", "` | computed in the browser from `lib/data/dpdpa-assessment.ts` (pure data, stays in frontend) and sent as `answerSummary`; backend never re-derives it |

## Strings to copy verbatim
- `"Invalid collection"`, `"Failed to fetch data."`, `"Email and name are required."`,
  `"A blogger with this email already exists."`, `"Could not create the invite."`,
  `"assessmentId is required"`, `"Assessment not found"`, `"Assessment has no email address"`,
  `"Email failed to send"`, `"OPENROUTER_API_KEY not configured in Vercel env vars"` (admin route),
  `"OPENROUTER_API_KEY not configured"` (cron), `"GSC_SERVICE_ACCOUNT_JSON is not configured in the Vercel env"`,
  `"URL must be on https://saralprivacy.com/"`, `"admin: Request Indexing pressed"`.
- BAND_DESCRIPTIONS (5 bands) in send-report.
- Every verdict summary/next string, suspect text and log line in lib/seo.

## Out of scope / kept in frontend
- `tools/seo/inspect.ts` CLI (`--dry-run`, `--offline`, `--budget`, `--no-db`, `--out`) and its JSON report
  files: replaced by `python -m app.jobs run seo-inspect`; the CLI + workflow are outside this
  boundary (orchestrator moves them).
- `tools/auth/provision.ts`: replaced by `/auth/invite` + `/auth/recover` (auth module).
- `lib/data/dpdpa-assessment.ts`, `lib/data/industry-assessment.ts`, `lib/discovery/data.ts`: pure client data, untouched.
- `admin/outreach`, `admin/blog`, `admin/briefings`, `admin/login`, `admin/set-password`: other modules.
