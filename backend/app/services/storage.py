"""File storage behind one interface: MinIO locally, S3/R2 in production (boto3).

Modules call `put`, `get_url`, `delete`, `exists`. Public URLs are built from
PUBLIC_ASSET_BASE_URL so the same key works behind a CDN later.
"""

import json
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings


@lru_cache(maxsize=1)
def _client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT or None,
        aws_access_key_id=settings.S3_ACCESS_KEY or None,
        aws_secret_access_key=settings.S3_SECRET_KEY or None,
        region_name=settings.S3_REGION,
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )


def put(key: str, data: bytes, content_type: str, *, cache_seconds: int = 31536000) -> str:
    """Upload (or replace) an object and return its public URL."""
    _client().put_object(
        Bucket=settings.S3_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
        CacheControl=f"public, max-age={cache_seconds}",
    )
    return get_url(key)


def get_url(key: str) -> str:
    return f"{settings.PUBLIC_ASSET_BASE_URL.rstrip('/')}/{key.lstrip('/')}"


def presigned_download_url(
    key: str, *, expires_seconds: int = 900, filename: str | None = None
) -> str:
    params: dict[str, Any] = {"Bucket": settings.S3_BUCKET, "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return str(
        _client().generate_presigned_url("get_object", Params=params, ExpiresIn=expires_seconds)
    )


def exists(key: str) -> bool:
    try:
        _client().head_object(Bucket=settings.S3_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def delete(key: str) -> None:
    _client().delete_object(Bucket=settings.S3_BUCKET, Key=key)


def get_bytes(key: str) -> bytes:
    body = _client().get_object(Bucket=settings.S3_BUCKET, Key=key)["Body"]
    return bytes(body.read())


def ensure_bucket(public_prefix: str = "infographics/") -> bool:
    """Create the bucket if missing and make `public_prefix` world-readable.

    Only for S3-compatible servers we run ourselves (local MinIO, S3_ENDPOINT set). On
    AWS the bucket and its policy come from Terraform, so this is a no-op there.
    Returns True when the bucket was created.
    """
    if not settings.S3_ENDPOINT:
        return False
    client = _client()
    created = False
    try:
        client.head_bucket(Bucket=settings.S3_BUCKET)
    except ClientError:
        client.create_bucket(Bucket=settings.S3_BUCKET)
        created = True
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PublicRead",
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{settings.S3_BUCKET}/{public_prefix}*"],
            }
        ],
    }
    client.put_bucket_policy(Bucket=settings.S3_BUCKET, Policy=json.dumps(policy))
    return created
