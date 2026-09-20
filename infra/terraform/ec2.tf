# ─────────────────────────────────────────────────────────────────────────────
# compute_mode = "ec2" — one instance running docker-compose, supervised by an
# Auto Scaling Group with min = max = 1 across every AZ.
#
# The ASG is a supervisor, not a scaler: when the health check fails it
# terminates the instance and launches a replacement in another AZ. The
# replacement claims the same Elastic IP on boot, so public DNS never changes
# and there is no TTL to wait out. Unattended recovery is ~3-6 minutes.
#
# State lives outside the box: Postgres in RDS, files in S3, secrets in Secrets
# Manager. The instance is disposable by design — never keep anything on it.
#
# Everything shared with the Fargate path (VPC, RDS, S3, ECR, secrets, DNS,
# CloudFront) is reused as-is; only the compute layer differs.
# ─────────────────────────────────────────────────────────────────────────────

# ─── Public address that survives a replacement ──────────────────────────────

resource "aws_eip" "app" {
  count = local.is_ec2 ? 1 : 0

  domain = "vpc"
  tags   = { Name = "${local.name}-app" }
}

# ─── Security group ──────────────────────────────────────────────────────────
# 80/443 from anywhere when the instance is the edge; CloudFront-only when it
# sits behind the distribution. No SSH — use SSM Session Manager.

resource "aws_security_group" "ec2" {
  count = local.is_ec2 ? 1 : 0

  name        = "${local.name}-ec2"
  description = "App instance: HTTP(S) in, everything out"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.name}-ec2" }
}

