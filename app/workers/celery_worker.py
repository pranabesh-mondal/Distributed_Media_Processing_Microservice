"""Celery worker tasks with network-failure handling.

This module defines the media-processing pipeline that runs in Celery workers.
It focuses specifically on Week 2's *error handling for network failures*:

* Transient network failures (a temporarily unreachable broker, an S3 request
  that times out, a dropped Redis connection) are retried automatically with
  exponential backoff and jitter.
* Permanent failures are not retried; the job is marked ``failed`` in Redis
  with a stored error message so the API layer can surface it.

The actual media transforms (Pillow/FFmpeg) are Week 3 scope; here the
pipeline focuses on robust, network-safe job flow.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from app.config import get_settings
from app.models.job import JobStatus
from app.services.celery_service import NetworkAwareTask, celery_app
from app.services.redis_service import RedisService, RedisServiceError
from app.services.retries import TransientNetworkError
from app.services.s3_service import S3Error, S3Service

logger = logging.getLogger(__name__)
settings = get_settings()


class MediaProcessingTask(NetworkAwareTask):
    """Task base that records permanent failures in Redis.

    Once retries are exhausted the job is marked ``failed`` (with the error
    message) so the API can report a definitive outcome rather than leaving
    the job stuck as ``processing``.
    """

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # noqa: PLW0613
        super().on_failure(exc, task_id, args, kwargs, einfo)
        if args:
            job_id = args[0]
            try:
                redis = RedisService(settings=settings)
                redis.update_status(job_id, JobStatus.FAILED.value)
                redis.set_error(job_id, str(exc))
            except RedisServiceError:
                logger.exception("Could not record failure for job %s", job_id)


def run_job_pipeline(
    job_id: str,
    source_key: str,
    result_key: Optional[str] = None,
) -> dict:
    """Execute the job pipeline independent of Celery.

    Broker-free so it can be invoked from offline simulations and tests while
    still exercising the same network-robust code path as the worker task.

    Args:
        job_id: Unique job identifier stored in Redis.
        source_key: S3 key of the source object to process.
        result_key: (optional) S3 key for the processed result.

    Returns:
        A dict with ``job_id`` and ``result_key``.

    Raises:
        S3Error / TransientNetworkError: Propagated so the Celery task's
            autoretry can retry transient failures.
    """
    redis = RedisService(settings=settings)
    s3 = S3Service(settings=settings)

    # 1. Transition to "processing" (transient Redis failures retried by the
    #    worker's autoretry_for since RedisUnavailable is transient).
    redis.update_status(job_id, JobStatus.PROCESSING.value)
    logger.info("Job %s is now processing", job_id)

    # 2. Download the source object (network op). A temporary S3 outage raises
    #    S3Error, which is retried automatically by the worker task.
    data = s3.get_object_bytes(source_key)
    logger.info("Job %s downloaded %d bytes from %s", job_id, len(data), source_key)

    # 3. Placeholder for Week-3 media transforms (Pillow/FFmpeg).
    processed = data

    # 4. Upload the processed result (network op, retried automatically).
    final_result_key = result_key or f"processed/{uuid.uuid4().hex}.bin"
    s3.put_bytes(processed, final_result_key, content_type="application/octet-stream")
    logger.info("Job %s uploaded result to %s", job_id, final_result_key)

    # 5. Mark completed.
    redis.update_status(job_id, JobStatus.COMPLETED.value)
    logger.info("Job %s completed", job_id)

    return {"job_id": job_id, "result_key": final_result_key}


@celery_app.task(
    bind=True,
    base=MediaProcessingTask,
    name="workers.process_media",
    autoretry_for=(S3Error, TransientNetworkError),
)
def process_media(
    self,
    job_id: str,
    source_key: str,
    result_key: Optional[str] = None,
) -> dict:
    """Celery task that runs the media pipeline with automatic retry.

    Transient network failures are propagated and automatically retried by
    Celery (backoff configured on ``NetworkAwareTask``); when retries are
    exhausted ``MediaProcessingTask.on_failure`` marks the job as failed.
    """
    return run_job_pipeline(job_id, source_key, result_key=result_key)


def run_offline_simulation(
    job_id: Optional[str] = None,
    source_key: str = "uploads/demo.txt",
    result_key: Optional[str] = None,
) -> dict:
    """Execute the pipeline inline (no broker/worker needed).

    Use for local verification of the network-safe job flow before the
    RabbitMQ broker is available::

        python -m workers.celery_worker
    """
    job_id = job_id or str(uuid.uuid4())
    logger.info("Offline simulation for job %s", job_id)
    return run_job_pipeline(job_id=job_id, source_key=source_key, result_key=result_key)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    try:
        result = run_offline_simulation()
        logger.info("Simulation result: %s", result)
    except Exception as exc:
        logger.error("Simulation failed permanently: %s", exc)
        raise