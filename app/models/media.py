"""Media-related request/response schemas."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class MediaOperation(str, Enum):
    RESIZE = "resize"
    COMPRESS = "compress"
    WATERMARK = "watermark"
    THUMBNAIL = "thumbnail"
    TRANSCODE = "transcode"


class UploadUrlResponse(BaseModel):
    """Response containing a pre-signed S3 upload URL."""

    upload_url: str = Field(..., description="Pre-signed URL for direct client upload.")
    object_key: str = Field(..., description="S3 object key of the uploaded file.")
    expires_in: int = Field(..., description="URL validity in seconds.")
    content_type: Optional[str] = Field(None, description="Expected MIME type.")


class OperationRequest(BaseModel):
    """A single processing operation with its parameters.

    See the README "Media Operations" section for accepted parameters per
    operation. Operations are executed in the order they are listed.
    """

    op: MediaOperation = Field(..., description="Operation to perform.")
    params: dict = Field(default_factory=dict, description="Operation-specific parameters.")
