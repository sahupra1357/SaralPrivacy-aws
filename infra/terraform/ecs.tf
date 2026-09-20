# Three services on one Fargate cluster:
#   frontend  Next.js, behind the ALB, calls the backend at local.backend_url
#   backend   FastAPI, reachable only from frontend tasks via Cloud Map DNS
#   worker    same image as backend, `python -m app.jobs` (APScheduler, 5 jobs)
# The backend runs `alembic upgrade head` on start (scripts/prestart.sh), so a deploy
# migrates the database before serving.

resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

# ─── Service discovery (backend.saralprivacy.internal) ───────────────────────

resource "aws_service_discovery_private_dns_namespace" "this" {
  name = local.service_namespace
  vpc  = aws_vpc.this.id
}

resource "aws_service_discovery_service" "backend" {
  name = "backend"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.this.id
    routing_policy = "MULTIVALUE"
    dns_records {
      type = "A"
      ttl  = 10
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

# ─── Logs ────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "svc" {
  for_each          = toset(["web", "api", "worker"])
  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
}

locals {
  app_secret_refs = [
    for k in var.app_secret_keys : { name = k, valueFrom = "${aws_secretsmanager_secret.app.arn}:${k}::" }
  ]
  db_secret_refs = [
    for env, key in local.db_secret_env : { name = env, valueFrom = "${aws_secretsmanager_secret.db.arn}:${key}::" }
  ]
  frontend_secret_refs = [
    for k in var.frontend_secret_keys : { name = k, valueFrom = "${aws_secretsmanager_secret.app.arn}:${k}::" }
  ]
  api_image = "${aws_ecr_repository.this["api"].repository_url}:${var.image_tag}"
  web_image = "${aws_ecr_repository.this["web"].repository_url}:${var.image_tag}"

  log_options = {
    for svc in ["web", "api", "worker"] : svc => {
      awslogs-group         = aws_cloudwatch_log_group.svc[svc].name
      awslogs-region        = var.aws_region
      awslogs-stream-prefix = svc
    }
  }
}

# ─── Frontend ────────────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${local.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.frontend_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name            = "web"
      image           = local.web_image
      essential       = true
      portMappings    = [{ containerPort = var.container_port, hostPort = var.container_port, protocol = "tcp" }]
      environment     = [for k, v in local.frontend_env : { name = k, value = v }]
      secrets         = local.frontend_secret_refs
      linuxParameters = { initProcessEnabled = true }
      healthCheck = {
        command     = ["CMD-SHELL", "node -e \"fetch('http://127.0.0.1:${var.container_port}/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
      logConfiguration = { logDriver = "awslogs", options = local.log_options["web"] }
    }
  ])
}

resource "aws_ecs_service" "frontend" {
  name            = "${local.name}-web"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.frontend.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  enable_execute_command            = true
  health_check_grace_period_seconds = 90
  propagate_tags                    = "SERVICE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = local.task_subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = local.task_assign_public
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "web"
    container_port   = var.container_port
  }

  lifecycle {
    ignore_changes = [desired_count] # autoscaling owns it
  }

  depends_on = [aws_lb_listener.https, aws_ecs_service.backend]
}

# ─── Backend (FastAPI) ───────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.backend_task_cpu
  memory                   = var.backend_task_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.backend_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name            = "api"
      image           = local.api_image
      essential       = true
      portMappings    = [{ containerPort = var.backend_port, hostPort = var.backend_port, protocol = "tcp" }]
      environment     = [for k, v in local.backend_env : { name = k, value = v }]
      secrets         = concat(local.app_secret_refs, local.db_secret_refs)
      linuxParameters = { initProcessEnabled = true } # Chromium child processes
      healthCheck = {
        command     = ["CMD-SHELL", "curl -fsS http://127.0.0.1:${var.backend_port}/api/v1/utils/health-check/ || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 90 # prestart waits for the db and runs migrations
      }
      logConfiguration = { logDriver = "awslogs", options = local.log_options["api"] }
    }
  ])
}

resource "aws_ecs_service" "backend" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = var.backend_min_count
  launch_type     = "FARGATE"

  enable_execute_command = true
  propagate_tags         = "SERVICE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = local.task_subnet_ids
    security_groups  = [aws_security_group.backend.id]
    assign_public_ip = local.task_assign_public
  }

  service_registries {
    registry_arn = aws_service_discovery_service.backend.arn
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_db_instance.postgres]
}

# ─── Worker (scheduled jobs) ─────────────────────────────────────────────────
# Exactly one task: APScheduler runs in-process. Each job also takes a Postgres
# advisory lock, so an overlapping deploy never double-runs a job.

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_task_cpu
  memory                   = var.worker_task_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.backend_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([
    {
      name             = "worker"
      image            = local.api_image
      essential        = true
      command          = ["python", "-m", "app.jobs"]
      environment      = [for k, v in local.backend_env : { name = k, value = v }]
      secrets          = concat(local.app_secret_refs, local.db_secret_refs)
      linuxParameters  = { initProcessEnabled = true }
      logConfiguration = { logDriver = "awslogs", options = local.log_options["worker"] }
    }
  ])
}

resource "aws_ecs_service" "worker" {
  name            = "${local.name}-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  enable_execute_command = true
  propagate_tags         = "SERVICE"

  # Stop the old task before starting the new one: one scheduler at a time.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = local.task_subnet_ids
    security_groups  = [aws_security_group.backend.id]
    assign_public_ip = local.task_assign_public
  }

  depends_on = [aws_ecs_service.backend]
}

# ─── Autoscaling (frontend + backend) ────────────────────────────────────────

locals {
  scaled = {
    web = { service = aws_ecs_service.frontend.name, min = var.min_count, max = var.max_count }
    api = { service = aws_ecs_service.backend.name, min = var.backend_min_count, max = var.backend_max_count }
  }
}

resource "aws_appautoscaling_target" "svc" {
  for_each           = local.scaled
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${each.value.service}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = each.value.min
  max_capacity       = each.value.max
}

resource "aws_appautoscaling_policy" "cpu" {
  for_each           = aws_appautoscaling_target.svc
  name               = "${local.name}-${each.key}-cpu-target"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = each.value.service_namespace
  resource_id        = each.value.resource_id
  scalable_dimension = each.value.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

resource "aws_appautoscaling_policy" "memory" {
  for_each           = aws_appautoscaling_target.svc
  name               = "${local.name}-${each.key}-memory-target"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = each.value.service_namespace
  resource_id        = each.value.resource_id
  scalable_dimension = each.value.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
    target_value       = 75
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