resource "aws_vpc_security_group_ingress_rule" "ec2_http" {
  for_each = local.is_ec2 ? toset(["80", "443"]) : toset([])

  security_group_id = aws_security_group.ec2[0].id
  description       = "HTTP(S) from the internet (or CloudFront when enabled)"
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "ec2_all" {
  count = local.is_ec2 ? 1 : 0

  security_group_id = aws_security_group.ec2[0].id
  description       = "Anthropic, Pinecone, SMTP, ECR, S3, Secrets Manager"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ─── Instance role ───────────────────────────────────────────────────────────

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  count = local.is_ec2 ? 1 : 0

  name               = "${local.name}-ec2"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# SSM Session Manager (shell access without SSH or a bastion) and the
# CloudWatch agent (memory metrics, which EC2 does not publish on its own).
resource "aws_iam_role_policy_attachment" "ec2_managed" {
  for_each = local.is_ec2 ? toset([
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
  ]) : toset([])

  role       = aws_iam_role.ec2[0].name
  policy_arn = each.value
}

data "aws_iam_policy_document" "ec2_inline" {
  count = local.is_ec2 ? 1 : 0

  # Pull both images.
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = [for r in aws_ecr_repository.this : r.arn]
  }
  # Read the app + db secrets at boot.
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app.arn, aws_secretsmanager_secret.db.arn]
  }
  # Uploads (S3_ENDPOINT empty = real S3 with these credentials).
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.assets.arn}/*"]
  }
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.assets.arn]
  }
  # Claim the Elastic IP after a replacement.
  statement {
    actions   = ["ec2:AssociateAddress", "ec2:DescribeAddresses"]
    resources = ["*"]
  }
  # Ship container logs.
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${one(aws_cloudwatch_log_group.ec2[*].arn)}:*"]
  }
}

resource "aws_iam_role_policy" "ec2" {
  count = local.is_ec2 ? 1 : 0

  name   = "${local.name}-ec2"
  role   = aws_iam_role.ec2[0].id
  policy = data.aws_iam_policy_document.ec2_inline[0].json
}

resource "aws_iam_instance_profile" "ec2" {
  count = local.is_ec2 ? 1 : 0

  name = "${local.name}-ec2"
  role = aws_iam_role.ec2[0].name
}

# ─── Logs ────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "ec2" {
  count = local.is_ec2 ? 1 : 0

  name              = "/ec2/${local.name}"
  retention_in_days = var.log_retention_days
}

# ─── Launch template ─────────────────────────────────────────────────────────

data "aws_ssm_parameter" "al2023" {
  count = local.is_ec2 ? 1 : 0

  # Amazon Linux 2023, architecture matched to the instance family.
  name = var.cpu_architecture == "ARM64" ? "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64" : "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_launch_template" "app" {
  count = local.is_ec2 ? 1 : 0

  name_prefix   = "${local.name}-"
  image_id      = data.aws_ssm_parameter.al2023[0].value
  instance_type = var.ec2_instance_type
  key_name      = var.ec2_key_name != "" ? var.ec2_key_name : null

  iam_instance_profile {
    arn = aws_iam_instance_profile.ec2[0].arn
  }

  network_interfaces {
    associate_public_ip_address = true
    security_groups             = [aws_security_group.ec2[0].id]
    delete_on_termination       = true
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.ec2_root_volume_gb
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  monitoring {
    enabled = var.ec2_detailed_monitoring
  }

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2 # containers reach the metadata service
  }

  user_data = base64encode(templatefile("${path.module}/templates/user_data.sh.tftpl", {
    aws_region     = var.aws_region
    eip_alloc_id   = aws_eip.app[0].id
    app_secret_arn = aws_secretsmanager_secret.app.arn
    db_secret_arn  = aws_secretsmanager_secret.db.arn
    log_group      = aws_cloudwatch_log_group.ec2[0].name
    web_image      = local.web_image
    api_image      = local.api_image
    container_port = var.container_port
    backend_port   = var.backend_port
    frontend_env   = local.frontend_env_ec2
    backend_env    = local.backend_env
    site_domain    = var.domain_name
    origin_domain  = "origin.${var.domain_name}"
    tls_domain     = var.enable_cloudfront ? "origin.${var.domain_name}" : var.domain_name
    acme_email     = var.alarm_email
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${local.name}-app" }
  }

  lifecycle {
    create_before_destroy = true

    # A Graviton instance needs an ARM64 AMI and ARM64 images. Catch the mismatch
    # at plan time rather than watching the instance fail to launch.
    precondition {
      condition     = local.ec2_is_graviton == (var.cpu_architecture == "ARM64")
      error_message = "ec2_instance_type ${var.ec2_instance_type} and cpu_architecture ${var.cpu_architecture} disagree: Graviton families (t4g/m7g/c7g/r7g/m6g/c6g/r6g/a1) require ARM64, everything else X86_64. The container images must match too."
    }
  }
}

# ─── Auto Scaling Group (min = max = 1: a supervisor, not a scaler) ──────────

resource "aws_autoscaling_group" "app" {
  count = local.is_ec2 ? 1 : 0

  name                      = "${local.name}-app"
  min_size                  = 1
  max_size                  = 1
  desired_capacity          = 1
  vpc_zone_identifier       = aws_subnet.public[*].id # needs the EIP, so public
  health_check_type         = "EC2"
  health_check_grace_period = var.ec2_health_check_grace
  default_instance_warmup   = var.ec2_health_check_grace

  launch_template {
    id      = aws_launch_template.app[0].id
    version = "$Latest"
  }

  # Replace, then terminate — the new instance is healthy before the old one goes.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 0 # only one instance exists; 0 allows the swap
      instance_warmup        = var.ec2_health_check_grace
    }
  }

  tag {
    key                 = "Name"
    value               = "${local.name}-app"
    propagate_at_launch = true
  }

  lifecycle {
    ignore_changes = [desired_capacity]
  }
}

# ─── Route 53 health check (alerting; the EIP handles the failover itself) ───

resource "aws_route53_health_check" "app" {
  count = local.is_ec2 && var.ec2_enable_health_check ? 1 : 0

  ip_address        = aws_eip.app[0].public_ip
  port              = 443
  type              = "HTTPS"
  resource_path     = var.ec2_health_check_path
  fqdn              = "origin.${var.domain_name}"
  request_interval  = 30
  failure_threshold = 2

  tags = { Name = "${local.name}-app" }
}

resource "aws_cloudwatch_metric_alarm" "ec2_unhealthy" {
  count = local.is_ec2 && var.ec2_enable_health_check ? 1 : 0

  alarm_name          = "${local.name}-app-unhealthy"
  alarm_description   = "Route 53 says the app is not answering. The ASG replaces the instance; this is the notification."
  namespace           = "AWS/Route53"
  metric_name         = "HealthCheckStatus"
  dimensions          = { HealthCheckId = aws_route53_health_check.app[0].id }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# ─── Instance alarms: what tells you to resize ───────────────────────────────

resource "aws_cloudwatch_metric_alarm" "ec2_status_check" {
  count = local.is_ec2 ? 1 : 0

  alarm_name          = "${local.name}-ec2-status-check"
  alarm_description   = "Instance or hypervisor status check failing"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { AutoScalingGroupName = aws_autoscaling_group.app[0].name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# Burstable families only: a credit balance trending to zero means sustained
# load above baseline — resize to a non-burstable instance rather than pay the
# unlimited-mode surcharge forever.
resource "aws_cloudwatch_metric_alarm" "ec2_cpu_credits" {
  count = local.is_ec2 && startswith(var.ec2_instance_type, "t") ? 1 : 0

  alarm_name          = "${local.name}-ec2-cpu-credits"
  alarm_description   = "CPU credit balance is low — the instance is running above its baseline"
  namespace           = "AWS/EC2"
  metric_name         = "CPUCreditBalance"
  dimensions          = { AutoScalingGroupName = aws_autoscaling_group.app[0].name }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.ec2_cpu_credit_threshold
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "ec2_memory" {
  count = local.is_ec2 ? 1 : 0

  alarm_name          = "${local.name}-ec2-memory"
  alarm_description   = "Memory above 80% — Chromium PDF renders are the usual cause"
  namespace           = "CWAgent"
  metric_name         = "mem_used_percent"
  dimensions          = { AutoScalingGroupName = aws_autoscaling_group.app[0].name }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching" # the agent may not be reporting yet
}
