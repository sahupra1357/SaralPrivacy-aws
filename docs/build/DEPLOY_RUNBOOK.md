# Deploy runbook — Vercel (frontend) + Render (backend) + R2 (files)

Done once on 2026-09-20. Follow top to bottom for a fresh environment.
Values in `<>` are yours. Every gotcha listed was hit for real.

## 0. Repo

```bash
git init -b main
git add . && git commit -m "SaralPrivacy rebuild: Next.js + FastAPI + Postgres"
gh repo create <user>/<repo> --private --source=. --remote=origin --push
```

`.gitignore` must exclude before the first `git add`:
`.terraform/`, `*.tfstate*`, `crash.log` (a 655 MB provider binary blocks the push),
`.next/`, `.claude/`, `dpdpa-guide-*.pdf` (~92 MB, they go to R2 in step 3).

- `.env*` also hides `.env.example` — add `!.env.example` if you want it tracked.
- Disable any AWS deploy workflow (`.github/workflows/deploy-aws.yml` → `on: workflow_dispatch:`),
  otherwise every push fails on missing `vars.AWS_REGION`.

## 1. Database (Render Postgres)

1. New → Postgres, **Singapore**, paid plan (the free one is deleted after 30 days).
2. To share an instance with another app, create a separate database **as the Render user**
   so it owns it: `CREATE DATABASE spdb;`
3. Connect to *that* database and add the extension — extensions are per-database:
   ```sql
   \c spdb
   CREATE EXTENSION IF NOT EXISTS pgcrypto;
   ```
   (pgAdmin: Maintenance database `spdb`, SSL mode `require`, external host.)

Tables are **not** created here. `scripts/prestart.sh` runs `alembic upgrade head` on every
backend start; `backend_pre_start.py` creates the `ops`/`app` schemas first, which managed
Postgres needs because `backend/db/init/` only runs for local Docker.

## 2. Backend (Render Web Service)

- Docker. **Root Directory `backend`, Dockerfile Path `Dockerfile`, Context `.`**
  — `backend` in two fields resolves to `backend/backend` and fails to clone.
- Plan: Standard or better. The image ships Chromium for PDFs; 512 MB dies at runtime.
- Health check path: `/api/v1/utils/health-check/`
- No start command; the Dockerfile runs prestart then the API.

Environment (see `.env.example` for the full list):

```
PORT=8000
ENVIRONMENT=production
POSTGRES_SERVER=<internal host, e.g. dpg-xxxxx-a>   # NOT localhost
POSTGRES_PORT=5432                                   # NOT 5434 (that is the local Compose mapping)
POSTGRES_USER / POSTGRES_PASSWORD=<instance user/password>
POSTGRES_DB=spdb
SECRET_KEY=<python3 -c "import secrets; print(secrets.token_urlsafe(48))">
TOTP_ENCRYPTION_KEY=<python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())">
FIRST_ADMIN_EMAIL / FIRST_ADMIN_PASSWORD=<your admin>
FRONTEND_HOST=https://<vercel-url>        # CORS, invite + reset links, revalidate
NEXT_PUBLIC_SITE_URL=https://<vercel-url> # every email and report link
SMTP_HOST=smtp.resend.com  SMTP_PORT=465  SMTP_SSL=true
SMTP_USER=resend  SMTP_PASSWORD=<resend api key>
TZ=Asia/Kolkata
```

`ENVIRONMENT=production` refuses to boot on a default `SECRET_KEY`,
`FIRST_ADMIN_PASSWORD`, `POSTGRES_PASSWORD` or a blank `TOTP_ENCRYPTION_KEY`. Intended.

Expected log: `database ready` → three `Running upgrade` lines → API start.
Verify: `curl https://<render>/api/v1/utils/health-check/db` → `{"ok":true,"db":true}`

## 3. Files (Cloudflare R2)

1. Create bucket `<bucket>`; API token **Object Read & Write** scoped to it.
   (`ListBuckets` returns AccessDenied for a scoped token — that is normal;
   test with `aws s3api head-bucket --bucket <bucket> --endpoint-url <api-endpoint>`.)
