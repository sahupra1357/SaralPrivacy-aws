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

---

# Part 3 — What each option costs

All figures are approximate **ap-south-1 / current provider list prices, monthly, light
traffic**, gathered 2026-09-20. Re-check the calculators before committing — they move.
The point of this section is the *shape* of each bill, which does not move: AWS charges for
provisioned capacity, the PaaS options charge per service.

## A. AWS ECS Fargate (what infra/terraform builds)

Defaults as shipped: single NAT, 2 AZs, WAF on, 20 GB storage, Performance Insights off.

| Item | Rate | USD/mo |
|---|---|---|
| ALB | $0.0225/hr + LCUs | 20 |
| NAT gateway ×1 | $0.045/hr + $0.045/GB | 33 |
| Fargate api 1 vCPU / 2 GB | $0.04048/vCPU-hr + $0.004445/GB-hr | 36 |
| Fargate worker 1 vCPU / 2 GB | same | 36 |
| Fargate web 0.5 vCPU / 1 GB | same | 18 |
| RDS db.t4g.micro + 20 GB gp3 | ~$0.016/hr + storage | 15 |
| WAF | $5 ACL + ~$1/rule + per-request | 8-12 |
| CloudFront | ~$0.17/GB (India edge) | 2 |
| Route 53, Secrets Manager ×2, CloudWatch, ECR, S3 | | 4-6 |
| **Total** | | **≈ 170** |

~90% is idle capacity: three tasks running 24x7 plus an ALB and NAT rented by the hour.
Ten visitors or ten thousand, the bill is the same.

Trims: `enable_nat_gateway = false` (-33, tasks move to public subnets behind SGs),
`enable_waf = false` (-10), worker on Fargate Spot (-25, interruptible but the jobs hold
Postgres advisory locks), merge the worker into the api container (-36), api at
0.5 vCPU / 2 GB (-15, slower Chromium PDFs). Trimmed sensibly: **≈ 100**.

## B. AWS, single EC2 box running docker compose

The repo's `docker-compose.yml` already runs the whole stack. One instance, Caddy or nginx
terminating TLS with Let's Encrypt, no ALB, no NAT, no ECR.

| Item | Rate | USD/mo |
|---|---|---|
| EC2 t4g.medium (2 vCPU burst, 4 GB) | $0.0336/hr | 25 |
| EBS gp3 30 GB | ~$0.092/GB-mo | 3 |
| EBS snapshots (daily, ~30 GB) | $0.05/GB-mo | 1-2 |
| Elastic IP (attached) | free while in use | 0 |
| Data transfer out | first 100 GB/mo free | 0 |
| Route 53 | | 0.50 |
| **Total, Postgres in a container on the same box** | | **≈ 30** |
| Same, but Postgres on RDS db.t4g.micro | +15 | **≈ 45** |

t4g.small (2 GB) at ~$12 is tempting but too tight once Chromium, Next.js and Postgres share
the box. A 1-year Savings Plan takes ~40% off the instance; Spot is ~$7/mo and can be
reclaimed at any time — not for the only server.

What you give up: no autoscaling, no multi-AZ, you patch the OS, backups are your job,
`docker compose pull && up -d` is the deploy (brief downtime), and one instance failure is a
full outage. Sensible for a pre-revenue product; not for one with an SLA.

## C. Vercel + Render + R2 (what is running today)

| Item | Plan | USD/mo |
|---|---|---|
| Vercel | Hobby | 0 |
| Vercel, if commercial use | Pro (per seat) | 20 |
| Render web service (backend, needs RAM for Chromium) | Standard 2 GB | 25 |
| Render background worker | Starter 512 MB | 7 |
| Render Postgres | shared with an existing instance | 0 |
| Render Postgres, if dedicated | Basic 1 GB | 19 |
| Cloudflare R2 | ~100 MB stored, egress free | <1 |
| Resend | free to 3k emails/mo | 0 |
| **Total as configured** | | **≈ 33** |
| **Total, commercial + dedicated DB** | | **≈ 72** |

**Read Vercel's Hobby terms.** It prohibits commercial use, and saralprivacy.com is a
commercial product — budget for Pro.

