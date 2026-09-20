# Manual acceptance checklist (wave 5)

Run the local stack (README → "Run it locally"), open the live site (saralprivacy.com) in a
second window, and compare. Tick each line when the local result matches the live site
(or the stated expected result, where live cannot be compared).

Where to look:
- **Site**: http://localhost:3000 · **Inbox** (every email the app sends): http://localhost:1080
- **Admin**: http://localhost:3000/admin/login · **API docs**: http://localhost:8000/docs
- **DB**: `docker compose exec db psql -U saral -d saralprivacy` · **Files**: http://localhost:9001
- **Job logs**: `docker compose logs -f worker`

Note: until your data is loaded, lists (briefings, blog, admin tables) are empty; the
"list" checks below are for after the data migration.

## A. Public pages (render, copy, links, layout identical to live)

- [ ] `/` home — hero, briefings deck, newsletter block, footer
- [ ] `/hi` and one `/hi/...` page — Hindi chrome; English fallback where untranslated
- [ ] `/about`, `/contact`, `/faq`, `/glossary`, `/privacy`, `/terms`, `/media`, `/media/coverage`, `/media/press-wall`
- [ ] `/learn`, `/learn/what-is-dpdpa`, `/learn/dpdp-act-2023`, `/learn/dpdp-rules-2025-plain-english-guide`, two other `/learn/[topic]`
- [ ] `/industries` and all 12 `/industries/<sector>` pages; `/industries/<sector>/data-flow` for mapped sectors
- [ ] `/assessment` and all 12 `/assessment/<sector>` start screens
- [ ] `/briefings`, `/briefings/all`, one `/briefings/[slug]` (after data load)
- [ ] `/blog`, one `/blog/[slug]` with its infographic image (after data load; image loads from storage)
- [ ] `/resources`, `/compliance-checklist`, `/data-mapping`, `/discovery`, `/penalty-calculator`, `/rights` (+ `/rights/access`, `/rights/erasure` redirect to `/privacy#data-rights`)
- [ ] `/tools/dpdpa-privacy-notice-generator`
- [ ] `/white-paper` — language picker lists 7 languages; PDFs open from `/guides/pdf/...`
- [ ] `/subscribe` → `/#newsletter`; `/unsubscribe` → `/consent-preferences`; `/webinars` → `/resources`
- [ ] Uppercase URL (e.g. `/Blog`) redirects to lowercase; `/en/learn` redirects to `/learn`
- [ ] `/sitemap.xml`, `/robots.txt`, `/llms.txt`, `/llms-full.txt`, `/manifest.webmanifest`
- [ ] 404 page for an unknown path; `/offline` page

## B. Public actions

**Forms**
- [ ] Contact form → success message; **Inbox**: "New Consultation Request — <name> from <company>" to ADMIN_EMAIL; **DB**: row in `ops.leads`
- [ ] Contact form with an empty required field → blocked in the browser; honeypot filled → success shown, nothing stored
- [ ] Newsletter sign-up (home + briefing card) → success; **Inbox**: welcome email; **DB**: `ops.subscribers` + `ops.consent_log`
- [ ] Sign up again with the same email → same success message, no duplicate row
- [ ] `/consent-preferences` → unsubscribe works; status `unsubscribed` in DB; no further briefing emails to it
- [ ] White paper: choose language, fill form → download link works for that language; consent rows per ticked box
- [ ] Template download (resources gate + report gate + template form) → email with the template link; link opens the file
- [ ] Template form with invalid Indian mobile → error before submit

**Assessments**
- [ ] General assessment end to end → result screen; **Inbox**: result email with report link; **DB**: `app.assessments`
- [ ] One sector assessment (e.g. recruitment) end to end → same checks
- [ ] Open the emailed report link `/report/<token>` → report renders; expired/unknown token → expiry screen

**Notice pack**
- [ ] Fill the generator, unlock with work email → lead captured (`app.notice_captures`), founder alert in Inbox
- [ ] Download PDF (English) and PDF (Hindi) → correct content, header/footer, Devanagari renders
- [ ] Copy HTML / print fallback still work

**Discovery**
- [ ] Complete discovery → result panel; request the inventory → **Inbox**: email with CSV attachment

**Chat (Setu)** — needs `ANTHROPIC_API_KEY` (and `PINECONE_API_KEY` for vector search) in `.env`
- [ ] Ask a DPDPA question → answer streams, then citations/actions appear
- [ ] Ask something off-topic / below the retrieval floor → canned refusal, no model call
- [ ] Thumbs down with a reason → row in `ops.chat_feedback`
- [ ] Hand-off to a human → **Inbox**: consultation email; **DB**: `ops.leads` row
- [ ] Rapid-fire messages → "Too many requests…" message

**Outreach (public side)**
- [ ] Magic subscribe link (`/subscribe?token=…` from an outreach email) → subscribed
- [ ] Outreach unsubscribe link → unsubscribed

## C. Admin

**Auth**
- [ ] Login with FIRST_ADMIN_EMAIL/PASSWORD → QR code enrolment → 6-digit code → dashboard
- [ ] Log out → back to login; `/admin` without a session redirects to login
- [ ] Wrong password → "Invalid credentials." (same text for an unknown email); 6 fast attempts → "Too many attempts…"
- [ ] Wrong TOTP code → "Code did not match. Try again."
- [ ] Invite a blogger (Bloggers page) → **Inbox**: invite email → set-password page (12+ chars) → login with TOTP enrolment
- [ ] Blogger can reach only `/admin/blog…`; other admin URLs redirect to `/admin/blog`
- [ ] Deactivate the blogger → their login is refused ("Access denied.")

**Dashboard and tables** (after data load)
- [ ] Dashboard counts, activity feed and risk split match the live admin
- [ ] Leads, Subscribers, Downloads, Consent, Consultations, Survey responses, Assessments, Discovery pages list the same rows as live
- [ ] Send a report from Assessments → **Inbox**: report email

**Editorial**
- [ ] Blog: new post → validate → revise with AI → generate infographic (image appears, stored under `infographics/`) → publish → visible on `/blog` without waiting (revalidated)
- [ ] Blog: edit an existing post, save, reopen
- [ ] Briefings: generate (or wait for the job), approve → approval email flow; send → **Inbox**: briefing emails to active subscribers only; delete a draft
- [ ] `GET /api/briefings/today` with the bearer secret returns today's briefing (n8n contract)

**SEO / AI citations / outreach**
- [ ] Citations: "Run now" → button polls, then new rows appear (needs OPENROUTER_API_KEY)
- [ ] SEO: "Run inspection" → button polls, verdict shows (needs GSC_SERVICE_ACCOUNT_JSON)
- [ ] Outreach: import a CSV (with an Excel BOM, a duplicate and an invalid row) → counts shown; stats page correct

## D. Scheduled jobs (run each once by hand)

```bash
docker compose exec worker python -m app.jobs run outreach-send
docker compose exec worker python -m app.jobs run briefing-send
docker compose exec worker python -m app.jobs run editorial_daily_briefing   # needs Google Sheet/SERP/KIE keys or ROADMAP CSV
docker compose exec worker python -m app.jobs run aeo-panel
docker compose exec worker python -m app.jobs run seo-inspect
```
- [ ] Each exits 0 (or logs a clear "skipped: …" reason); emails land in the Inbox; running the same job twice does not send twice

## E. Restart and resilience

- [ ] `make down && make up` → data still there, frontend log shows `[warmup] refreshed pre-built pages`
- [ ] Stop the backend (`docker compose stop backend`) → public pages still render; forms show the friendly error; start it again → recovers

Sign-off: ______________________  Date: __________
