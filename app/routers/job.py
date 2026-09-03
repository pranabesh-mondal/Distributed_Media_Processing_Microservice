"""Job endpoints: submit media processing jobs and poll their status."""

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from kombu.exceptions import OperationalError

from app.models.job import Job, JobCreateRequest, JobStatus
from app.services.celery_service import dispatch_task
from app.services.redis_service import RedisService
from app.services.s3_service import S3Error, S3Service
from app.utils.helpers import generate_object_key
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
    result_key = payload.result_key or generate_object_key("processed", "result.bin")

    redis_service.create_job(job_id)

    try:
        dispatch_task(
            process_media,
            args=[job_id, payload.source_key],
            kwargs={"result_key": result_key},
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
        source_key=payload.source_key,
        result_key=result_key,
    )


@router.get("/{job_id}", response_model=Job)
def get_job(request: Request, job_id: UUID) -> Job:
    """Return the current status (and error, if any) of a job."""
    redis_service: RedisService = request.app.state.redis_service

    status = redis_service.get_status(str(job_id))
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

    return Job(
        job_id=job_id,
        status=job_status,
        error=redis_service.get_error(str(job_id)),
    )