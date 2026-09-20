# chat inventory

Source read: `frontend/app/api/chat/{route.ts,feedback/route.ts,handoff/route.ts,health/route.ts}`
and `frontend/lib/chat/*` (orchestrate, system-prompt, journeys, strings, briefings-live,
guard, protocol, pinecone, retrieve, redact, triggers, knowledge-tools, site-routing,
handoff, index-build, theme).
Widget call sites: `frontend/components/chat/useSetuChat.ts`.
No cron in `frontend/vercel.json` touches chat.

Backend paths below are all under `/api/v1`. The browser reaches them through
`frontend/app/api/proxy/[...path]` — e.g. `POST /api/proxy/api/v1/chat`.

## Routes

| # | Method + path | Auth / rate limit | Request (fields, validation, limits) | Side effects | Response (shape, status codes, exact error strings) | Callers | Test written |
|---|---|---|---|---|---|---|---|
| 1 | `POST /api/chat` → `POST /api/v1/chat` | public. Three fixed windows, checked in this order after the body is parsed: `chat-burst:<sessionId>` 5/10 s, `chat-hour:<sessionId>` 30/3600 s, `chat-ip:<ip>` 60/3600 s. `maxDuration = 60`. | JSON body. `sessionId` string, sliced to 64, required. `message` string, trimmed, required, `> 2000` chars rejected. `pageUrl` string sliced to 200, optional. `history` array, last 8 entries kept, each `{role:"user"\|"assistant", content:string, sig?:string}`; content sliced to 2000 then `sanitizeUntrusted`; assistant turns dropped unless `verifyTurn(content.trim(), sig)` passes, and dropped wholesale when `CHAT_HISTORY_SECRET` is unset. `state` object → `sanitizeState`. | Pinecone REST search (`/records/namespaces/content/search`, rerank `bge-reranker-v2-m3`, 4 s timeout, null on any failure); local BM25 index `public/chat-index.json`; on freshness intent, a read of the `briefings` collection (`app.briefings_meta`, newest 25); Anthropic `claude-sonnet-5`, `maxOutputTokens: 600`, **no temperature**. No DB write. | **Two-phase byte stream**, `Content-Type: text/plain; charset=utf-8`, status 200: phase A = answer text, then `META_SENTINEL` (`U+001E`), then one `ChatMeta` JSON object. Error JSON bodies: 400 `{"error":"Invalid JSON body."}`; 400 `{"error":"sessionId and message are required."}`; 413 `{"error":"Message too long (max 2000 characters.)"}` — exact string is `Message too long (max 2000 characters).`; 429 `{"error":"You've asked a lot — give me a minute and try again."}` + `Retry-After`; 500 `{"error":"Something went wrong on my side — please that again."}` — exact string is `Something went wrong on my side — please try that again.`. In-stream failures do **not** change the status: the guarded/limited/error text is streamed with a meta block. | `frontend/components/chat/useSetuChat.ts:113` | ✅ `app/tests/chat/test_routes_chat.py` |
| 1a | — injection guard | runs **before** retrieval and before the model, on the raw message | `detectInjection(message)` — 12 rules (below) | logs `[chat-guard] blocked turn (<rule>)` | 200 stream: text = `I stay on DPDPA questions answered from SaralPrivacy's own guides — that's the only way I can be sure of what I tell you.` + `guardedMeta()` (no citations, FAQ + Contact actions, `confidence:"low"`, `refusal:true`, followups `["What is DPDPA?","Does DPDPA apply to me?"]`, `animation.state:"unsure"`). Signed. | — | ✅ `test_routes_chat.py::test_chat_blocks_injection_without_calling_model` |
| 1b | — retrieval floor | after `planTurnAsync` | `plan.refuse` true ⇒ model is never called | none | 200 stream: text = `I can only help with what's on SaralPrivacy — I don't have that in our guides yet. Try the FAQ, the Learning Hub, or ask our team directly.` + the plan's meta. Signed. | — | ✅ `test_routes_chat.py::test_chat_refuses_below_floor_without_model` |
| 1c | — output leak scan | during streaming | release lags the model by `LEAK_HOLDBACK` (= longest leak signature) characters | logs `[chat-guard] output leak scan tripped — answer withheld` | text released so far + ` ` + the guarded line, then `guardedMeta()` signed over the released answer | — | ✅ `test_routes_chat.py::test_chat_withholds_answer_when_leak_detected` |
| 1d | — mid-stream model error | during streaming | — | none | appends `"\n" + "Something went wrong on my side — please try that again."` then the sentinel + meta with `confidence:"low"`, `animation.state:"unsure"` | — | ✅ `test_routes_chat.py::test_chat_streams_error_tail_when_model_raises` |
| 2 | `POST /api/chat/feedback` → `POST /api/v1/chat/feedback` | public. `chat-fb:<ip>` 20/60 s. | JSON body. `sessionId` sliced 64, required. `turnId` sliced 64, required. `helpful` boolean or null. `reason` string sliced 500 or null. `pageUrl` string sliced 200 or null. `failureKind` ∈ `{refusal, low_confidence, thumbs_down}` else null. `question` string sliced 2000, **stored only when `failureKind` is set, always through `redactText`** (D2). | insert into `chat_feedback` (`ops.chat_feedback`) with `ts = now().toISOString()`. Failures are swallowed. | 200 `{"stored": true}`; 200 `{"stored": false}` when the insert throws (logs `chat_feedback store failed: <msg>`); 400 `{"error":"Invalid JSON body."}`; 400 `{"error":"sessionId and turnId are required."}`; 429 `{"error":"Too many requests."}` | `useSetuChat.ts:192` | ✅ `app/tests/chat/test_routes_feedback.py` |
| 3 | `POST /api/chat/handoff` → `POST /api/v1/chat/handoff` | public. Two ceilings: `chat-handoff-req:<ip>` 15/600 s checked before validation; `chat-handoff-submit:<ip>` 3/600 s consumed only *after* validation passes. | JSON body. Honeypot `hp_url` — any non-blank string ⇒ pretend success. `name` trimmed sliced 100, required. `email` trimmed sliced 200, must match `^[^\s@]+@[^\s@]+\.[^\s@]{2,}$`. `consent` must be literal `true`. `sessionId` sliced 64, required. `pageUrl` sliced 200. `reason` ∈ `{explicit_ask, journey_stalled, repeat_refusal, negative_feedback}` else `explicit_ask`. `lastUserMessage` sliced 2000. `state` → `sanitizeState`. | insert into `leads` (`ops.leads`, `source:"setu_handoff"`, `consent_version: PRIVACY_NOTICE_VERSION` = `1.0.0`, `created_at` → column `created_at_attr`, geo from `x-vercel-ip-city/country/country-region`); then **non-blocking** insert into `consent_log` (`ops.consent_log`, `consent_type:"data_processing"`, `consent_value:true`, `privacy_version:"1.0.0"`); then **non-blocking** `sendConsultationAlert(leadData)` — admin email, subject `New Consultation Request — <name> from <company>` (company is `""` for a handoff). | 200 `{"ok": true}` (also for a tripped honeypot); 400 `{"error":"Invalid JSON body."}`; 400 `{"error":"Consent is required."}`; 400 `{"error":"Please add your name."}`; 400 `{"error":"Please check the email address."}`; 400 `{"error":"sessionId is required."}`; 429 `{"error":"Too many requests. Please wait a moment and try again."}` + `Retry-After`; 500 `{"error":"We couldn't send that just now. Please use the contact page."}` when the lead insert fails. | `useSetuChat.ts:218` | ✅ `app/tests/chat/test_routes_handoff.py` |
| 4 | `GET /api/chat/health` → `GET /api/v1/chat/health` | public, no limit | none | loads the index; `pineconeStats()` (POST `describe_index_stats`) when a key is configured | 200 `{"ok":true,"chunks":<n>,"pinecone":{"configured":bool,"index":"saralprivacy-setu","records":<n>\|null,"reachable":bool}}`; 500 `{"ok":false,"error":"<message>"}` | ops/monitoring only (no in-app caller) | ✅ `app/tests/chat/test_routes_health.py` |

