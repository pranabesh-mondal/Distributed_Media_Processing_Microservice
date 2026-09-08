"""General helper utilities."""

import re
import uuid
from pathlib import PurePosixPath
from typing import Optional


def generate_object_key(prefix: str, filename: str) -> str:
    """Generate a sanitized, unique S3 object key for an uploaded file.

    Args:
        prefix: Top-level folder for the object (e.g. ``uploads`` or ``processed``).
        filename: The original filename of the upload.

    Returns:
        An object key of the form ``prefix/<uuid><ext>``.
    """
    extension = PurePosixPath(filename).suffix.lower()
    safe_extension = re.sub(r"[^a-z0-9.]", "", extension)[:16]
    unique_name = f"{uuid.uuid4().hex}{safe_extension}"
    return str(PurePosixPath(prefix, unique_name))


def sanitize_filename(filename: str) -> str:
    """Remove characters that are unsafe or awkward in file paths."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")


def build_cdn_url(key: str, domain: str) -> Optional[str]:
    """Return the CloudFront CDN URL for an S3 key, or ``None`` if unconfigured.

    Args:
        key: S3 object key (path) within the bucket.
        domain: CloudFront distribution domain (empty string disables CDN).
    """
    if not domain:
        return None
    return f"https://{domain.strip('/')}/{key.lstrip('/')}"


def infer_content_type(filename: str) -> Optional[str]:
    """Best-effort MIME inference from a filename extension."""
    extension = PurePosixPath(filename).suffix.lower().lstrip(".")
    mapping = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "bmp": "image/bmp",
        "svg": "image/svg+xml",
        "mp4": "video/mp4",
        "mov": "video/quicktime",
        "avi": "video/x-msvideo",
        "webm": "video/webm",
        "mkv": "video/x-matroska",
    }
    return mapping.get(extension)