2. Upload the guide PDFs (`aws s3 sync` trips over R2's listing — use `cp`):
   ```bash
   export AWS_DEFAULT_REGION=auto
   export AWS_REQUEST_CHECKSUM_CALCULATION=when_required
   export AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
   aws s3 cp frontend/public/guides/pdf/ s3://<bucket>/guides/pdf/ \
     --recursive --content-type application/pdf \
     --endpoint-url https://<account-id>.r2.cloudflarestorage.com
   ```
3. Settings → Public access → custom domain (preferred) or the r2.dev URL.
   The `*.r2.cloudflarestorage.com` endpoint is signed-API only and returns 400 to browsers.
   Public access exposes the **whole** bucket — keep private files in another bucket.

Add to Render (backend + worker):

```
GUIDE_PDF_BASE_URL=<public base>/guides/pdf   # without it, guide links 404
S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
S3_BUCKET=<bucket>   S3_REGION=auto   S3_ACCESS_KEY=<key>   S3_SECRET_KEY=<secret>
PUBLIC_ASSET_BASE_URL=<public base>           # bucket root, code appends the key
```

`could not prepare the storage bucket` on startup is expected: R2 has no S3 bucket policies.

## 4. Frontend (Vercel)

Import the repo, **Root Directory `frontend`**, framework Next.js, everything else default.
Functions region: Mumbai (bom1).

```
BACKEND_URL=https://<render>          # no trailing slash; without it the build hits localhost:8000
SECRET_KEY=<byte-identical to Render> # proxy.ts verifies the admin cookie
CRON_SECRET=<same as Render>
PUBLIC_ASSET_BASE_URL=<public base>
NEXT_PUBLIC_SITE_URL=https://<vercel-url>
NEXT_PUBLIC_SHOW_HINDI=true
```

All are compiled in at build time — **redeploy after any change**.

`next.config.ts` disables `output: "standalone"` and the `outputFileTracingRoot` pin when
`process.env.VERCEL` is set. Both are required for Docker and both break Vercel: with the
tracing root pinned to `frontend/`, traced paths lose their `frontend/` prefix and the
deploy dies at *Deploying outputs* with `ENOENT /vercel/path0/.next/package.json`.

Deploy by pushing to `main`. Avoid `vercel deploy --prebuilt`: it uploads ~6,000 files and
burns the Hobby cap of 5,000 uploads / 24 h.

## 5. Worker (Render Background Worker)

Same repo/Dockerfile and the same environment group. Docker Command: `python -m app.jobs`.
One instance only. Deploy after the web service, which owns migrations.

## 6. Verify

```bash
curl https://<vercel>/api/proxy/api/v1/utils/health-check/db   # {"ok":true,"db":true} = all three tiers
curl -I <public base>/guides/pdf/dpdpa-guide-en.pdf            # 200 application/pdf
curl -o /dev/null -w '%{http_code}\n' -X POST https://<vercel>/api/proxy/api/v1/forms/white-paper \
  -H 'content-type: application/json' -d '{}'                  # 400 = writes reach FastAPI
```

Then in a browser: `/admin/login` → password → **TOTP enrolment QR on first sign-in** →
6-digit code → `/admin`. Bouncing back to login means `SECRET_KEY` differs between the two
platforms. Never change `TOTP_ENCRYPTION_KEY` afterwards: it decrypts stored TOTP secrets.

## Admin account afterwards

`FIRST_ADMIN_*` seeds only when **no** admin row exists; later edits do nothing.
Change the password from Render → Shell:

```python
python - <<'PY'
from sqlmodel import Session
from app.core.db import engine
from app.crud import auth
with Session(engine) as s:
    u = auth.get_user_by_email(s, "<email>")
    auth.set_password(s, u, "<new password>")
    auth.revoke_all_sessions(s, u.id)
PY
```

Email: `UPDATE app.users SET email=…`. Full reseed: delete from `app.sessions` and
`app.invite_tokens` first (foreign keys), then `app.users`, then restart.

---

# Part 2 — AWS (the final target)

Architecture, cost table and operations commands live in `infra/AWS_DEPLOYMENT_PLAN.md`
(CloudFront → ALB → ECS Fargate web/api/worker → RDS 16, S3 assets, Resend SMTP).
Terraform is in `infra/terraform`, validated, **never applied**. This section is only what
changes because Render/Vercel/R2 ran first, plus the order to do it in.

## Before `terraform apply` — three edits

`infra/terraform/terraform.tfvars` (copied from `.example`) still carries pre-rebuild values:

| Key | Change to | Why |
|---|---|---|
| `github_repository` | `sahupra1357/SaralPrivacy-aws` | The example names the old repo; the OIDC trust policy is built from this, so pushes from the new repo are refused |
| `app_env.ANTHROPIC_MODEL` | `claude-sonnet-5` | `claude-sonnet-4-6` is previous-generation and costs more ($3/$15 vs $2/$10 per MTok) |
| `app_env.GUIDE_PDF_BASE_URL` | `<asset_base_url>/guides/pdf` | Not in Terraform's defaults. `app_env` merges into the backend/worker env. Set it after the first apply, when `terraform output -raw asset_base_url` is known, then re-apply |

Re-enable the push trigger in `.github/workflows/deploy-aws.yml` (Part 1 §0 disabled it), or
keep it manual and run the workflow from the Actions tab.

## Order

1. **Phases 0-3 of `infra/AWS_DEPLOYMENT_PLAN.md`** — state bucket, `terraform apply`, write
   `secrets.json` to Secrets Manager, set the GitHub repository variables from
   `terraform output`, run the deploy workflow.
   Use the *same* `SECRET_KEY`, `TOTP_ENCRYPTION_KEY`, `CRON_SECRET`, `EMAIL_LINK_SECRET`
   and `CHAT_HISTORY_SECRET` as Render, or every session, emailed link and enrolled
   authenticator breaks.
2. **Data: Render Postgres → RDS.** By then `spdb` holds real leads, assessments and
   briefings.
   ```bash
   pg_dump "postgresql://<user>:<pw>@<render-external-host>/spdb?sslmode=require" \
     --no-owner --no-privileges -Fc -f spdb.dump
   # from a bastion or `aws ecs execute-command` into the api task:
   pg_restore --no-owner --no-privileges -d "$DATABASE_URL" spdb.dump
   ```
   The api task has already run `alembic upgrade head`, so restore **data only**
   (`--data-only --disable-triggers`) or drop and recreate the schemas first — a plain
   restore over migrated tables fails on duplicate keys.
3. **Files: R2 → S3.** Terraform creates the assets bucket; the task role replaces the R2
   keys (`S3_ENDPOINT` stays empty = real S3).
   ```bash
   aws s3 sync frontend/public/guides/pdf/ s3://<assets_bucket>/guides/pdf/ --content-type application/pdf
   rclone sync r2:<r2-bucket>/infographics s3:<assets_bucket>/infographics   # or re-download and aws s3 cp
   ```
   Then rewrite stored infographic URLs in the database from the R2 base to
   `asset_base_url`, and set `PUBLIC_ASSET_BASE_URL` + `GUIDE_PDF_BASE_URL` accordingly.
4. **Verify on the ALB/CloudFront URL before DNS moves** — the Part 1 §6 checks, plus
   `/admin` sign-in and one PDF generation (Playwright is the piece Render proved, ECS must
   prove again at 1 vCPU / 2 GB).
5. **Phase 5 cutover.** Copy Resend DKIM/SPF/DMARC, Search Console TXT and MX into the
   Route 53 zone *first*, then repoint the registrar at `route53_nameservers`.
   Keep Render and Vercel running until DNS has propagated; then disable the Vercel project
   so nothing serves or sends twice.

## What stays behind

- **R2** can stay as the public asset host (cheaper egress) — then keep `GUIDE_PDF_BASE_URL`
  and `PUBLIC_ASSET_BASE_URL` on the R2 domain and skip step 3. But `S3_ACCESS_KEY` /
  `S3_SECRET_KEY` are **not** in `app_secret_keys`, so uploads would need those keys added
  to the secret; the S3 bucket Terraform creates is the lower-friction path.
- **Render Postgres**: keep the instance until a week of AWS backups exists.
- Vercel's project can be deleted once DNS is stable; the Render services likewise.