## Choosing

| | Fargate | EC2 box | Vercel + Render |
|---|---|---|---|
| Monthly | 100-170 | 30-45 | 33-72 |
| Ops burden | low | **high** | lowest |
| Scales without work | yes | no | yes |
| Survives one failure | yes | **no** | yes |
| Data in India | **yes** (ap-south-1) | **yes** | no (Render SG, R2 ENAM) |

The residency row is the one that may decide it: this product sells DPDPA readiness, and
"where does our data live" is a question customers will ask. A middle path is RDS in
ap-south-1 (~15) with the app staying on Render.

---

# Part 4 — Which AWS shape, and when to move

Conclusion of the 2026-09-20 sizing discussion. Fargate (Part 2) stays the documented
target, but it is not the cheapest correct answer at this stage.

## The recommended shape: ASG(min=1) + RDS + Route 53 health check — ≈ 48/mo

One EC2 instance running the repo's `docker-compose.yml`, in an Auto Scaling Group with
`min=max=1` across two AZs, RDS in a private subnet, nginx or Caddy terminating TLS.

The ASG is a supervisor, not a scaler: when the health check fails it terminates the
instance and launches a replacement in the other AZ, unattended.

| Step | Time |
|---|---|
| Health check marks unhealthy (2 × 30s) | ~1 min |
| Instance boots | ~1 min |
| `docker compose up` pulls + starts | 1-3 min |
| Traffic moves | instant behind an ALB; +TTL with DNS |
| **Unattended recovery** | **3-6 min** |

Three things decide whether that actually happens:

1. **No state on the box.** Postgres must be RDS. A replacement with a container database
   comes up empty and you are restoring a snapshot — an hour, not minutes.
2. **It must rebuild itself unattended** — user-data script or a baked AMI. The backend
   image carries Chromium and is large; bake it into the AMI or accept the slower pull.
3. **Traffic has to move** — ALB (instant, +20/mo) or a Route 53 health check with a 60s
   TTL (~0.50/mo, some clients cache longer) or an Elastic IP claimed on boot.

Not covered by failover: **deploy downtime** (compose drops requests for seconds — run two
backend replicas behind local nginx and restart them one at a time), and **RDS single-AZ**
(AWS replaces a failed instance in 5-10 min; Multi-AZ cuts that to ~60s for +15/mo).

**Terminate the instance on purpose once, on a quiet afternoon.** Untested failover almost
always hides one missing piece: an unset env var, an unpersisted TLS cert, a security group
that named the old instance.

| Setup | USD/mo | Recovery |
|---|---|---|
| Single box, manual rebuild | 45 | 20-60 min |
| **ASG min=1 + RDS + Route 53 health check** | **48** | **3-6 min** |
| ASG min=1 + RDS + ALB | 68 | 1-2 min |
| Two instances + ALB | 93 | 0 |
| Fargate (Part 2) | 100-170 | 0 |

## Capacity on t4g.medium (2 vCPU, 4 GB) + db.t4g.micro

Most pages are SSG and absorbed by the CDN, so the origin sees API calls, PDFs and chat.

| Load | Verdict |
|---|---|
| 100k registered accounts | Fine — rows are cheap |
| 100k visitors/month (~3.3k/day) | Comfortable |
| 100k visitors/day | Needs t4g.large (~50) + CDN. Feasible |
| 100k concurrent | No |

**What breaks first, in order:**

1. **Playwright PDFs** — 300-500 MB and 1-3s each, so 2-3 concurrent on 4 GB; the 4th
   queues. The true ceiling, and it is about simultaneous renders, not daily volume.
2. **CPU credits** — t4g.medium's baseline is 20% of 2 vCPU. Sustained load above it drains
   `CPUCreditBalance` and silently bills unlimited-mode surcharges. Move to m7g.large rather
   than pay the surcharge indefinitely.
3. **RDS connections** — db.t4g.micro caps near 80-100. Binds once several app instances run.
4. **Chat streams** — I/O bound; Anthropic rate limits bite before local CPU does.

## Scaling path

- **Vertical first:** t4g.medium → large → xlarge. One Terraform line, minutes of downtime.
  Carries roughly to 100k visitors/day.
