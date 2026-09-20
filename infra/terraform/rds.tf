resource "aws_db_subnet_group" "this" {
  name       = "${local.name}-db-subnets"
  subnet_ids = aws_subnet.private[*].id

  tags = { Name = "${local.name}-db-subnets" }
}

resource "aws_db_parameter_group" "postgres" {
  name   = "${local.name}-pg${var.db_engine_version}"
  family = "postgres${var.db_engine_version}"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "1000" # log queries slower than 1 s
  }

  tags = { Name = "${local.name}-pg-params" }
}

resource "aws_db_instance" "postgres" {
  identifier = "${local.name}-postgres"

  engine               = "postgres"
  engine_version       = var.db_engine_version
  instance_class       = var.db_instance_class
  parameter_group_name = aws_db_parameter_group.postgres.name

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db.result
  port     = 5432

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  multi_az               = var.db_multi_az

  backup_retention_period = var.db_backup_retention_days
  backup_window           = "20:00-21:00" # 01:30–02:30 IST
  maintenance_window      = "sun:21:00-sun:22:00"
  copy_tags_to_snapshot   = true

  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name}-postgres-final"

  auto_minor_version_upgrade      = true
  apply_immediately               = false
  performance_insights_enabled    = var.db_performance_insights
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  tags = { Name = "${local.name}-postgres" }
}
