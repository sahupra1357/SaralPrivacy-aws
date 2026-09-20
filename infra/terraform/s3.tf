# Uploaded files (blog infographics). Objects under infographics/ are public-read, the
# way the Supabase "infographics" bucket was; everything else stays private.
resource "aws_s3_bucket" "assets" {
  bucket = "${local.name}-assets-${data.aws_caller_identity.current.account_id}"
  tags   = { Name = "${local.name}-assets" }
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false # the bucket policy below grants read on one prefix
  restrict_public_buckets = false
}

resource "aws_s3_bucket_ownership_controls" "assets" {
  bucket = aws_s3_bucket.assets.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

data "aws_iam_policy_document" "assets_public_read" {
  statement {
    sid       = "PublicReadInfographics"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.assets.arn}/infographics/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

resource "aws_s3_bucket_policy" "assets" {
  bucket     = aws_s3_bucket.assets.id
  policy     = data.aws_iam_policy_document.assets_public_read.json
  depends_on = [aws_s3_bucket_public_access_block.assets]
}

resource "aws_s3_bucket_versioning" "assets" {
  bucket = aws_s3_bucket.assets.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "assets" {
  bucket = aws_s3_bucket.assets.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
