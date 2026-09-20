output "route53_nameservers" {
  description = "Set these at your domain registrar (only when create_hosted_zone = true)."
  value       = var.create_hosted_zone ? aws_route53_zone.this[0].name_servers : null
}

output "hosted_zone_id" {
  value = local.zone_id
}

output "site_url" {
  value = local.site_url
}

output "alb_dns_name" {
  description = "The ALB behind CloudFront. With CloudFront on, it accepts only CloudFront traffic; smoke-test via the CloudFront domain instead."
  value       = aws_lb.this.dns_name
}

output "ecr_web_repository_url" {
  description = "Frontend image repository (GitHub variable ECR_WEB_REPOSITORY)."
  value       = aws_ecr_repository.this["web"].repository_url
}

output "ecr_api_repository_url" {
  description = "Backend/worker image repository (GitHub variable ECR_API_REPOSITORY)."
  value       = aws_ecr_repository.this["api"].repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "ecs_services" {
  description = "Service names (GitHub variables ECS_WEB_SERVICE, ECS_API_SERVICE, ECS_WORKER_SERVICE)."
  value = {
    web    = aws_ecs_service.frontend.name
    api    = aws_ecs_service.backend.name
    worker = aws_ecs_service.worker.name
  }
}

output "backend_internal_url" {
  description = "Where the frontend reaches the backend (baked into the frontend image as BACKEND_URL)."
  value       = local.backend_url
}

output "cloudfront_domain" {
  value = var.enable_cloudfront ? aws_cloudfront_distribution.this[0].domain_name : null
}

output "app_secret_arn" {
  description = "Secrets Manager secret holding the app's runtime secrets (JSON)."
  value       = aws_secretsmanager_secret.app.arn
}

output "db_secret_arn" {
  description = "Secrets Manager secret holding the RDS credentials and DATABASE_URL."
  value       = aws_secretsmanager_secret.db.arn
}

output "db_endpoint" {
  value = aws_db_instance.postgres.address
}

output "github_deploy_role_arn" {
  description = "Put this in the GitHub repo as the AWS_DEPLOY_ROLE_ARN secret/variable."
  value       = aws_iam_role.github_deploy.arn
}


output "assets_bucket" {
  value = aws_s3_bucket.assets.bucket
}

output "asset_base_url" {
  description = "Public base URL of uploaded files (frontend build arg PUBLIC_ASSET_BASE_URL)."
  value       = local.asset_base_url
}

output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}