### `ChatMeta` (phase B object — server-built, the model can never author it)
```
citations:        [{title, url, tier}]   max 3, deduped by url, every url passes isValidCitation
actions:          [{type:"open_url", label, url}] max 3
confidence:       "high" | "low"
refusal:          bool
piiWarning:       bool
suggestedFollowups: string[]
journey?:         "J1".."J6"
industry?:        <industry slug>
animation:        {state: "pointing"|"unsure"|"speaking"}
disclaimer:       "Educational only — not legal advice."
escalation?:      {reason: "explicit_ask"|"journey_stalled"|"repeat_refusal"|"negative_feedback"}
sig?:             HMAC-SHA256(CHAT_HISTORY_SECRET, answer.trim()) hex, first 32 chars ("" when unset)
```

## Server-rendered pages reading these tables
None. The widget is fully client-side; no page reads `chat_feedback`.

## Jobs / crons
None. (`scripts/build-chat-index.mts` and `scripts/pinecone-ingest.mts` stay build-time
tools in the frontend and are run by hand; they are not crons.)

## Library logic to rewrite (not routes)

| Source file | Behaviour to keep | Notes |
|---|---|---|
| `lib/chat/protocol.ts` | `META_SENTINEL = ""`, `parseChatResponse` | **Stays in the frontend** (widget). Python gets its own `META_SENTINEL` constant. `ChatMeta` type moves into this file so the widget stops importing `orchestrate.ts`. |
| `lib/chat/guard.ts` | `stripInvisible` (U+00AD, U+200B–U+200F, U+202A–U+202E, U+2060–U+2064, U+206A–U+206F, U+FEFF, U+E0000–U+E007F), `neutralizeTags` (escape `<`/`>` in any tag-shaped token), `sanitizeUntrusted`, `sanitizeInline` (+ newline strip, slice 200), `detectInjection` (12 rules), `newNonce` (8 random bytes hex), `wrapUserMessage`, `scanOutput` (10 leak signatures), `LEAK_HOLDBACK`, `historySigningAvailable`, `signTurn`, `verifyTurn` (constant-time, 32-char sig) | → `app/services/chat/guard.py`. `CONTROL_TAGS` list unchanged. |
| `lib/chat/redact.ts` | `redact` / `redactText`, four patterns in order: `[email]`, `[aadhaar]`, `[pan]`, `[phone]` | → `app/services/chat/redact.py`. Order matters (Aadhaar before phone). |
| `lib/chat/journeys.ts` | `JOURNEYS` J1–J6 (names, entryKeywords, slots, completionUrl), `detectJourney`, `journeyById`, `ChatSessionState`, `createInitialState`, `sanitizeState` (clamps: facts ≤ 20 keys × 40/200 chars, pagesShown ≤ 30, messageCount ≤ 500, counters 0–10) | **Split**: `createInitialState`/`journeyById`/`JourneyId`/`ChatSessionState` stay in the frontend (widget + memory panel); `detectJourney` + `sanitizeState` move to `app/services/chat/journeys.py`. Frontend file keeps both halves so the widget is unchanged. |
| `lib/chat/site-routing.ts` | `ROUTES` (51), `EXCLUDE_FROM_AUTHORITY`, `NEVER_SURFACE_PREFIXES`, `normalizePath`, `isNeverSurfaced`, `isValidCitation`, `isAuthorityCitation`, `routesForTopic`, `routeForIndustry`, `toolForIntent`, `INDUSTRY_SLUGS`, chips | **Stays in the frontend** (`chipsForPage` is imported by `ChatPanel.tsx`, and `site-routing.test.ts` is the dead-link gate against `app/`). The route/allow-list data is **exported** to `backend/app/data/site-routes.json` by the new `frontend/scripts/export-chat-kb.mjs`; `app/services/chat/site_routing.py` reads that file, so there is still exactly one authoring source. |
| `lib/chat/knowledge-tools.ts` | `lookupGlossary` (exact term/id, else token-overlap score > 0.34), `lookupChecklist` (exact `n.n` id, else token overlap + 0.5 × section overlap) | → `app/services/chat/knowledge.py`, reading `backend/app/data/{glossary,checklist}.json` (exported from the same TS data). |
| `lib/chat/retrieve.ts` | `stem`, `tokenize` (stopword list verbatim), BM25 (`K1=1.5`, `B=0.75`, title+tags ×3 / section ×2 / body ×1), `CONFIDENCE_MIN_RATIO=0.4`, `CONFIDENCE_MIN_SCORE=3.0`, `loadIndex` (cached), `platformTour`, `boostFactor`, `fuseRetrieval` (RRF k=60, vector 1.0 / lexical 0.75), `retrieve` (optional dense merge 0.55/0.45) | → `app/services/retrieval.py`, reading `backend/app/data/chat-index.json`. |
| `lib/chat/pinecone.ts` | `pineconeSearch` (integrated index, `top_k 24` → rerank `top_n 6`, industry `$or` filter, 4 s timeout, confidence floor `score >= 0.12`, null on any failure), `pineconeStats`, `INDEX_NAME/INDEX_HOST/NAMESPACE`, API version `2025-04` | Search + stats → `app/services/retrieval.py` with **httpx, no SDK**. The ingest half (`chunkToRecord`, `pineconeUpsert`, `UPSERT_BATCH`) stays in the frontend for `scripts/pinecone-ingest.mts`; `pineconeSearch` is removed from the live file (partial supersede, original copied to `_backup/`). |
| `lib/chat/orchestrate.ts` | `INDUSTRY_KEYWORDS` + `mentionsWord` whole-word match, `detectIndustry`, `NAV_INTENT_RE`, `ESCALATE_RE`, `routerRescue`, `planTurn`, `detectEscalation` (offer-discipline gate first), `planTurnAsync` (Pinecone → fuse → lexical fallback), `buildGroundingBlock`, `buildCitations`, `buildActions`, `buildFollowups`, `FOLLOWUP_BY_JOURNEY`, `buildMeta`, `DISCLAIMER` | → `app/services/chat/orchestrate.py`. `ChatMeta` shape unchanged. |
| `lib/chat/system-prompt.ts` | `buildSystemPrompt()` — the whole prompt verbatim, `REGULATORY_CONTEXT` verbatim, `buildTurnNotes` | → `app/services/chat/system_prompt.py`. **Legal text copied character for character.** |
| `lib/chat/strings.ts` | `t(locale,key)`, en + hi tables | **Stays in the frontend** (every widget component imports `t`). The seven strings the server emits are duplicated verbatim in `app/services/chat/strings.py` with a pointer comment. |
| `lib/chat/briefings-live.ts` | `FRESH_INTENT_RE`, `fetchLiveBriefings` (newest 25, lexical overlap, top 3, null on any failure), `briefingsContextBlock` | → `app/services/chat/briefings_live.py` reading `app.briefings_meta` by raw SQL (the table belongs to the editorial module). Field precedence kept exactly: title ← `title`,`headline`; summary ← `summary`,`excerpt`,`whyItMatters`,`business_impact`,`businessImpact`,`content` (sliced 600); date ← `date`,`published_at`,`publishedAt`, else `created_at[:10]`. |
| `lib/chat/handoff.ts` | `escapeHtml`, `SITE_PATH_RE`, `SITE_SLUG_RE`, `ESCALATION_REASONS`, `isEscalationReason`, `summarise` (redact **then** escape), `buildHandoffPacket`, `validateHandoffInput` | → `app/services/chat/handoff.py`. |
| `lib/chat/triggers.ts` | proactive nudge policy, sector openers, `chipsForPage` companions | **Stays in the frontend** — pure client policy, imported by `SetuChat.tsx`. Untouched. |
| `lib/chat/theme.ts` | Setu colour roles | **Stays in the frontend** — imported by 5 components. Untouched. |
| `lib/chat/index-build.ts` | corpus extraction from typed TS modules | **Stays in the frontend** — build-time only, imports `lib/data/*` and `components/glossary`. Produces `public/chat-index.json`, which the backend consumes. |
| `lib/abuseGuard.ts` (`getClientIp`, `rateLimit`, `isHoneypotTripped`, `HONEYPOT_FIELD`) | IP rule and honeypot | Backend uses `deps.client_ip` + `core.ratelimit.hit`; honeypot field name `hp_url` copied. File stays in the frontend (other modules still import it). |
| `lib/email.ts` `sendConsultationAlert` + `lib/email-templates.ts` `consultationAlertTemplate` | admin alert, subject `New Consultation Request — <name> from <company>` | Chat calls `app.services.email.send` with the same subject and the same labelled rows. **Shared with forms/outreach** — see status file. |

