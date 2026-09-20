# chat — status: static-clean
Updated: 2026-09-18

Inventory: `docs/build/inventory/chat.md` (4 routes + 4 in-stream behaviours, every row has a test).
No test run. No git command run except read-only `git show HEAD:` to recover the originals of
the four partially edited files for `_backup/`.

## Files created
Backend
- backend/app/api/routes/chat.py — `POST /api/v1/chat` (two-phase `StreamingResponse` over
  `app.services.llm.stream`), `POST /chat/feedback`, `POST /chat/handoff`, `GET /chat/health`
- backend/app/models/chat.py — `ChatFeedback` (`ops.chat_feedback`, camelCase column names kept)
- backend/app/crud/chat.py — `create_feedback`, `insert_lead`, `insert_consent_log`
- backend/app/services/retrieval.py — BM25 over the local index, Pinecone search/stats via httpx
  (no SDK), RRF fusion, platform tour
- backend/app/services/chat/{__init__,protocol,strings,redact,site_routing,journeys,guard,
  system_prompt,knowledge,orchestrate,handoff,briefings_live}.py
- backend/app/data/chat-index.json (copy of frontend/public/chat-index.json),
  site-routes.json, glossary.json, checklist.json (exported from the TS sources),
  chat-golden.json (copy of frontend/eval/chat-golden.json)
- backend/app/tests/chat/{__init__,test_guard,test_redact,test_retrieval,test_site_routing,
  test_journeys,test_knowledge,test_orchestrate,test_handoff_packet,test_routes_chat,
  test_routes_feedback,test_routes_handoff,test_routes_health,test_golden}.py

Frontend
- frontend/scripts/export-chat-kb.mjs — regenerates backend/app/data/{site-routes,glossary,
  checklist,chat-index}.json from the TS sources (one authoring source for routes/glossary)
- frontend/tests/unit/chat/protocol.test.ts, frontend/tests/unit/chat/useSetuChat.test.tsx

## Files changed (frontend call sites etc.)
- frontend/components/chat/useSetuChat.ts — the three fetches now go to
  `/api/proxy/api/v1/chat`, `/chat/feedback`, `/chat/handoff` (via `CHAT_ENDPOINTS`);
  `ChatMeta` imported from `lib/chat/protocol`. No markup/copy change.
- frontend/lib/chat/protocol.ts — gains the `ChatMeta`/`ChatCitation`/`ChatAction`/
  `EscalationReason` types (moved from orchestrate.ts) and `CHAT_ENDPOINTS`.
- frontend/lib/chat/pinecone.ts — query-time `pineconeSearch` removed (now in retrieval.py);
  ingest half kept for scripts/pinecone-ingest.mts.
- frontend/scripts/redteam-chat.mts — target URL now `/api/proxy/api/v1/chat`.
- Kept untouched (client helpers the widget imports): lib/chat/{strings,theme,triggers,journeys,
  site-routing,index-build}.ts and their node:test suites.

## Moved to _backup (also in _backup/LEDGER.md)
21 files moved with `mv`: app/api/chat/{route,feedback/route,handoff/route,health/route}.ts,
lib/chat/{orchestrate,system-prompt,briefings-live,guard,retrieve,redact,knowledge-tools,handoff}.ts
with their tests (orchestrate, escalation, golden, guard, retrieve, redact, knowledge-tools,
handoff .test.ts), scripts/smoke-chat.mts.
4 partial files (original copied from HEAD, live file edited): lib/chat/protocol.ts,
lib/chat/pinecone.ts, components/chat/useSetuChat.ts, scripts/redteam-chat.mts.

## Needs from orchestrator
- include_router: in backend/app/api/main.py add `chat` to the routes import
  (`from app.api.routes import assessments, chat, login, mfa, notices, users, utils`) and
  `api_router.include_router(chat.router)`
- models import: `from app.models.chat import ChatFeedback  # noqa: F401` (+ `"ChatFeedback"` in `__all__`)
- jobs: none
- dependencies: backend none (httpx, anthropic already present); frontend none
- **REQUIRED shared change, backend/app/services/llm.py**: the route calls
  `llm.stream(system, messages, model="claude-sonnet-5", max_tokens=600)`. The TS route omitted
  `temperature` because the claude-5 family rejects it, but `llm.stream` always sends
  `temperature=0.3`. Make it optional and omit it when None:
  `temperature: float | None = None` and pass `**({"temperature": temperature} if temperature is not None else {})`
  (same for `complete`). Without this the live chat stream fails over to the apiError tail.
- Migration: `ops.chat_feedback` (from ChatFeedback). The handoff route and its tests also need
  `ops.leads` and `ops.consent_log` (forms module) — integrate forms before running chat tests.
- The backend image must ship `backend/app/data/*.json` (read at runtime). Timeout for
  `POST /api/v1/chat` behind the proxy/ALB must be >= 60 s (old `maxDuration = 60`).
- Stale `frontend/.next/types/validator.ts` still references the moved `app/api/chat/*`
  routes (4 of the 19 `tsc` errors, all in `.next/`); cleared by the next `next build`.

## Open questions
- Handoff writes `ops.leads` / `ops.consent_log` with parameterised SQL (crud/chat.py) instead of
  importing `app.models.forms.Lead`/`ConsentLog`, to avoid coupling to a file under parallel
  edit. Collapse onto forms crud after integration if preferred (same note as assessments).
- The consultation alert email is built inline in routes/chat.py with the same subject
  (`New Consultation Request — <name> from <company>`), rows and closing line; styling is
  plain. Switch to the shared template when forms/outreach provide one. All values are now
  escaped (the TS template escaped only issue_summary).
- Geo fields read `x-vercel-ip-city/country/country-region`; behind an ALB these are absent, so
  city/country/region will be blank unless the CDN sets them.
- Live briefings keep the TS field precedence exactly, so `why_it_matters` is still never used
  as a summary (same as today via Supabase). One-word fix in briefings_live.py if wanted.
- `CHAT_MODEL = "claude-sonnet-5"` is pinned in the route, as the TS pinned it; a
  `CHAT_MODEL` setting in core config would make it configurable.
- lib/chat/journeys.ts still contains `sanitizeState`/`detectJourney` (pure, now unused by the
  frontend); left in place so the widget file is unchanged.
