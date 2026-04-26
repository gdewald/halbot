"""S3-compatible publisher (AWS S3 + Cloudflare R2 via custom endpoint).

R2 selection: set ``stats_s3_endpoint`` to
``https://<account>.r2.cloudflarestorage.com`` and ``stats_s3_region`` to
``auto``. AWS: leave endpoint empty.

Credentials (R2 access key + secret) come from
``HKLM\\SOFTWARE\\Halbot\\Secrets`` (DPAPI) under the names
``R2_ACCESS_KEY_ID`` / ``R2_SECRET_ACCESS_KEY`` (or ``AWS_*`` for AWS S3).
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Optional

from .. import config, secrets
from . import Publisher

log = logging.getLogger(__name__)


class S3PublisherError(RuntimeError):
    pass


def _need(name: str) -> str:
    val = (config.get(name) or "").strip()
    if not val:
        raise S3PublisherError(f"{name} not configured")
    return val


def _content_type(path: Path) -> str:
    mt, _ = mimetypes.guess_type(path.name)
    if mt:
        return mt
    return "application/octet-stream"


def _resolve_credentials() -> tuple[str, str]:
    access = secrets.get_secret("R2_ACCESS_KEY_ID") or secrets.get_secret("AWS_ACCESS_KEY_ID")
    secret = secrets.get_secret("R2_SECRET_ACCESS_KEY") or secrets.get_secret("AWS_SECRET_ACCESS_KEY")
    if not access or not secret:
        raise S3PublisherError(
            "missing R2/AWS credentials in HKLM\\SOFTWARE\\Halbot\\Secrets "
            "(set R2_ACCESS_KEY_ID + R2_SECRET_ACCESS_KEY)"
        )
    return access, secret


class S3Publisher(Publisher):
    def publish(self, local_dir: Path) -> str:
        import boto3  # imported lazily — daemon-only dep
        from botocore.config import Config as _BotoConfig

        bucket = _need("stats_s3_bucket")
        public_url = _need("stats_public_url")
        prefix = (config.get("stats_s3_key_prefix") or "").lstrip("/")
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        endpoint = (config.get("stats_s3_endpoint") or "").strip() or None
        region = (config.get("stats_s3_region") or "auto").strip() or "auto"
        access, secret = _resolve_credentials()

        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            # R2's S3 surface only supports SigV4 with empty/none-style content sha256
            # and a request_checksum_calculation of "when_required" (boto3 1.36 default
            # of "when_supported" makes it trip with InvalidArgument on small objects).
            config=_BotoConfig(
                signature_version="s3v4",
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

        files = sorted(p for p in local_dir.rglob("*") if p.is_file())
        if not files:
            raise S3PublisherError(f"empty staging dir {local_dir}")

        total_bytes = 0
        for fp in files:
            rel = fp.relative_to(local_dir).as_posix()
            key = f"{prefix}{rel}"
            body = fp.read_bytes()
            total_bytes += len(body)
            ct = _content_type(fp)
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType=ct,
                CacheControl="public, max-age=300",
            )

        log.info(
            "[stats_publisher] s3: uploaded %d files (%d bytes) to s3://%s/%s",
            len(files), total_bytes, bucket, prefix,
        )

        base = public_url if public_url.endswith("/") else public_url + "/"
        return f"{base}{prefix}index.html"
