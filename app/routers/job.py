"""Job endpoints: submit media processing jobs and poll their status."""

import json
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from kombu.exceptions import OperationalError

from app.models.job import Job, JobCreateRequest, JobStatus
from app.models.media import MediaType
from app.services.celery_service import dispatch_task
from app.services.redis_service import RedisService
from app.services.s3_service import S3Error, S3Service
from app.workers.celery_worker import process_media

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


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

    redis_service.create_job(job_id)
    # Persist what the worker should do so status polls can echo it back.
    redis_service.set_payload(
        job_id,
        json.dumps({"media_type": media_type, "operations": operations}),
    )

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

    return Job(
        job_id=job_id,
        status=job_status,
        media_type=media_type,
        operations=operations,
        result_key=result_key,
        thumbnail_keys=thumbnail_keys,
        metadata=metadata,
        error=redis_service.get_error(job_id_str),
    )