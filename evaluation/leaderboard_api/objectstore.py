"""Thin S3/R2 access layer for the arena API.

Mirrors the client configuration qa-automation uses in `src/pii_qa/r2_client.py`:
an S3-compatible endpoint with static credentials and `region_name="auto"`,
because R2 offers neither STS nor object versioning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .config import Settings

log = logging.getLogger("arena.objectstore")


class StorageUnavailable(RuntimeError):
    """Raised when object storage is not configured or cannot be reached."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    etag: str
    last_modified: Any


@lru_cache(maxsize=4)
def _client(
    endpoint_url: str, access_key_id: str, secret_access_key: str, region: str
) -> Any:
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise StorageUnavailable("boto3 is not installed") from exc

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name=region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=20,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def client_for(settings: Settings) -> Any:
    if not settings.storage_configured:
        raise StorageUnavailable(
            "Object storage is not configured. Set ARENA_S3_ENDPOINT_URL, "
            "ARENA_S3_ACCESS_KEY_ID, and ARENA_S3_SECRET_ACCESS_KEY."
        )
    return _client(
        settings.endpoint_url,
        settings.access_key_id,
        settings.secret_access_key,
        settings.region,
    )


def list_objects(
    settings: Settings, bucket: str, prefix: str, limit: int | None = None
) -> list[StoredObject]:
    """List objects under a prefix, newest last. Never raises on an empty prefix."""
    s3 = client_for(settings)
    found: list[StoredObject] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Prefix": f"{prefix}/" if prefix else "",
            "MaxKeys": settings.poll_page_size,
        }
        if token:
            kwargs["ContinuationToken"] = token
        response = s3.list_objects_v2(**kwargs)
        for item in response.get("Contents", []):
            key = item["Key"]
            if key.endswith("/"):
                continue
            found.append(
                StoredObject(
                    key=key,
                    size=int(item.get("Size", 0)),
                    etag=str(item.get("ETag", "")).strip('"'),
                    last_modified=item.get("LastModified"),
                )
            )
            if limit is not None and len(found) >= limit:
                return found
        if not response.get("IsTruncated"):
            return found
        token = response.get("NextContinuationToken")
        if not token:
            return found


def get_bytes(settings: Settings, bucket: str, key: str) -> tuple[bytes, str]:
    """Return the object body and its stored content type."""
    s3 = client_for(settings)
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    return body, str(response.get("ContentType") or "application/octet-stream")


def head(settings: Settings, bucket: str, key: str) -> StoredObject | None:
    s3 = client_for(settings)
    try:
        response = s3.head_object(Bucket=bucket, Key=key)
    except Exception:
        return None
    return StoredObject(
        key=key,
        size=int(response.get("ContentLength", 0)),
        etag=str(response.get("ETag", "")).strip('"'),
        last_modified=response.get("LastModified"),
    )


def put_bytes(
    settings: Settings, bucket: str, key: str, body: bytes, content_type: str
) -> str:
    """Upload an object and return its ETag, which NiFi folds into the request ID."""
    s3 = client_for(settings)
    response = s3.put_object(
        Bucket=bucket, Key=key, Body=body, ContentType=content_type
    )
    return str(response.get("ETag", "")).strip('"')