- **Horizontal later:** ALB + `max=3` (+20/mo). The app is already stateless — JWT cookie,
  RDS, object storage, no local disk — and the worker's Postgres advisory locks already
  prevent double job runs across instances, so this is configuration, not a rewrite.
- **Fargate** when the trigger is operational rather than capacity: promised uptime, spiky
  campaign traffic, more than one person deploying, or an audit demanding immutable
  infrastructure and per-service logging. None of those are user counts.

**Alarms that tell you when to resize:** `CPUCreditBalance` trending to zero, memory >80%
(needs the CloudWatch agent), PDF endpoint p95 latency climbing, RDS `DatabaseConnections`
near the cap.

---

# Part 5 — Switching the AWS stack between EC2 and Fargate

`infra/terraform` builds either compute layer from one variable. Everything else —
VPC, RDS, S3, ECR, Secrets Manager, Route 53, ACM, CloudFront, SNS alarms — is shared and
untouched by the switch.

```hcl
compute_mode = "ec2"      # ASG(min=max=1) + docker compose, ~48/mo
compute_mode = "fargate"  # ECS web/api/worker behind an ALB, ~170/mo (the default)
```

`terraform apply` after the change migrates between them. Switching is **not** zero
downtime: the old compute is destroyed and the new one built, a few minutes with the
database untouched. Do it deliberately, not during traffic.

## What each mode creates

| | `ec2` | `fargate` |
|---|---|---|
| Compute | 1 × EC2 in an ASG (min=max=1), docker compose | 3 ECS services (web, api, worker) |
| Edge | Caddy on the box, TLS from Let's Encrypt | ALB + WAF |
| Public address | Elastic IP, re-claimed by the replacement | ALB DNS |
| DNS | A records → EIP | Alias → ALB/CloudFront |
| Logs | one group, streams api/worker/web/caddy | one group per service |
| Scaling | resize the instance | ECS target tracking, 1-4 tasks |
| Extra cost | — | ALB 20 + NAT 33 + WAF 10 |

In `ec2` mode set `enable_nat_gateway = false`: the instance lives in a public subnet with
its Elastic IP and never needs NAT. WAF is skipped automatically (it can only attach to the
ALB), and `enable_cloudfront` still works — CloudFront's origin becomes `origin.<domain>`,
pointing at the EIP, with Caddy terminating TLS there.

## Files

- `ec2.tf` — EIP, security group, instance role, launch template, ASG, Route 53 health
  check, instance alarms (status check, CPU credits, memory).
- `templates/user_data.sh.tftpl` — boot script: claim the EIP, install docker, read both
  secrets into `/opt/app/.env`, write `docker-compose.yml` + `Caddyfile`, start. Idempotent,
  because the ASG reruns it on every replacement.
- `alb.tf`, `ecs.tf`, `waf.tf` — gated `count = local.is_fargate ? 1 : 0`.
- `dns.tf` — alias records in fargate mode, A records to the EIP in ec2 mode.

## Operating the ec2 mode

```bash
# shell on the box (no SSH key, no bastion)
aws ssm start-session --target "$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names saralprivacy-prod-app \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)"

# deploy: images are already in ECR (the GitHub workflow builds them)
aws ssm send-command --document-name AWS-RunShellScript \
  --targets Key=tag:Name,Values=saralprivacy-prod-app \
  --parameters 'commands=["systemctl start app-deploy"]'

# logs, all four containers
aws logs tail /ec2/saralprivacy-prod --follow

# run one job now
docker compose -f /opt/app/docker-compose.yml exec worker python -m app.jobs run briefing-send
```

`.github/workflows/deploy-aws.yml` rolls **ECS services** and therefore only fits fargate
mode. In ec2 mode, let it build and push the images, then replace the three "Roll …" steps
with the `ssm send-command` above.

## Before trusting the failover

Terminate the instance on purpose and watch a replacement come up:

```bash
aws autoscaling terminate-instance-in-auto-scaling-group \
  --instance-id <id> --should-decrement-desired-capacity false
```

