"""Job-related request/response schemas.

Note: full job lifecycle tracking (Celery/RabbitMQ/Redis) is implemented in a
later stage. Only the base schema for representing a job is defined here.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.media import MediaType, OperationRequest


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(BaseModel):
    """Represents a media processing job."""

    job_id: UUID = Field(..., description="Unique job identifier.")
    status: JobStatus = Field(JobStatus.PENDING, description="Current job status.")
    media_type: Optional[MediaType] = Field(None, description="Type of media being processed.")
    operations: Optional[List[dict]] = Field(None, description="Operations requested for this job.")
    source_key: Optional[str] = Field(None, description="S3 key of the source object.")
    result_key: Optional[str] = Field(None, description="S3 key of the processed result.")
    result_url: Optional[str] = Field(None, description="CDN URL of the processed result (CloudFront).")
    thumbnail_keys: Optional[List[str]] = Field(None, description="S3 keys of generated thumbnails.")
    thumbnail_urls: Optional[List[str]] = Field(None, description="CDN URLs of generated thumbnails.")
    metadata: Optional[dict] = Field(None, description="Processing metadata (dimensions, duration, ...).")
    error: Optional[str] = Field(None, description="Error message if the job failed.")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Job creation time.",
    )
    updated_at: Optional[datetime] = Field(None, description="Last update time.")


class JobCreateRequest(BaseModel):
    """Payload for submitting a new media processing job.

    The client first uploads the raw file to S3 using a pre-signed URL
    (``GET /api/v1/upload-url``) and then submits the resulting object key here.
    """

    media_type: MediaType = Field(..., description="Type of media being processed.")
    source_key: str = Field(..., min_length=1, description="S3 key of the uploaded source object.")
    result_key: Optional[str] = Field(
        None,
        description="Optional S3 key for the processed result. Auto-generated when omitted.",
    )
    operations: List[OperationRequest] = Field(
        default_factory=list,
        description="Ordered processing operations (resize, compress, watermark, ...).",
    )
