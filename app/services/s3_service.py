"""AWS S3 service client.

Provides Boto3-backed read/write access to an S3 bucket:

* Generate pre-signed URLs (for direct client uploads/downloads).
* Upload and download files and in-memory byte streams.
* Inspect object existence / metadata and delete objects.

The client is created lazily from application settings so that the service can
be instantiated before any credentials are present (e.g. during tests).
"""

import logging
from typing import BinaryIO, Optional

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class S3Error(Exception):
    """Base exception for S3 service failures."""


class S3Service:
    """Thin wrapper around a boto3 S3 client."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings: Settings = settings or get_settings()
        self._client = None

    # ---- Low-level client ------------------------------------------------

    @property
    def client(self):
        """Lazily construct and return the boto3 S3 client."""
        if self._client is None:
            client_kwargs = {
                "region_name": self.settings.AWS_REGION,
                "config": Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                    connect_timeout=5,
                    read_timeout=60,
                ),
            }
            if self.settings.s3_has_credentials:
                client_kwargs["aws_access_key_id"] = self.settings.AWS_ACCESS_KEY_ID
                client_kwargs["aws_secret_access_key"] = self.settings.AWS_SECRET_ACCESS_KEY
            if self.settings.S3_ENDPOINT_URL:
                client_kwargs["endpoint_url"] = self.settings.S3_ENDPOINT_URL

            self._client = boto3.client("s3", **client_kwargs)
        return self._client

    @property
    def bucket_name(self) -> str:
        return self.settings.S3_BUCKET_NAME

    # ---- Pre-signed URLs -------------------------------------------------

    def generate_presigned_upload_url(
        self,
        key: str,
        expires_in: Optional[int] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """Return a pre-signed URL the client can use to PUT an object to S3.

        Args:
            key: Object key (path) within the bucket.
            expires_in: URL validity in seconds; defaults to settings value.
            content_type: Optional MIME type to enforce on upload.

        Returns:
            Pre-signed upload URL as a string.

        Raises:
            S3Error: If URL generation fails.
        """
        params = {"Bucket": self.bucket_name, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        return self._generate_presigned_url("put_object", params, expires_in)

    def generate_presigned_download_url(
        self,
        key: str,
        expires_in: Optional[int] = None,
        response_content_disposition: Optional[str] = None,
    ) -> str:
        """Return a pre-signed URL for a client to GET an object from S3."""
        params = {"Bucket": self.bucket_name, "Key": key}
        if response_content_disposition:
            params["ResponseContentDisposition"] = response_content_disposition
        return self._generate_presigned_url("get_object", params, expires_in)

    def _generate_presigned_url(self, method: str, params: dict, expires_in: Optional[int]) -> str:
        try:
            return self.client.generate_presigned_url(
                ClientMethod=method,
                Params=params,
                ExpiresIn=expires_in or self.settings.S3_PRESIGNED_URL_EXPIRY,
            )
        except (ClientError, BotoCoreError, NoCredentialsError) as exc:
            logger.exception("Failed to generate pre-signed URL for method=%s", method)
            raise S3Error(f"Failed to generate pre-signed URL: {exc}") from exc

    # ---- Write (upload) ---------------------------------------------------

    def upload_file(
        self,
        local_path: str,
        key: str,
        extra_args: Optional[dict] = None,
        bucket: Optional[str] = None,
    ) -> str:
        """Upload a local file to S3 and return its key.

        `extra_args` may contain S3 upload options, e.g.
        ``{"ContentType": "image/jpeg", "ACL": "public-read"}``.
        """
        target_bucket = bucket or self.bucket_name
        try:
            self.client.upload_file(
                Filename=local_path,
                Bucket=target_bucket,
                Key=key,
                ExtraArgs=extra_args or {},
            )
        except (ClientError, BotoCoreError, NoCredentialsError) as exc:
            logger.exception("Failed to upload file %s to s3://%s/%s", local_path, target_bucket, key)
            raise S3Error(f"Failed to upload file to S3: {exc}") from exc
        logger.info("Uploaded file to s3://%s/%s", target_bucket, key)
        return key

    def upload_fileobj(
        self,
        data: BinaryIO,
        key: str,
        content_type: str,
        extra_args: Optional[dict] = None,
        bucket: Optional[str] = None,
    ) -> str:
        """Upload an in-memory file-like object (bytes stream) to S3."""
        target_bucket = bucket or self.bucket_name
        args = dict(extra_args or {})
        args.setdefault("ContentType", content_type)
        try:
            self.client.upload_fileobj(data, target_bucket, key, ExtraArgs=args)
        except (ClientError, BotoCoreError, NoCredentialsError) as exc:
            logger.exception("Failed to upload object to s3://%s/%s", target_bucket, key)
            raise S3Error(f"Failed to upload object to S3: {exc}") from exc
        logger.info("Uploaded object to s3://%s/%s", target_bucket, key)
        return key

    def put_bytes(
        self,
        data: bytes,
        key: str,
        content_type: str,
        bucket: Optional[str] = None,
    ) -> str:
        """Upload raw bytes to S3 as an object."""
        from io import BytesIO

        return self.upload_fileobj(BytesIO(data), key, content_type, bucket=bucket)

    # ---- Read (download) --------------------------------------------------

    def download_file(
        self,
        key: str,
        local_path: str,
        bucket: Optional[str] = None,
    ) -> str:
        """Download an S3 object to a local file and return the local path."""
        target_bucket = bucket or self.bucket_name
        try:
            self.client.download_file(target_bucket, key, local_path)
        except (ClientError, BotoCoreError, NoCredentialsError) as exc:
            logger.exception("Failed to download s3://%s/%s", target_bucket, key)
            raise S3Error(f"Failed to download object from S3: {exc}") from exc
        logger.info("Downloaded s3://%s/%s to %s", target_bucket, key, local_path)
        return local_path

    def get_object_bytes(self, key: str, bucket: Optional[str] = None) -> bytes:
        """Download an S3 object and return its contents as bytes."""
        target_bucket = bucket or self.bucket_name
        try:
            response = self.client.get_object(Bucket=target_bucket, Key=key)
            return response["Body"].read()
        except (ClientError, BotoCoreError, NoCredentialsError) as exc:
            logger.exception("Failed to read s3://%s/%s", target_bucket, key)
            raise S3Error(f"Failed to read object from S3: {exc}") from exc

    def open_stream(self, key: str, bucket: Optional[str] = None) -> BinaryIO:
        """Return a streaming body for an S3 object (lazy download)."""
        target_bucket = bucket or self.bucket_name
        try:
            response = self.client.get_object(Bucket=target_bucket, Key=key)
            return response["Body"]
        except (ClientError, BotoCoreError, NoCredentialsError) as exc:
            logger.exception("Failed to open stream s3://%s/%s", target_bucket, key)
            raise S3Error(f"Failed to open object stream from S3: {exc}") from exc

    # ---- Metadata / management ---------------------------------------------

    def file_exists(self, key: str, bucket: Optional[str] = None) -> bool:
        """Return True if an object exists at ``key`` in the bucket."""
        target_bucket = bucket or self.bucket_name
        try:
            self.client.head_object(Bucket=target_bucket, Key=key)
            return True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise S3Error(f"Failed to check object existence: {exc}") from exc
        except (BotoCoreError, NoCredentialsError) as exc:
            logger.exception("Failed to check s3://%s/%s", target_bucket, key)
            raise S3Error(f"Failed to check object existence: {exc}") from exc

    def delete_object(self, key: str, bucket: Optional[str] = None) -> None:
        """Delete an object from the bucket."""
        target_bucket = bucket or self.bucket_name
        try:
            self.client.delete_object(Bucket=target_bucket, Key=key)
        except (ClientError, BotoCoreError, NoCredentialsError) as exc:
            logger.exception("Failed to delete s3://%s/%s", target_bucket, key)
            raise S3Error(f"Failed to delete object from S3: {exc}") from exc
        logger.info("Deleted s3://%s/%s", target_bucket, key)

    def list_objects(self, prefix: str = "", bucket: Optional[str] = None) -> list[str]:
        """Return a list of object keys under an optional prefix."""
        target_bucket = bucket or self.bucket_name
        keys: list[str] = []
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=target_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
        except (ClientError, BotoCoreError, NoCredentialsError) as exc:
            logger.exception("Failed to list objects in s3://%s/%s", target_bucket, prefix)
            raise S3Error(f"Failed to list objects in S3: {exc}") from exc
        return keys