Expect ~3-6 minutes to a working site. What usually breaks the first time: a secret key the
user-data does not read, an ACME rate limit from repeated certificate issuance, or the EIP
association failing because the role lacks `ec2:AssociateAddress`. Better to learn that on a
quiet afternoon than during an outage.

---

# Part 6 — AWS deploy, step by step

Copy-paste walkthrough for `compute_mode = "ec2"` (Part 5 covers the fargate variant; the
steps are identical except 4 and 6). Assumes Part 1 is live on Render/Vercel and tested.

Set these once per shell:

```bash
cd infra/terraform
export AWS_PROFILE=<your profile>
export AWS_REGION=ap-south-1
export TF=terraform
```

## 1. Prerequisites

AWS CLI logged in with admin rights on the target account, Terraform >= 1.10, Docker with
buildx, and the domain's registrar login. Create the state bucket once:

```bash
aws s3api create-bucket --bucket saralprivacy-tfstate --region $AWS_REGION \
  --create-bucket-configuration LocationConstraint=$AWS_REGION
aws s3api put-bucket-versioning --bucket saralprivacy-tfstate \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket saralprivacy-tfstate \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

cp backend.hcl.example backend.hcl
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`: `compute_mode = "ec2"`, `enable_nat_gateway = false` (the instance
uses its Elastic IP), `alarm_email`, `domain_name`, `github_repository`, and — if the zone
already exists — `create_hosted_zone = false` + `hosted_zone_id`.

```bash
$TF init -backend-config=backend.hcl
```

## 2. Create the registries first

The instance pulls its images at boot, so they must exist before it launches.

```bash
$TF apply -target='aws_ecr_repository.this'
export ECR_WEB=$($TF output -raw ecr_web_repository_url)
export ECR_API=$($TF output -raw ecr_api_repository_url)
aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin "${ECR_WEB%%/*}"
```

## 3. Build and push both images

**Architecture must match `cpu_architecture`** — `linux/arm64` for a t4g instance. A
mismatch fails at plan time now, but the images are yours to get right.

```bash
cd ../..                      # repo root
docker buildx build --platform linux/arm64 -t "$ECR_API:latest" --push backend
docker buildx build --platform linux/arm64 \
  --build-arg PUBLIC_ASSET_BASE_URL="https://<assets-bucket>.s3.ap-south-1.amazonaws.com" \
  --build-arg NEXT_PUBLIC_SHOW_HINDI=true \
  -t "$ECR_WEB:latest" --push frontend
cd infra/terraform
```

The frontend bakes `PUBLIC_ASSET_BASE_URL` and `NEXT_PUBLIC_SHOW_HINDI` at build time. The
bucket name is `<project>-<env>-assets`; if you would rather not guess, run step 4 first,
read `terraform output -raw asset_base_url`, then build and push.

## 4. Provision everything else

```bash
$TF plan -out=tfplan      # read it: ~60 resources, no deletions on a first run
$TF apply tfplan
```

Route 53, ACM (DNS-validated, so the zone must be authoritative or validation hangs), VPC,
RDS, S3, Secrets Manager, ECR, the EIP, launch template, ASG, CloudFront, alarms.

If the zone was created by this apply, move the registrar's nameservers to
`$TF output -json route53_nameservers` **now** — ACM validation will not finish until the
zone answers publicly. Expect 5-30 minutes.

## 5. Fill the secrets

The instance boots, reads blank secrets, and the API refuses to start
(`ENVIRONMENT=production` rejects a default `SECRET_KEY`). That is expected until this step.

