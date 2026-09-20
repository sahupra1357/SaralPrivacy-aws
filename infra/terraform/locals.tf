data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  name     = "${var.project_name}-${var.environment}"
  azs      = slice(data.aws_availability_zones.available.names, 0, var.az_count)
  site_url = "https://${var.domain_name}"

  # Tasks live in private subnets when a NAT gateway exists; otherwise in public
  # subnets with a public IP so they can still reach Anthropic, Pinecone, SMTP, etc.
  task_subnet_ids    = var.enable_nat_gateway ? aws_subnet.private[*].id : aws_subnet.public[*].id
  task_assign_public = !var.enable_nat_gateway

  # Internal DNS (Cloud Map) — the frontend reaches the backend at this address.
  service_namespace = "${var.project_name}.internal"
  backend_url       = "http://backend.${local.service_namespace}:${var.backend_port}"

  # Public URL of uploaded files (blog infographics) in the assets bucket.
  asset_base_url = "https://${aws_s3_bucket.assets.bucket_regional_domain_name}"

  # ── Frontend (Next.js) ──────────────────────────────────────────────────
  frontend_env = {
    NODE_ENV                = "production"
    PORT                    = tostring(var.container_port)
    HOSTNAME                = "0.0.0.0"
    NEXT_TELEMETRY_DISABLED = "1"
    NEXT_PUBLIC_SITE_URL    = local.site_url
    BACKEND_URL             = local.backend_url
    TZ                      = "Asia/Kolkata"
  }

  # ── Backend + worker (FastAPI image) ────────────────────────────────────
  backend_base_env = {
    ENVIRONMENT          = var.environment == "prod" ? "production" : "staging"
    FRONTEND_HOST        = local.site_url
    NEXT_PUBLIC_SITE_URL = local.site_url
    # Database: host/user/password/dbname come from the db secret (see ecs.tf).
    POSTGRES_PORT = "5432"
    # Storage: empty endpoint = real S3; empty keys = the task role's credentials.
    S3_ENDPOINT           = ""
    S3_ACCESS_KEY         = ""
    S3_SECRET_KEY         = ""
    S3_BUCKET             = aws_s3_bucket.assets.bucket
    S3_REGION             = var.aws_region
    PUBLIC_ASSET_BASE_URL = local.asset_base_url
    # Email: Resend SMTP over implicit TLS (host/user/password are secrets).
    SMTP_PORT      = "465"
    SMTP_SSL       = "true"
    SMTP_TLS       = "false"
    EMAILS_ENABLED = "true"
    TZ             = "Asia/Kolkata"
  }
  backend_env = merge(local.backend_base_env, var.app_env)

  # Database connection pieces, injected from the RDS secret JSON.
  db_secret_env = {
    POSTGRES_SERVER   = "host"
    POSTGRES_USER     = "username"
    POSTGRES_PASSWORD = "password"
    POSTGRES_DB       = "dbname"
  }
}
