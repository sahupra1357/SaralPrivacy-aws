# CloudFront in front of the ALB (var.enable_cloudfront).
#
# Why: it adds CloudFront-Viewer-City / -Country / -Country-Region / -Address headers,
# which the backend stores on leads, assessments, notices and chat handoffs (what
# Vercel's x-vercel-ip-* headers did) and uses as the real client IP for rate limits.
# The ALB security group then accepts only CloudFront's origin-facing ranges.
#
# Caching: off for pages and API (Next.js ISR and the backend decide freshness), on for
# /_next/static/* whose file names are content hashes.

resource "aws_acm_certificate" "cloudfront" {
  count    = var.enable_cloudfront ? 1 : 0
  provider = aws.us_east_1

  domain_name               = var.domain_name
  subject_alternative_names = ["www.${var.domain_name}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${local.name}-cloudfront-cert" }
}

# Same names as the regional certificate, so ACM issues identical validation CNAMEs;
# allow_overwrite lets both certificates share the records.
resource "aws_route53_record" "cloudfront_cert_validation" {
  for_each = var.enable_cloudfront ? {
    for dvo in aws_acm_certificate.cloudfront[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id         = local.zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 60
  records         = [each.value.record]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "cloudfront" {
  count                   = var.enable_cloudfront ? 1 : 0
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.cloudfront[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cloudfront_cert_validation : r.fqdn]
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

# Forwards every viewer header (Host, cookies, Authorization, svix-*) plus the
# CloudFront-Viewer-* geo/address headers to the origin.
data "aws_cloudfront_origin_request_policy" "all_viewer_and_cf" {
  name = "Managed-AllViewerAndCloudFrontHeaders-2022-06"
}

resource "aws_cloudfront_distribution" "this" {
  count = var.enable_cloudfront ? 1 : 0

  enabled         = true
  is_ipv6_enabled = true
  comment         = "${local.name} — site + API via Next.js"
  aliases         = [var.domain_name, "www.${var.domain_name}"]
  price_class     = "PriceClass_200" # includes India edge locations
  http_version    = "http2and3"

  origin {
    origin_id   = "alb"
    domain_name = "origin.${var.domain_name}"

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "https-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60 # the longest synchronous routes (chat stream, PDF) fit in 60 s
      origin_keepalive_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = true
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_and_cf.id
  }

  ordered_cache_behavior {
    path_pattern           = "/_next/static/*"
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.cloudfront[0].certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  tags = { Name = "${local.name}-cdn" }
}
