# ─── Hosted zone ─────────────────────────────────────────────────────────────

resource "aws_route53_zone" "this" {
  count = var.create_hosted_zone ? 1 : 0

  name    = var.domain_name
  comment = "${local.name} — managed by Terraform"
}

data "aws_route53_zone" "existing" {
  count = var.create_hosted_zone ? 0 : 1

  zone_id = var.hosted_zone_id
}

locals {
  zone_id = var.create_hosted_zone ? aws_route53_zone.this[0].zone_id : data.aws_route53_zone.existing[0].zone_id
}

# ─── ACM certificate (apex + www), DNS-validated ─────────────────────────────

resource "aws_acm_certificate" "this" {
  domain_name = var.domain_name
  # origin.<domain> is the name CloudFront uses to reach the ALB.
  subject_alternative_names = ["www.${var.domain_name}", "origin.${var.domain_name}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${local.name}-cert" }
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.this.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id         = local.zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 60
  records         = [each.value.record]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# ─── Records ─────────────────────────────────────────────────────────────────
# apex + www → CloudFront when enabled (else straight to the ALB);
# origin.<domain> → ALB always (CloudFront's origin).

locals {
  edge_alias = var.enable_cloudfront ? {
    name    = aws_cloudfront_distribution.this[0].domain_name
    zone_id = aws_cloudfront_distribution.this[0].hosted_zone_id
    } : {
    name    = aws_lb.this.dns_name
    zone_id = aws_lb.this.zone_id
  }
}

resource "aws_route53_record" "apex" {
  zone_id = local.zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = local.edge_alias.name
    zone_id                = local.edge_alias.zone_id
    evaluate_target_health = !var.enable_cloudfront
  }
}

resource "aws_route53_record" "www" {
  zone_id = local.zone_id
  name    = "www.${var.domain_name}"
  type    = "A"

  alias {
    name                   = local.edge_alias.name
    zone_id                = local.edge_alias.zone_id
    evaluate_target_health = !var.enable_cloudfront
  }
}

resource "aws_route53_record" "origin" {
  zone_id = local.zone_id
  name    = "origin.${var.domain_name}"
  type    = "A"

  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = true
  }
}
