# ─── Application secrets (one JSON secret, keys injected individually) ───────

resource "aws_secretsmanager_secret" "app" {
  name                    = "${local.name}/app"
  description             = "Runtime secrets for the ${local.name} web container (JSON: key -> value)"
  recovery_window_in_days = 7
}

# Seed only when values are supplied via var.app_secrets. When the map is
# empty an all-blank document is written so every key in app_secret_keys
# resolves and the task can start; fill real values with:
#   aws secretsmanager put-secret-value --secret-id <arn> --secret-string file://secrets.json
resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode(merge(
    { for k in var.app_secret_keys : k => "" },
    var.app_secrets,
  ))

  lifecycle {
    # Once seeded, values are maintained outside Terraform (console/CLI).
    # Remove this block if you prefer tfvars to be the source of truth.
    ignore_changes = [secret_string]
  }
}

# ─── Database credentials ────────────────────────────────────────────────────

resource "random_password" "db" {
  length           = 32
  special          = true
  override_special = "!#$%^&*()-_=+[]{}<>:?"
}

resource "aws_secretsmanager_secret" "db" {
  name                    = "${local.name}/db"
  description             = "RDS PostgreSQL master credentials and connection URL"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = var.db_username
    password = random_password.db.result
    host     = aws_db_instance.postgres.address
    port     = aws_db_instance.postgres.port
    dbname   = var.db_name
    # sslmode=require: the parameter group forces SSL.
    DATABASE_URL = "postgresql://${var.db_username}:${urlencode(random_password.db.result)}@${aws_db_instance.postgres.address}:${aws_db_instance.postgres.port}/${var.db_name}?sslmode=require"
  })
}
