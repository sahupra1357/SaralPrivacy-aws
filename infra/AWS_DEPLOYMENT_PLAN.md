# SaralPrivacy on AWS — Deployment Plan (v2)

**Status:** Terraform in `infra/terraform` validates; nothing applied yet.
**Shape:** CloudFront → ALB → ECS Fargate (frontend, backend, worker) → RDS PostgreSQL 16, S3 for uploads, SMTP (Resend) for email.
**Region:** `ap-south-1` (Mumbai). CloudFront certificate in `us-east-1` (CloudFront requirement).
v1 of this plan (single Next.js container on Supabase) is in `_backup/infra_AWS_DEPLOYMENT_PLAN.v1.md`.

## 1. Architecture

```
Visitor ─▶ Route 53 (apex, www) ─▶ CloudFront ─▶ origin.<domain> ─▶ ALB :443 (WAF)
             adds CloudFront-Viewer-{Address,City,Country,Country-Region}      │
                                                                               ▼
                                        ECS service "web"  (Next.js, pages only, port 3000)
                                          /api/proxy/*  and rewrites for /api/briefings/*, /api/webhooks/resend
                                                                               │  http://backend.saralprivacy.internal:8000
                                                                               ▼  (Cloud Map DNS, backend SG allows only web SG)
                                        ECS service "api"  (FastAPI, Playwright PDFs, runs Alembic on start)
                                        ECS service "worker" (same image, `python -m app.jobs`, exactly 1 task)
                                               │                       │                    │
                                               ▼                       ▼                    ▼
                                     RDS PostgreSQL (private)   S3 assets bucket      Resend SMTP, Anthropic,
                                     only api/worker SG         infographics/* public  Pinecone, Google, SERP…
```

| Concern | How it is handled |
|---|---|
| Schema | `backend/scripts/prestart.sh` runs `alembic upgrade head` before the API starts; CI rolls the backend first |
| Scheduled jobs | Worker service, APScheduler, 5 jobs (UTC): editorial_daily_briefing 03:30, aeo-panel Mon 03:30, outreach-send 04:00, seo-inspect Mon 04:00, briefing-send 04:30. Postgres advisory lock per job |
| Visitor geo + IP | CloudFront origin request policy `AllViewerAndCloudFrontHeaders` → proxy forwards `cloudfront-viewer-*` → `deps.client_geo` / `deps.client_ip`. ALB ingress limited to CloudFront's origin-facing prefix list, so the headers cannot be forged |
| Uploads | `S3_ENDPOINT` empty = real S3 with the task role; objects under `infographics/` public-read |
| Secrets | One Secrets Manager JSON (`app_secret_keys`); frontend receives only `SECRET_KEY` + `CRON_SECRET`. DB credentials from the RDS secret |
| Stale pre-built pages | Frontend entrypoint `scripts/start-with-warmup.mjs` refreshes every page after start |
| External callers | Resend webhook `/api/webhooks/resend`, n8n `/api/briefings/today`, emailed `/api/briefings/approve` links keep their URLs (Next.js rewrites) |
| Long work | SEO inspect / AEO panel return 202 and run as background tasks; CloudFront origin timeout 60 s covers chat streams and PDFs |

## 2. Exact steps

**Phase 0 — prerequisites.** AWS CLI logged in to the target account, Terraform ≥ 1.10, Docker. Create the state bucket (see `terraform/backend.hcl.example`), copy it to `backend.hcl`, copy `terraform.tfvars.example` to `terraform.tfvars` and set `alarm_email` (and `hosted_zone_id` if the zone exists).

**Phase 1 — provision.**
```bash
cd infra/terraform
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan && terraform apply tfplan
```
Creates VPC, NAT, Route 53 records, two ACM certificates, ALB + WAF, CloudFront, ECR (web, api), ECS cluster with three services, Cloud Map namespace, RDS, S3, Secrets Manager, SNS alarms, GitHub OIDC role. Services start failing until images and secrets exist; the circuit breaker retries.

**Phase 2 — secrets.** Write every key in `app_secret_keys` to `secrets.json` (values from the old Vercel env plus the new ones: `SECRET_KEY`, `TOTP_ENCRYPTION_KEY`, SMTP), then:
```bash
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw app_secret_arn)" --secret-string file://secrets.json && rm secrets.json
```

**Phase 3 — images.** Set the GitHub repository variables listed at the top of `.github/workflows/deploy-aws.yml` from `terraform output`, then run the workflow (Actions → Deploy to AWS → Run workflow). It builds both images, rolls the backend (migrations), then worker and frontend, then smoke-tests.

**Phase 4 — data.** Load the exported data into RDS (see `docs/build/BUILD_PLAN.md` §6 and wave 3 notes): `pg_restore`/`psql` from a bastion or ECS Exec into the api task; copy infographics to `s3://<assets_bucket>/infographics/` and rewrite the stored URLs to `asset_base_url`.

**Phase 5 — DNS cutover.** Copy Resend DKIM/SPF/DMARC, Search Console TXT and MX records into the Route 53 zone first, then point the registrar at `route53_nameservers`. Disable the Vercel project and its crons so nothing sends twice.

## 3. Rough monthly cost (ap-south-1, light traffic)

| Item | USD/month |
|---|---|
| ALB | ~20 |
| Fargate: web 0.5 vCPU/1 GB, api 1/2, worker 1/2 | ~80 |
| NAT gateway + egress | ~35 |
| RDS db.t4g.micro + 20 GB | ~17 |
| CloudFront (low traffic), WAF | ~12 |
| Route 53, Secrets, CloudWatch, ECR, S3 | ~6 |
| **Total** | **≈ 170** (≈ 135 without NAT) |

## 4. Operations

```bash
aws logs tail /ecs/saralprivacy-prod/api --follow          # also /web and /worker
aws ecs execute-command --cluster saralprivacy-prod --task <id> --container api --interactive --command bash
# run a job now:           python -m app.jobs run briefing-send   (inside the worker or api task)
# migrate manually:        alembic upgrade head                   (inside the api task)
```