```bash
$TF output -json app_secret_arn        # the target
cat > secrets.json <<'JSON'
{
  "SECRET_KEY": "<same value as Render>",
  "TOTP_ENCRYPTION_KEY": "<same as Render — changing it breaks every enrolled authenticator>",
  "CRON_SECRET": "<same as Render>",
  "EMAIL_LINK_SECRET": "<same as Render — old emailed links stop verifying otherwise>",
  "CHAT_HISTORY_SECRET": "<same as Render>",
  "BRIEFING_CRON_SECRET": "", "RESEND_WEBHOOK_SECRET": "",
  "FIRST_ADMIN_EMAIL": "<admin>", "FIRST_ADMIN_PASSWORD": "<admin password>",
  "ADMIN_EMAIL": "<notifications>", "EXTRA_ADMIN_EMAILS": "",
  "SMTP_HOST": "smtp.resend.com", "SMTP_USER": "resend", "SMTP_PASSWORD": "<resend key>",
  "ANTHROPIC_API_KEY": "", "PINECONE_API_KEY": "", "OPENROUTER_API_KEY": "",
  "GITHUB_TOKEN": "", "GITHUB_OWNER": "", "GITHUB_REPO": "",
  "GSC_SERVICE_ACCOUNT_JSON": "",
  "TWILIO_ACCOUNT_SID": "", "TWILIO_AUTH_TOKEN": "", "TWILIO_WHATSAPP_FROM": "",
  "GOOGLE_SHEET_ID": "", "GOOGLE_CREDENTIALS_JSON": "", "SERP_API_KEY": "", "KIE_API_KEY": ""
}
JSON
aws secretsmanager put-secret-value \
  --secret-id "$($TF output -raw app_secret_arn)" \
  --secret-string file://secrets.json
rm secrets.json
```

Every key in `app_secret_keys` must be present — an empty string is fine, a missing key is
not. Then replace the instance so it re-reads them:

```bash
aws autoscaling start-instance-refresh \
  --auto-scaling-group-name "$($TF output -raw app_asg_name)"
```

## 6. Watch it come up

```bash
aws logs tail "$($TF output -raw app_log_group)" --follow
```

Expect, in order: `database ready`, three `Running upgrade` lines (Alembic creating the
schema in RDS), the API start, then web and caddy. Certificate issuance appears in the
caddy stream. If it stalls, open a shell and read the boot log:

```bash
INSTANCE=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$($TF output -raw app_asg_name)" \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)
aws ssm start-session --target "$INSTANCE"
sudo tail -100 /var/log/user-data.log
```

## 7. Move the data and files

```bash
# Database: Render -> RDS. Schema already exists (prestart migrated it), so data only.
pg_dump "postgresql://<user>:<pw>@<render-external-host>/spdb?sslmode=require" \
  --no-owner --no-privileges --data-only --disable-triggers -Fc -f spdb.dump
# copy it to the instance (or run from anywhere that can reach RDS), then:
pg_restore --no-owner --no-privileges --data-only --disable-triggers \
  -d "$DATABASE_URL" spdb.dump

# Files: guide PDFs and infographics into the assets bucket
aws s3 sync ../../frontend/public/guides/pdf/ \
  "s3://$($TF output -raw assets_bucket)/guides/pdf/" --content-type application/pdf
```

Then set `GUIDE_PDF_BASE_URL` in `app_env` (tfvars) to
`$($TF output -raw asset_base_url)/guides/pdf`, re-apply, and refresh the instance. If any
infographic URLs in the database still point at R2, rewrite them to `asset_base_url`.

## 8. Verify before DNS moves

```bash
SITE=$($TF output -raw site_url)
curl -I "https://origin.<domain>/api/health"                       # the instance itself
curl -s "$SITE/api/proxy/api/v1/utils/health-check/db"             # all three tiers
curl -I "$($TF output -raw asset_base_url)/guides/pdf/dpdpa-guide-en.pdf"
```

In a browser: `/admin/login` (TOTP should accept your existing authenticator, because
`TOTP_ENCRYPTION_KEY` matched), one PDF generation, one form submission, one email.

## 9. Cut over

1. Copy Resend DKIM/SPF/DMARC, Search Console TXT and any MX records into the Route 53 zone.
2. Point the registrar at `route53_nameservers`.
3. Watch for an hour. Keep Render and Vercel **running** — DNS propagation is uneven.
4. Once traffic has moved, disable the Vercel project and the Render services so nothing
   sends email or runs a cron twice. Keep the Render database for a week of AWS backups.

## Rollback

DNS is the switch. Point the records back at Vercel and Render; both are still running, and
their database is untouched. Everything else can be destroyed with `terraform destroy`
(`db_deletion_protection = true` guards RDS — clear it deliberately).
