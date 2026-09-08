"""Upload endpoints: pre-signed S3 upload URLs."""

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from app.models.media import MediaType, UploadUrlResponse
from app.services.metrics import UPLOAD_URLS_ISSUED
from app.services.s3_service import S3Error, S3Service
from app.utils.helpers import generate_object_key, infer_content_type

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["upload"])

# Top-level S3 prefixes per media type.
_MEDIA_PREFIXES = {
    MediaType.IMAGE: "uploads/images",
    MediaType.VIDEO: "uploads/videos",
}


@router.get("/upload-url", response_model=UploadUrlResponse)
def get_upload_url(
    request: Request,
    media_type: MediaType = Query(..., description="Type of media being uploaded."),
    filename: str = Query(..., min_length=1, description="Original filename."),
) -> UploadUrlResponse:
    """Generate a pre-signed S3 URL the client can PUT the file to directly."""
    s3: S3Service = request.app.state.s3_service

    prefix = _MEDIA_PREFIXES.get(media_type, "uploads")
    object_key = generate_object_key(prefix, filename)
    content_type = infer_content_type(filename)

    try:
        upload_url = s3.generate_presigned_upload_url(
            object_key, content_type=content_type
        )
    except S3Error as exc:
        logger.exception("Failed to generate upload URL for key=%s", object_key)
        raise HTTPException(
            status_code=503,
            detail=f"Failed to generate upload URL: {exc}",
        ) from exc

    logger.info("Issued upload URL key=%s media_type=%s", object_key, media_type)
    UPLOAD_URLS_ISSUED.labels(media_type=media_type.value).inc()
    return UploadUrlResponse(
        upload_url=upload_url,
        object_key=object_key,
        expires_in=s3.settings.S3_PRESIGNED_URL_EXPIRY,
        content_type=content_type,
    )