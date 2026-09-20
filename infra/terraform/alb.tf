resource "aws_lb" "this" {
  name               = substr("${local.name}-alb", 0, 32)
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  # Cron routes run up to 300 s (maxDuration = 300); keep the connection open
  # longer than that so the ALB never returns a 504 mid-run.
  idle_timeout               = 330
  drop_invalid_header_fields = true
  enable_http2               = true
  enable_deletion_protection = var.environment == "prod"
  xff_header_processing_mode = "append"

  tags = { Name = "${local.name}-alb" }
}

resource "aws_lb_target_group" "app" {
  name                 = substr("${local.name}-tg", 0, 32)
  port                 = var.container_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.this.id
  deregistration_delay = 30

  health_check {
    path                = "/api/health"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${local.name}-tg" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.this.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

# www.<domain> → <domain> (301), keeps one canonical host for SEO.
resource "aws_lb_listener_rule" "www_redirect" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  action {
    type = "redirect"
    redirect {
      host        = var.domain_name
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  condition {
    host_header {
      values = ["www.${var.domain_name}"]
    }
  }
}
