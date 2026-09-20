# assessments — status: static-clean
Updated: 2026-09-18

Inventory: `docs/build/inventory/assessments.md` — every row implemented and tested.

## Files created

Backend
- `backend/app/models/assessments.py` — `Assessment` (`app.assessments`), all 40 columns
  including `created_at_attr` and the text `report_token_expires_at`.
- `backend/app/crud/assessments.py` — `create_assessment`, `get_by_report_token`,
  `get_by_id`, `mark_report_email_sent`, `log_consent`, `upsert_subscriber`.
- `backend/app/api/routes/assessments.py` — `POST /api/v1/assessments`,
  `GET /api/v1/assessments/report/{token}`, plus the band colours, band CTAs, checklist
  table and scorecard maths ported verbatim from `lib/email-templates.ts`.
- `backend/app/email-templates/assessment_base.html` — verbatim port of `baseLayout()`.
- `backend/app/email-templates/assessment_alert.html` — `assessmentAlertTemplate`.
- `backend/app/email-templates/assessment_survey_result.html` — `surveyResultEmailTemplate`.
- `backend/app/crud/__init__.py` — created only because it did not exist (docstring only;
  the notices builder had already added `app/crud/notices.py` without it).
- `backend/app/tests/assessments/{__init__,conftest}.py`,
  `test_routes.py` (17 tests), `test_crud.py` (9 tests), `test_emails.py` (10 tests + 5 parametrised subject cases).

Frontend
- `frontend/tests/unit/assessments/report-page.test.tsx`
- `frontend/tests/unit/assessments/industry-clients.test.tsx`
- `frontend/tests/unit/assessments/survey-client.test.tsx`
- `frontend/tests/unit/assessments/answer-summary.test.tsx`
- `frontend/tests/unit/assessments/call-sites.test.ts`

## Files changed (frontend call sites etc.)
- All 13 submitters now `fetch("/api/proxy/api/v1/assessments", …)` instead of
  `fetch("/api/assessment", …)`; nothing else in those files changed:
  `app/[locale]/assessment/SurveyClient.tsx` and the 12
  `app/[locale]/assessment/<sector>/<Sector>AssessmentClient.tsx` files.
- `app/[locale]/assessment/SurveyClient.tsx` additionally exports `buildAnswerSummary`
  (moved out of the retired route handler) and posts `answerSummary` in the payload.
- `app/(backoffice)/report/[token]/page.tsx`: `findOneBy("assessments", "report_token", token)`
  → `apiGet("/assessments/report/<token>", { revalidate: 0 })`. Markup, copy, expiry
  screen, projection maths and the template list are untouched.

## Moved to _backup (also in _backup/LEDGER.md)
- `frontend/app/api/assessment/route.ts` → `_backup/webapp/app/api/assessment/route.ts`
  (1 file). The now-empty `frontend/app/api/assessment/` directory was removed.
- Nothing else. `lib/data/industry-assessment/**` and `lib/data/dpdpa-assessment.ts` are
  pure browser TypeScript with 27 live importers and **stay in the frontend** — there was
  no server behaviour in them to move.

## Needs from orchestrator
- include_router:
  `api_router.include_router(assessments.router)`
  with `from app.api.routes import assessments` in `backend/app/api/main.py`.
- models import:
  `from app.models.assessments import Assessment  # noqa: F401`
  in `backend/app/models/__init__.py` (and `"Assessment"` in `__all__`).
- migration: autogenerate picks up `app.assessments` from that import. The module also
  writes `ops.consent_log` and `ops.subscribers` with raw SQL, so those two tables must
  exist in the migration — they come from the **forms** module's models.
- jobs: none.
- dependencies: none new (fastapi, sqlmodel, jinja2, pydantic already in
  `backend/pyproject.toml`; no frontend package added).
- Stale build artifact: `frontend/.next/types/validator.ts` still references
  `app/api/assessment/route.js` and fails `tsc` until the next `next build`. Same class
  of stale reference as the auth and notices moves.

## Open questions
1. **`upsert_subscriber` / `log_consent` are duplicated with forms.** They are raw SQL in
   `app/crud/assessments.py` so two wave-1 builders never declare the same SQLModel
   table. Once forms is merged, the orchestrator may want them to call
   `app.crud.forms.upsert_subscriber` instead. Behaviour is a faithful port of
   `lib/subscribers.ts` either way.
2. **`source` → `consent_source`.** `lib/subscribers.ts` inserts a key `source`, but
   `ops.subscribers` has no such column — only `consent_source`, whose CHECK allows
   exactly `'assessment_form'`. Today's Supabase write therefore looks like it has been
   failing silently (the caller is `.catch()`-ed). The port writes `consent_source`, i.e.
   it fixes the write. Flagging it because it changes what the live table receives.
3. **`answerSummary` now travels in the request.** Building it server-side would mean
   copying all of `lib/data/dpdpa-assessment.ts`'s question and option text into Python.
   `SurveyClient` computes it with the same helper the route handler used and posts it;
   the backend tolerates its absence. The 12 industry clients send nothing, which matches
   today exactly (their answer keys never matched the general question keys, so the old
   `buildAnswerSummary` already returned `[]` for them).
4. **Report link base URL.** The route handler hardcoded
   `https://saralprivacy.com/report/<token>` in the email. The port uses
   `settings.NEXT_PUBLIC_SITE_URL` so the link works in the local stack during wave 5;
   production behaviour is identical as long as that key is set to the live origin. Every
   other URL in the two emails stays hardcoded, as it is marketing copy.
5. **Error envelope.** `HTTPException` renders `{"detail": "…"}` where the route handler
   rendered `{"error": "…"}`. Strings and status codes are identical and none of the 13
   callers reads the failure body (they branch on `res.ok` / `res.status === 429`).
6. **Geo headers.** `x-vercel-ip-city/-country/-country-region` are still read when
   present but the core proxy does not forward them, so off Vercel `country`/`region`
   will be empty. The self-reported `body.city` path is unchanged. Decide at wave 3
   whether the proxy should pass a geo header from the ALB.

## Static checks (run once)
- `ruff check` on the 3 module files + `app/tests/assessments` — **All checks passed!**
- `python -m compileall -q` on the same files — clean.
- `npx tsc --noEmit -p frontend/tsconfig.json` — no error in any assessments file. The
  only output is the stale `.next/types/validator.ts` references listed above and a
  pre-existing `lib/auth/session.ts` error owned by the auth module.
- No test was executed. No git command that changes anything was run.
