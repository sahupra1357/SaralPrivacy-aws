# ─── Task execution role (pull images, write logs, read secrets) — shared ────

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${local.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "task_execution_secrets" {
  statement {
    sid       = "ReadAppSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app.arn, aws_secretsmanager_secret.db.arn]
  }
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name   = "read-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution_secrets.json
}

# ─── ECS Exec (aws ecs execute-command) — attached to every task role ────────

data "aws_iam_policy_document" "ecs_exec" {
  statement {
    sid = "EcsExec"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
}

# ─── Frontend task role: no AWS access beyond ECS Exec ───────────────────────

resource "aws_iam_role" "frontend_task" {
  name               = "${local.name}-frontend-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "frontend_task" {
  name   = "ecs-exec"
  role   = aws_iam_role.frontend_task.id
  policy = data.aws_iam_policy_document.ecs_exec.json
}

# ─── Backend + worker task role: S3 assets bucket ────────────────────────────

resource "aws_iam_role" "backend_task" {
  name               = "${local.name}-backend-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "backend_task" {
  source_policy_documents = [data.aws_iam_policy_document.ecs_exec.json]

  statement {
    sid       = "AssetsBucketObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.assets.arn}/*"]
  }

  statement {
    sid       = "AssetsBucketList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.assets.arn]
  }
}

resource "aws_iam_role_policy" "backend_task" {
  name   = "app-permissions"
  role   = aws_iam_role.backend_task.id
  policy = data.aws_iam_policy_document.backend_task.json
}
