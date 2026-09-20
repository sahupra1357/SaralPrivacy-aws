# ─── Identity ────────────────────────────────────────────────────────────────

variable "project_name" {
  description = "Short name used as a prefix for every resource."
  type        = string
  default     = "saralprivacy"
}

variable "environment" {
  description = "Deployment environment (prod, staging)."
  type        = string
  default     = "prod"
}

variable "aws_region" {
  description = "AWS region. Mumbai is closest to the Indian audience and to the existing Supabase project."
  type        = string
  default     = "ap-south-1"
}

# ─── DNS / TLS ───────────────────────────────────────────────────────────────

variable "domain_name" {
  description = "Apex domain served by the ALB. www.<domain> is redirected to it."
  type        = string
  default     = "saralprivacy.com"
}

variable "create_hosted_zone" {
  description = "Create a new Route 53 public hosted zone for domain_name. Set false and pass hosted_zone_id to reuse an existing zone."
  type        = bool
  default     = true
}

variable "hosted_zone_id" {
  description = "Existing Route 53 hosted zone id (only when create_hosted_zone = false)."
  type        = string
  default     = ""
}

# ─── Network ─────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to spread subnets across (ALB needs at least 2)."
  type        = number
  default     = 2
}

variable "enable_nat_gateway" {
  description = "Run ECS tasks in private subnets behind a NAT gateway (recommended). false = tasks get public IPs in public subnets and no NAT is created (saves ~USD 35/month)."
  type        = bool
  default     = true
}

variable "single_nat_gateway" {
  description = "One shared NAT gateway instead of one per AZ. Cheaper; a single point of failure for egress only."
  type        = bool
  default     = true
}

# ─── Container / ECS ─────────────────────────────────────────────────────────

variable "container_port" {
  type    = number
  default = 3000
}

variable "task_cpu" {
  description = "Frontend (Next.js) task CPU units (1024 = 1 vCPU)."
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Frontend task memory in MiB."
  type        = number
  default     = 1024
}

variable "cpu_architecture" {
  description = "X86_64 or ARM64 (Graviton, ~20% cheaper). The Docker image must be built for the same architecture."
  type        = string
  default     = "X86_64"
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "min_count" {
  type    = number
  default = 1
}

variable "max_count" {
  type    = number
  default = 4
}

variable "backend_port" {
  type    = number
  default = 8000
}

variable "backend_task_cpu" {
  description = "Backend (FastAPI) task CPU units. Playwright renders PDFs in-process, so keep at least 1 vCPU."
  type        = number
  default     = 1024
}

variable "backend_task_memory" {
  type    = number
  default = 2048
}

variable "backend_min_count" {
  type    = number
  default = 1
}

variable "backend_max_count" {
  type    = number
  default = 4
}

variable "worker_task_cpu" {
  description = "Worker (scheduled jobs) task CPU. The daily briefing renders an infographic with Chromium."
  type        = number
  default     = 1024
}

variable "worker_task_memory" {
  type    = number
  default = 2048
}

variable "image_tag" {
  description = "Image tag the task definition points at. CI pushes <sha> and re-tags 'latest', then forces a new deployment."
  type        = string
  default     = "latest"
}

variable "app_env" {
  description = "Extra non-secret environment variables for the BACKEND and WORKER containers (merged over the defaults in locals.tf), e.g. OUTREACH_DAILY_CAP."
  type        = map(string)
  default     = {}
}

variable "app_secret_keys" {
  description = "Keys injected from the app Secrets Manager JSON secret into the BACKEND and WORKER containers. Every key listed here MUST exist in the secret (empty string is fine) or the task fails to start."
  type        = list(string)
  default = [
    # Auth / signing
    "SECRET_KEY",
    "TOTP_ENCRYPTION_KEY",
    "CRON_SECRET",
    "BRIEFING_CRON_SECRET",
    "EMAIL_LINK_SECRET",
    "CHAT_HISTORY_SECRET",
    "RESEND_WEBHOOK_SECRET",
    # First admin + notification recipients
    "FIRST_ADMIN_EMAIL",
    "FIRST_ADMIN_PASSWORD",
    "ADMIN_EMAIL",
    "EXTRA_ADMIN_EMAILS",
    # Email (Resend SMTP: host smtp.resend.com, user "resend", password = API key)
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
    # External APIs
    "ANTHROPIC_API_KEY",
    "PINECONE_API_KEY",
    "OPENROUTER_API_KEY",
    "GITHUB_TOKEN",
    "GITHUB_OWNER",
    "GITHUB_REPO",
    "GSC_SERVICE_ACCOUNT_JSON",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_WHATSAPP_FROM",
    # Editorial pipeline
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDENTIALS_JSON",
    "SERP_API_KEY",
    "KIE_API_KEY",
  ]
}

variable "frontend_secret_keys" {
  description = "Keys from the same app secret that the FRONTEND container needs: SECRET_KEY (proxy.ts verifies the session JWT) and CRON_SECRET (/api/revalidate + start-up warm-up)."
  type        = list(string)
  default     = ["SECRET_KEY", "CRON_SECRET"]
}

variable "app_secrets" {
  description = "Optional seed values for the app secret JSON (key -> value). Leave empty to fill the secret in the console/CLI instead. Never commit a tfvars file containing this."
  type        = map(string)
  default     = {}
  sensitive   = true
}

# ─── Database (RDS PostgreSQL) ───────────────────────────────────────────────

variable "db_engine_version" {
  type    = string
  default = "16"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "Initial storage in GiB (gp3). Autoscales up to db_max_allocated_storage."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage" {
  type    = number
  default = 100
}

variable "db_name" {
  type    = string
  default = "saralprivacy"
}

variable "db_username" {
  type    = string
  default = "saral_admin"
}

variable "db_multi_az" {
  description = "Synchronous standby in a second AZ. Doubles the instance cost; turn on once the app is on RDS in production."
  type        = bool
  default     = false
}

variable "db_backup_retention_days" {
  type    = number
  default = 7
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}

variable "db_performance_insights" {
  type    = bool
  default = false
}

variable "db_admin_cidrs" {
  description = "Optional CIDRs (e.g. a bastion or VPN range) allowed to reach Postgres in addition to the ECS tasks."
  type        = list(string)
  default     = []
}

# ─── Cron (EventBridge Scheduler -> Lambda -> /api/cron/*) ───────────────────


# ─── Edge protection / observability ─────────────────────────────────────────

variable "enable_waf" {
  description = "Attach an AWS WAF web ACL (managed rule groups + per-IP rate limit) to the ALB."
  type        = bool
  default     = true
}

variable "waf_rate_limit" {
  description = "Requests per 5 minutes per IP before WAF starts blocking."
  type        = number
  default     = 2000
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "alarm_email" {
  description = "Email address subscribed to the alarm SNS topic. Empty disables the subscription (alarms still exist)."
  type        = string
  default     = ""
}

# ─── CI/CD ───────────────────────────────────────────────────────────────────

variable "github_repository" {
  description = "owner/repo allowed to assume the deploy role via GitHub OIDC."
  type        = string
  default     = "SahuDilip1356/SaralPrivacy"
}

variable "github_deploy_branch" {
  type    = string
  default = "main"
}

# ─── Optional assets bucket ──────────────────────────────────────────────────

variable "enable_cloudfront" {
  description = "Put a CloudFront distribution in front of the ALB. It supplies the visitor's city/country/region and real IP headers (CloudFront-Viewer-*), and the ALB is then reachable only from CloudFront."
  type        = bool
  default     = true
}
