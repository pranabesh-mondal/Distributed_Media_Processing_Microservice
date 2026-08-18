"""Job-related request/response schemas.

Note: full job lifecycle tracking (Celery/RabbitMQ/Redis) is implemented in a
later stage. Only the base schema for representing a job is defined here.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(BaseModel):
    """Represents a media processing job."""

    job_id: UUID = Field(..., description="Unique job identifier.")
    status: JobStatus = Field(JobStatus.PENDING, description="Current job status.")
    source_key: Optional[str] = Field(None, description="S3 key of the source object.")
    result_key: Optional[str] = Field(None, description="S3 key of the processed result.")
    error: Optional[str] = Field(None, description="Error message if the job failed.")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Job creation time.")
    updated_at: Optional[datetime] = Field(None, description="Last update time.")