## Strings to copy verbatim

Streamed / returned by the server:
- refusal: `I can only help with what's on SaralPrivacy — I don't have that in our guides yet.`
- refusalHint: `Try the FAQ, the Learning Hub, or ask our team directly.`
- guarded: `I stay on DPDPA questions answered from SaralPrivacy's own guides — that's the only way I can be sure of what I tell you.`
- rateLimited: `You've asked a lot — give me a minute and try again.`
- apiError: `Something went wrong on my side — please try that again.`
- disclaimer: `Educational only — not legal advice.`

HTTP error bodies:
- `Invalid JSON body.`
- `sessionId and message are required.`
- `Message too long (max 2000 characters).`
- `sessionId and turnId are required.`
- `Too many requests.`
- `Too many requests. Please wait a moment and try again.`
- `Consent is required.`
- `Please add your name.`
- `Please check the email address.`
- `sessionId is required.`
- `We couldn't send that just now. Please use the contact page.`

Action / citation labels built server-side:
- `Open the FAQ`, `Browse the Learning Hub`, `Contact SaralPrivacy`,
  `Talk to a human — Contact SaralPrivacy`, `Start with Data Discovery`,
  `Map my data`, `Start the readiness check`, `Read the Daily Briefings`,
  `DPDPA Daily Briefings`, `DPDPA Glossary`

