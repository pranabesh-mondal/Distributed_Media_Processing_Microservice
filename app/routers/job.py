"""Job endpoints: submit media processing jobs and poll their status."""

import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from kombu.exceptions import OperationalError

from app.config import get_settings
from app.models.job import Job, JobCreateRequest, JobStatus
from app.models.media import MediaType
from app.services.celery_service import dispatch_task
from app.services.metrics import JOBS_FAILED, JOBS_SUBMITTED
from app.services.redis_service import RedisService
from app.services.s3_service import S3Error, S3Service
from app.utils.helpers import build_cdn_url
from app.workers.celery_worker import process_media

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

settings = get_settings()


@router.post("", response_model=Job, status_code=202)
def create_job(request: Request, payload: JobCreateRequest) -> Job:
    """Register a job in Redis and enqueue it for processing by a worker.

    Returns ``202 Accepted`` with the job resource; poll
    ``GET /api/v1/jobs/{job_id}`` for progress.
    """
    redis_service: RedisService = request.app.state.redis_service
    s3: S3Service = request.app.state.s3_service

    # Fail fast if the client never uploaded (or mistyped) the source key.
    try:
        object_exists = s3.file_exists(payload.source_key)
    except S3Error as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not verify source object: {exc}",
        ) from exc
    if not object_exists:
        raise HTTPException(
            status_code=404,
            detail=f"Source object '{payload.source_key}' does not exist in the bucket.",
        )

    job_id = str(uuid4())
    media_type = payload.media_type.value
    operations = [op.model_dump() for op in payload.operations]

    # Persist what the worker should do so status polls can echo it back.
    payload_json = json.dumps({"media_type": media_type, "operations": operations})
    # Status, payload and timestamps are written atomically (single pipeline).
    redis_service.create_job(job_id, payload_json=payload_json)
    JOBS_SUBMITTED.labels(media_type=media_type).inc()

    try:
        dispatch_task(
            process_media,
            args=[job_id, payload.source_key, media_type],
            kwargs={"operations": operations, "result_key": payload.result_key},
        )
    except (OperationalError, S3Error) as exc:
        # The job was registered but never reached the queue: mark it failed so
        # clients are not left polling a job that will never run.
        logger.error("Could not dispatch job %s: %s", job_id, exc)
        redis_service.update_status(job_id, JobStatus.FAILED.value)
        redis_service.set_error(job_id, f"Broker unavailable: {exc}")
        JOBS_FAILED.labels(media_type=media_type, reason="dispatch_error").inc()
        raise HTTPException(
            status_code=503,
            detail="Job registered but the task queue is unavailable; try again later.",
        ) from exc

    return Job(
        job_id=UUID(job_id),
        status=JobStatus.PENDING,
        media_type=payload.media_type,
        operations=operations,
        source_key=payload.source_key,
        result_key=payload.result_key,
    )


@router.get("/{job_id}", response_model=Job)
def get_job(request: Request, job_id: UUID) -> Job:
    """Return the current status, requested operations and result of a job."""
    redis_service: RedisService = request.app.state.redis_service
    job_id_str = str(job_id)

    status = redis_service.get_status(job_id_str)
    if status is None:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' not found.",
        )

    try:
        job_status = JobStatus(status)
    except ValueError:
        logger.warning("Unknown status '%s' for job %s", status, job_id)
        job_status = JobStatus.PENDING

    # Echo the stored payload (media type + operations) back to the client.
    media_type = None
    operations = None
    payload_raw = redis_service.get_payload(job_id_str)
    if payload_raw:
        try:
            payload = json.loads(payload_raw)
            media_type = MediaType(payload.get("media_type"))
            operations = payload.get("operations")
        except (ValueError, TypeError):
            logger.warning("Invalid payload stored for job %s", job_id)

    # Include the worker's outcome (result key, thumbnails, metadata) when done.
    result_key = None
    thumbnail_keys = None
    metadata = None
    result_raw = redis_service.get_result(job_id_str)
    if result_raw:
        try:
            result = json.loads(result_raw)
            result_key = result.get("result_key")
            thumbnail_keys = result.get("thumbnail_keys")
            metadata = result.get("metadata")
        except ValueError:
            logger.warning("Invalid result stored for job %s", job_id)

    # Public CDN URLs when a CloudFront distribution is configured. Prefer the
    # URLs persisted by the worker; fall back to deriving them from the keys.
    try:
        stored = json.loads(result_raw) if result_raw else {}
    except ValueError:
        stored = {}
    result_url = stored.get("result_url")
    thumbnail_urls = stored.get("thumbnail_urls") or None
    if result_key and not result_url:
        result_url = build_cdn_url(result_key, settings.CLOUDFRONT_DOMAIN)
    if thumbnail_keys and not thumbnail_urls:
        thumbnail_urls = [
            build_cdn_url(key, settings.CLOUDFRONT_DOMAIN)
            for key in thumbnail_keys
        ]

    # Restore persisted timestamps (stored as ISO strings in Redis).
    created_at = None
    updated_at = None
    try:
        created_raw = redis_service.get_created_at(job_id_str)
        updated_raw = redis_service.get_updated_at(job_id_str)
        if created_raw:
            created_at = datetime.fromisoformat(created_raw)
        if updated_raw:
            updated_at = datetime.fromisoformat(updated_raw)
    except ValueError:
        logger.warning("Invalid timestamps stored for job %s", job_id)

    return Job(
        job_id=job_id,
        status=job_status,
        media_type=media_type,
        operations=operations,
        result_key=result_key,
        result_url=result_url,
        thumbnail_keys=thumbnail_keys,
        thumbnail_urls=thumbnail_urls,
        metadata=metadata,
        error=redis_service.get_error(job_id_str),
        created_at=created_at or datetime.now(timezone.utc),
        updated_at=updated_at,
    )


@router.get("/{job_id}/download-url")
def get_download_url(request: Request, job_id: UUID) -> dict:
    """Return a pre-signed S3 download URL for a completed job's result."""
    redis_service: RedisService = request.app.state.redis_service
    s3: S3Service = request.app.state.s3_service
    job_id_str = str(job_id)

    status = redis_service.get_status(job_id_str)
    if status is None:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' not found.",
        )
    if status != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Job '{job_id}' is not completed yet (status: {status}).",
        )

    result_raw = redis_service.get_result(job_id_str)
    if not result_raw:
        raise HTTPException(
            status_code=404,
            detail=f"No result stored for job '{job_id}'.",
        )
    try:
        result_key = json.loads(result_raw).get("result_key")
    except ValueError:
        result_key = None
    if not result_key:
        raise HTTPException(
            status_code=404,
            detail=f"No result key stored for job '{job_id}'.",
        )

    try:
        url = s3.generate_presigned_download_url(result_key)
    except S3Error as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to generate download URL: {exc}",
        ) from exc
    return {
        "job_id": job_id_str,
        "result_key": result_key,
        "download_url": url,
        "expires_in": s3.settings.S3_PRESIGNED_URL_EXPIRY,
    }