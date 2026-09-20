# editorial inventory

Sources read once: `frontend/app/api/briefings/{approve,delete,generate,send,today}/route.ts`,
`frontend/app/api/blog/{[id],infographic,revise,save,validate}/route.ts`,
`frontend/app/api/revalidate/route.ts`, `frontend/lib/content-freshness.ts`, the helpers they
import (`lib/db/{index,supabase,storage}.ts`, `lib/email.ts` briefing functions,
`lib/email-templates.ts` `briefingApprovalTemplate` / `briefingEmailTemplate`,
`lib/sendGateway.ts`), the root pipeline `run_pipeline.sh`, `tools/*.py`,
`.github/workflows/{daily-briefing,backfill-taxonomy}.yml`, and every server component that
reads `briefings` / `blog_posts`.

Shared facts:

- Tables: `briefings` → `app.briefings_meta`, `blog_posts` → `ops.blog_posts`,
  `ai_citations` → `ops.ai_citations` (written by the admin module's AEO job; modelled here).
- `lib/db` renames the app field `created_at` → column `created_at_attr` for `briefings` only.
  Row → document mapping (`rowToDoc`): every column, `created_at` = `created_at_attr` (may be
  null), plus `id`, `$id` (= id), `$createdAt` (= row `created_at`), `$updatedAt`.
- Ids: UUID, else looked up by `legacy_id` (Appwrite `$id`s in old emails and admin URLs).
- Cron secret: `CRON_SECRET || BRIEFING_CRON_SECRET` (trimmed), read from
  `Authorization: Bearer <s>` (generate also accepts `x-cron-secret`).
- Model ids: auto-generate `claude-opus-4-5` (max_tokens 2048); blog validate/revise
  `claude-sonnet-4-6`; pipeline content `CLAUDE_MODEL` default `claude-sonnet-4-6`,
  `CLAUDE_MAX_TOKENS` 4096, `CLAUDE_TEMPERATURE` 0.3.
- `maxDuration`: approve 300 s (serial send, 100 ms per recipient), infographic 30 s.

## Routes

| # | Method + path | Auth / rate limit | Request | Side effects | Response | Callers | Test written |
|---|---|---|---|---|---|---|---|
| 1 | GET `/api/briefings/generate` → `/api/v1/briefings/generate` | none | — | none | 410 `{error:"Vercel cron disabled. Use n8n pipeline → POST /api/briefings/generate."}` | `admin/briefings/page.tsx:36` ("Generate" button) | yes |
| 2 | POST `/api/briefings/generate` → `/api/v1/briefings/generate` | cron secret (header `x-cron-secret` or Bearer); empty expected ⇒ 401 | JSON (bad JSON ⇒ `{}`). **With `title`** (pipeline payload): `date?, excerpt?, summary?, why_it_matters?, business_impact?, who_is_affected?[], action_checklist?[], category?, tags?[], industries?[], read_time?, infographic_url?, infographic_base64?`. **Without `title`**: auto-generate; query `?forDate=YYYY-MM-DD` | *manual*: slug = `<date or UTC today>-<title lower, [^a-z0-9]+→"-", trim "-", first 60>`; delete existing `draft` rows with that slug (non-blocking); insert briefing: `why_it_matters` = JSON `{why, impact, affected}` when `business_impact`/`who_is_affected` present else raw text; `action_checklist`/`tags`/`industries` JSON strings (defaults `[]`, `[]`, `["general"]`); `category` default `compliance-guidance`; `read_time` = body value or max(1, round(words/200)) over title, excerpt, summary, why_it_matters, business_impact, who_is_affected, action_checklist; `featured=false`, `author="DPDPA Editorial Team"`, `status="approved"`, `approval_token` uuid, `scheduled_for` tomorrow 03:30 UTC, `created_at` = `<date>T03:30:00.000Z` when `date` given else now, `infographic_base64` = `infographic_url || infographic_base64 || ""`. *auto*: topic = `DPDPA_TOPICS[dayOfYear % 30]`, theme = `DOW_THEMES[weekday, 0=Sun]`, Claude JSON → v2 envelope in `why_it_matters` (`version:2`, tag, hook_line1/2, card_*, explainer_*, action_format, action_items, save_line, participation, theme_type, theme_label); `summary` = save_line; `read_time` = max(1, ceil(words/200)); insert; optional GitHub push `briefings/<date>-<slug>.json` (GITHUB_TOKEN/OWNER/REPO); approval email to the 3 admins + EXTRA_ADMIN_EMAILS | manual 200 `{success:true, briefingId, slug}`; auto 200 `{success:true, briefingId, slug, topic}`; 401 `{error:"Unauthorized."}`; 500 `{error:"Failed to generate briefing."}` | pipeline `publish_to_webapp.py` (now in-process); n8n | yes |
| 3 | GET `/api/briefings/approve?token&briefingId` → `/api/v1/briefings/approve` | the briefing's `approval_token` | query `token`, `briefingId` | audience = eligible subscribers (suppressed statuses `unsubscribed/bounced/complained` dropped, deduped by lower-cased email, paginated); send `briefingEmailTemplate` to each (from briefings sender, `List-Unsubscribe` header, signed unsubscribe URL, 100 ms apart); update briefing `status="sent"`, `sent_at=now`, `subscriber_count=sent` | redirects: missing params → `/admin?briefing=error&reason=missing-params`; bad token → `/admin?briefing=error&reason=invalid-token`; already sent → `/admin?briefing=already-sent`; success → `<site>/admin?briefing=published&sent=<n>`; not found / any error → `<site>/admin?briefing=error&reason=server-error` | approval email link `<site>/api/briefings/approve?...` (old inboxes) | yes |
| 4 | POST `/api/briefings/send` → `/api/v1/briefings/send` | admin role only | JSON `briefingId` | status must be `approved`; send to subscribers (old code: first 1000 rows, no suppression filter — see open question; now eligible subscribers); update `sent`, `sent_at`, `subscriber_count` | 200 `{success:true, sent, failed, total}`; 400 `{error:"briefingId is required."}`; 404 `{error:"Briefing not found"}`; 400 `{error:"Briefing status is \"<s>\". Only approved briefings can be sent."}`; 401 `{error:"Unauthorized."}` (now deps 401/403); 500 `{error:"Failed to send briefing."}` | `admin/briefings/page.tsx:58` | yes |
| 5 | DELETE `/api/briefings/delete?id=` → `/api/v1/briefings/delete` | Bearer `BRIEFING_CRON_SECRET` | query `id` | delete row (no-op if missing; legacy id accepted) | 200 `{success:true, deleted:id}`; 401 `{error:"Unauthorized"}`; 400 `{error:"Missing ?id= parameter"}`; 500 `{error:<msg>}` | pipeline (dup cleanup), none in-repo | yes |
| 6 | GET `/api/briefings/today` → `/api/v1/briefings/today` | cron secret (Bearer) | — | read latest `approved|sent` briefing whose `created_at` falls in today (IST) | 200 `{briefingId, title, slug, url, date, hook_line1, hook_line2, card_what, card_why, card_action, card_owner, explainer_concept, explainer_example, explainer_mistake, action_items[], save_line, excerpt, read_time, category, author, status, created_at}` with v2→v1 fallbacks (`hook_line1 ← why_heading ← why`, `card_why ← impact`, `save_line ← save ← summary`, `read_time` default 3, `author` default `DPDPA Editorial Team`); 404 `{error:"No briefing found for today.", date, hint:"Pipeline may not have run yet, or no topic was planned for today."}`; 401 `{error:"Unauthorized."}`; 500 `{error:"Failed to fetch today's briefing."}` | n8n email workflow (external) | yes |
| 7 | GET `/api/blog/[id]` → `/api/v1/blog/{id}` | none before (now admin/blogger) | path id (uuid or legacy) | read | 200 document; 404 `{error:"Not found"}` | `admin/blog/[id]/edit/page.tsx:29` | yes |
| 8 | POST `/api/blog/save` → `/api/v1/blog/save` | admin or blogger | `SavePayload` | build document (below); insert `ops.blog_posts`; revalidate path `/blog/<slug>`, `/blog`, tag `blog-posts` (failures swallowed) | 200 `{success:true, id, slug}`; 400 `{error:"Title and slug are required"}`; 400 `{error:"Slug cannot be only slashes or whitespace"}`; 401 `{error:"Unauthorized"}`; 500 `{error:<msg>}` | `components/admin/BlogEditor.tsx:465` | yes |
| 9 | PATCH `/api/blog/save` → `/api/v1/blog/save` | admin or blogger | `SavePayload` with `id` | build document; update row; same revalidation | 200 `{success:true, id, slug}`; 400 `{error:"Document ID required for update"}`; 400 slug message as #8; 500 `{error:<msg>}` | `BlogEditor.tsx:465` | yes |
| 10 | POST `/api/blog/validate` → `/api/v1/blog/validate` | admin or blogger | `title, lane, section_*` (title + section_what_changed required) | Claude (`claude-sonnet-4-6`) with the DPDPA guardrail system prompt, structured output `{scores{5+total}, section_feedback[], suggested_sources[], editorial_notes}` | 200 output + `validated_at` (IST `YYYY-MM-DD`, server-side); 400 `{error:"Title and at least one content section required"}`; 502 `{error:"Validation model did not return a structured response. Please try again."}`; 500 `{error:<msg>}` | `BlogEditor.tsx:302` | yes |
| 11 | POST `/api/blog/revise` → `/api/v1/blog/revise` | admin or blogger | `sectionKey, currentContent, feedbackNote` required; `title`, other `section_*` for context | Claude (`claude-sonnet-4-6`) corrector prompt with other sections as context | 200 `{revisedContent}` (trimmed); 400 `{error:"sectionKey, currentContent, and feedbackNote are required"}`; 502 `{error:"Revision model returned empty content. Please try again."}`; 500 `{error:<msg>}` | `BlogEditor.tsx:350` | yes |
| 12 | POST `/api/blog/infographic` → `/api/v1/blog/infographic` | admin or blogger | `id, title` required; `lane, excerpt, section_what_changed, section_law_says, section_do_now` | brand SVG (layout by lane: law-explained→stat, compliance-playbook→process, myth-fact→comparison, sector-notes→checklist, governance-watch→timeline, default stat) rasterised 1200×630 PNG with Inter; upload `blog_inf_<id>.png` (deterministic, replace) → URL + `?v=<ms>`; update `infographic_url`; revalidate `/blog/<slug>`, `/blog`, tag `blog-posts` (non-fatal) | 200 `{success:true, url}`; 400 `{error:"id and title are required"}`; 500 `{error:<msg>}` | `BlogEditor.tsx:417` | yes |
| 13 | GET `/api/revalidate` (stays in frontend) | `x-revalidate-secret` header or `?secret=`; expected `CRON_SECRET || BRIEFING_CRON_SECRET` | — | `revalidateTag("briefings")` | 200 `{revalidated:true, tag:"briefings", at}`; 401 `{error:"Unauthorized."}` | backend `services/revalidate.py` (POST — added), legacy pipeline | yes |
| 14 (new) | GET `/api/v1/briefings` | public | `limit` (≤100), `offset` | read `approved|sent`, newest `$createdAt` first; `approval_token` never returned | `{docs:[doc], total}` | briefings-archive, briefings-source, briefings/[slug] related pool | yes |
| 15 (new) | GET `/api/v1/briefings/by-slug/{slug}` | public | slug | read by exact slug, any status (as before) | doc / 404 `{error:"Not found"}` | briefings/[slug] | yes |
| 16 (new) | GET `/api/v1/briefings/admin/all` | admin | `limit` | read all statuses | `{documents:[doc], total}` | admin/briefings (was `/api/admin/data?collection=briefings`) | yes |
| 17 (new) | GET `/api/v1/blog` | public | `lane?`, `exclude_slug?`, `order=created|updated`, `limit` (≤100), `offset` | read `published` | `{docs, total}` | blog list, blog/[slug] related, sitemap | yes |
| 18 (new) | GET `/api/v1/blog/by-slug/{slug}` | public | slug | read `published` with exact slug | doc / 404 | blog/[slug] | yes |
| 19 (new) | GET `/api/v1/blog/admin/all` | admin or blogger | `limit` | read all statuses, newest first | `{documents, total}` | admin/blog (was `/api/admin/data?collection=blog_posts`) | yes |

### Blog `buildDocument` (rows 8/9)
`title[:200]`, `slug` = trim + strip leading/trailing `/` `[:200]`, `excerpt[:600]`, `lane[:60]`,
`author[:100]`, `tags[:500]` or null, `featured`, `status`, `section_what_changed` /
`section_law_says` normalised (empty or "blank" → null) `[:10000]`, `sections_json` = JSON
`{section_do_now, section_uncertain, section_mistakes, primary_sources: JSON string}` (sections
normalised), `validated_at[:30]` or null, the five scores, `validation_score` = sum,
`read_time` = max(1, round(words/200)) over the normalised sections, `published_at` =
payload value or UTC today (`YYYY-MM-DD`) — only when `status == "published"` (otherwise the
column is left untouched).

## Server-rendered pages reading these tables

| Page | Query it needs | Cache/ISR setting |
|---|---|---|
| `lib/data/briefings-archive.ts` (`/briefings`, `/briefings/all`, home BriefingsSection) | all `approved|sent`, paginated 100 | `unstable_cache` 3600 s, tag `briefings` |
| `lib/data/briefings-source.ts` (sitemap, TrustStrip) | same | 3600 s, tag `briefings` |
| `app/[locale]/briefings/[slug]/page.tsx` | by slug (any status); related pool = all `approved|sent` | page `revalidate = 1800`; `unstable_cache` 1800 s, tag `briefings` |
| `app/[locale]/blog/page.tsx` | `published`, optional `lane`, newest, limit 50 | `unstable_cache` 600 s per lane, tag `blog-posts` |
| `app/[locale]/blog/[slug]/page.tsx` | `published` by slug; 3 related `published` same lane, slug ≠ | page `revalidate = 3600` |
| `app/sitemap.ts` `getBlogSlugs` | all `published`, order `$updatedAt` desc, paginated | sitemap default |
| `admin/blog/page.tsx`, `admin/briefings/page.tsx` (client) | all rows, limit 100 | none |
| `admin/blog/[id]/edit/page.tsx` | one post by id | `no-store` |

Other modules read `briefings` too (outreach: `cron/outreach-send`, `cron/briefing-send`;
admin: dashboard count + recent, `ai_citations` page; chat: `briefings_live`). They import
`app.models.editorial` / `app.crud.editorial`.

## Jobs / crons

| Name | Schedule (UTC) | What it does | Idempotency |
|---|---|---|---|
| `editorial_daily_briefing` | `30 3 * * *` (09:00 IST) | roadmap row for today (Google Sheet, CSV fallback; `Plan Published Date` = date and `Published` ≠ Yes) → skip cleanly if none → SerpAPI research (2 queries, 2 s apart, dedupe, keyword relevance) → Claude content (Pydantic-validated, 3 attempts, 5 s apart) → KIE.ai Nano Banana image (createTask + poll 5 s / 120 s, 3 attempts, watermark) → upload `infographics/inf<YYYYMMDD>.jpg` → insert briefing (manual path of row 2) → revalidate `briefings` → pre-warm `/briefings/<slug>` → mark Sheet row `sent=done, Published=Yes, Actual Publish Date=<d-Mon-yy>` (CSV fallback) | Sheet flag (a published row is never picked again) + DB guard: an `approved|sent` briefing whose slug starts with `<date>-` ⇒ skip |
| manual CLI `python -m app.jobs.editorial pipeline --date YYYY-MM-DD` | on demand | backfill a missed day (was `workflow_dispatch` input / `./run_pipeline.sh <date>`) | as above |
| manual CLI `python -m app.jobs.editorial backfill-taxonomy [--apply] [--revert FILE]` | on demand | reclassify archive facets (category ← stage, industries ← [sector], tags ← [format, …]) by roadmap slug; dry-run default; `--apply` writes a JSON backup first | deterministic, re-run is a no-op |

## Library logic to rewrite (not routes)

| Source file | Behaviour to keep | Notes |
|---|---|---|
| `tools/briefing_taxonomy.py` | stage/sector/format derivation tables | copied verbatim into `app/editorial/taxonomy.py` |
| `tools/read_roadmap.py` | date parsing `24-Mar-26`, row selection, result shape, mark-published | gspread via service-account JSON |
| `tools/research.py` | SerpAPI params (`gl=in, hl=en, num=6, safe=active`), fallback knowledge-only | httpx instead of requests |
| `tools/generate_content.py` | system prompt + user prompt verbatim, schema, word count | via `services.llm.complete` |
| `tools/generate_infographic.py` | KIE prompt verbatim, async task API, watermark | SVG fallback was written to `.tmp` but never uploaded ⇒ dropped |
| `tools/publish_to_webapp.py` | payload build (v1 envelope with facets), revalidate, pre-warm, mark roadmap | in-process, no HTTP self-call |
| `tools/backfill_briefing_taxonomy.py` | plan/apply/revert | Postgres instead of Appwrite REST |
| `lib/email.ts` briefing functions + `lib/sendGateway.ts` | approval email to admins, subscriber broadcast, eligible audience, HMAC unsubscribe URL | templates in `app/email-templates/editorial/` |
| `lib/content-freshness.ts` | pure constants for sitemap/Byline | **kept in frontend** (no server logic) |

## Strings to copy verbatim
All error strings in the Routes table; approval email subject `New Briefing LIVE: <title> — Send to Subscribers`;
subscriber email subject `DPDPA Daily Brief: <title>`; all Claude prompts (auto-generate,
validate, revise, pipeline content, KIE image prompt).

## Out of scope / kept in frontend
- `frontend/app/api/revalidate/route.ts` stays (GET kept, POST `{tag}|{path}` added for the backend).
- `frontend/lib/content-freshness.ts` stays (pure constants).
- Standalone manual tools not in the daily pipeline (`tools/send_email.py`, `send_confirmation.py`,
  `send_user_guide.py`, `html_builder.py`, `log_entry.py`, `utils.py`) stay at the repo root —
  Gmail/Notion/Resend CLIs unrelated to the scheduled pipeline (open question).