Follow-up sets (`FOLLOWUP_BY_JOURNEY`, refusal set, nav set) — copied verbatim in
`app/services/chat/orchestrate.py`.

The system prompt, `REGULATORY_CONTEXT`, the grounding-block wrapper tags and the
briefings block note are copied character for character into
`app/services/chat/system_prompt.py` / `orchestrate.py` / `briefings_live.py`.

## Environment variables read
`ANTHROPIC_API_KEY` (+ model id `claude-sonnet-5`, hard-coded as it was in the route),
`PINECONE_API_KEY`, `CHAT_HISTORY_SECRET`, `ADMIN_EMAIL`, database settings.
`maxDuration = 60` on `/api/chat` ⇒ the backend/ALB timeout for `POST /api/v1/chat`
must be at least 60 s.

## Out of scope / kept in the frontend
- `components/chat/*` — markup, copy and styling untouched; only the three fetch URLs in
  `useSetuChat.ts` change, and its `ChatMeta` import moves from `orchestrate.ts` to
  `protocol.ts`.
- `lib/chat/{protocol,strings,theme,triggers,journeys,site-routing,index-build,pinecone}.ts`
  and their `node:test` suites.
- `scripts/{build-chat-index,pinecone-ingest,redteam-chat}.mts` (redteam's URL is
  repointed at the proxy path); `eval/chat-golden.json` (copied into the backend as a
  pytest gate, original kept).
- `frontend/public/chat-index.json` — still the build output; the backend reads a copy.
