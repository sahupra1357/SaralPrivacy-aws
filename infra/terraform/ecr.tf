# Two images: the Next.js frontend ("web") and the FastAPI backend ("api"), which the
# worker service also runs with a different command.
locals {
  ecr_repos = {
    web = "${local.name}-web"
    api = "${local.name}-api"
  }
}

resource "aws_ecr_repository" "this" {
  for_each = local.ecr_repos

  name                 = each.value
  image_tag_mutability = "MUTABLE" # 'latest' is re-pointed by CI on every deploy

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = { Name = each.value }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the last 15 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 15
        }
        action = { type = "expire" }
      }
    ]
  })
}
