"""Celery worker tasks implementing the full media-processing pipeline.

This module defines the media-processing pipeline that runs in Celery workers:

* Transient network failures (a temporarily unreachable broker, an S3 request
  that times out, a dropped Redis connection) are retried automatically with
  exponential backoff and jitter.
* Permanent failures (corrupt media, invalid operations, missing objects) are
  not retried; the job is marked ``failed`` in Redis with a stored error
  message so the API layer can surface it.

Media transforms (Pillow for images, FFmpeg for videos) live in
``app.services.processing``; this module wires them into a robust,
network-safe job flow.
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Optional

from app.config import get_settings
from app.models.job import JobStatus
from app.models.media import MediaType
from app.services.celery_service import NetworkAwareTask, celery_app
from app.services.metrics import (
    JOBS_COMPLETED,
    JOBS_FAILED,
    JOBS_IN_PROGRESS,
    JOB_END_TO_END_SECONDS,
    JOB_PROCESSING_SECONDS,
    S3_OPERATIONS,
)
from app.services.processing import (
    FORMAT_EXTENSIONS,
    UnsupportedMediaError,
    image_processor,
    video_processor,
)
from app.services.redis_service import RedisService, RedisServiceError
from app.services.retries import TransientNetworkError
from app.services.s3_service import S3Error, S3Service
from app.utils.helpers import build_cdn_url, generate_object_key, infer_content_type

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
                media_type = "unknown"
                if len(args) > 2:
                    media_type = str(args[2])
                JOBS_FAILED.labels(
                    media_type=media_type, reason="retries_exhausted"
                ).inc()
            except RedisServiceError:
                logger.exception("Could not record failure for job %s", job_id)


def _record_s3_outcome(operation: str, outcome: str) -> None:
    """Increment the S3 operation counter (best-effort; never raises)."""
    try:
        S3_OPERATIONS.labels(operation=operation, outcome=outcome).inc()
    except Exception:  # pragma: no cover - metrics must never break the job
        pass


def run_job_pipeline(
    job_id: str,
    source_key: str,
    media_type: str = "image",
    operations: Optional[list] = None,
    result_key: Optional[str] = None,
    *,
    redis_service: Optional[RedisService] = None,
    s3_service: Optional[S3Service] = None,
) -> dict:
    """Execute the full media pipeline independent of Celery.

    Broker-free so it can be invoked from offline simulations and tests while
    still exercising the same network-robust code path as the worker task.
    Services can be injected for testing (defaults to real S3/Redis clients).

    Args:
        job_id: Unique job identifier stored in Redis.
        source_key: S3 key of the source object to process.
        media_type: ``"image"`` or ``"video"`` (routes to the right processor).
        operations: Ordered list of operations (dicts with ``op``/``params``).
        result_key: (optional) S3 key for the processed result. Auto-generated
            with an extension matching the output format when omitted.

    Returns:
        A dict with ``job_id``, ``result_key`` and ``thumbnail_keys``.

    Raises:
        S3Error / TransientNetworkError: Propagated so the Celery task's
            autoretry can retry transient failures.
        UnsupportedMediaError / ImageProcessingError / VideoProcessingError:
            Permanent failures — never retried.
    """
    redis = redis_service or RedisService(settings=settings)
    s3 = s3_service or S3Service(settings=settings)
    started = time.monotonic()
    JOBS_IN_PROGRESS.labels(media_type=media_type).inc()

    # 1. Transition to "processing" (transient Redis failures retried by the
    #    worker's autoretry_for since RedisUnavailable is transient).
    redis.update_status(job_id, JobStatus.PROCESSING.value)
    logger.info("Job %s is now processing", job_id)

    try:
        result = _run_job_pipeline_inner(
            job_id, source_key, media_type, operations, result_key,
            redis=redis, s3=s3,
        )
        elapsed = time.monotonic() - started
        JOB_PROCESSING_SECONDS.labels(media_type=media_type).observe(elapsed)
        JOBS_COMPLETED.labels(media_type=media_type).inc()
        _observe_end_to_end(job_id, media_type, redis)
        return result
    except Exception as exc:
        # Permanent failures are counted here; transient ones (S3/Redis/broker)
        # are re-raised and counted only when retries are exhausted, in
        # MediaProcessingTask.on_failure below.
        if not isinstance(
            exc, (S3Error, TransientNetworkError, RedisServiceError)
        ):
            JOBS_FAILED.labels(
                media_type=media_type, reason=type(exc).__name__
            ).inc()
        raise
    finally:
        JOBS_IN_PROGRESS.labels(media_type=media_type).dec()


def _observe_end_to_end(job_id: str, media_type: str, redis: RedisService) -> None:
    """Record submission->completion latency using the stored created_at."""
    try:
        created_raw = redis.get_created_at(job_id)
        if created_raw:
            created = datetime.fromisoformat(created_raw)
            JOB_END_TO_END_SECONDS.labels(media_type=media_type).observe(
                max(0.0, (datetime.now(timezone.utc) - created).total_seconds())
            )
    except (ValueError, TypeError, OSError):
        pass


def _run_job_pipeline_inner(
    job_id: str,
    source_key: str,
    media_type: str,
    operations: Optional[list],
    result_key: Optional[str],
    *,
    redis: RedisService,
    s3: S3Service,
) -> dict:
    """Run the download/process/upload stages (called by run_job_pipeline)."""

    with tempfile.TemporaryDirectory(prefix="media_job_") as tmp_dir:
        # 2. Download the source object to a temp file (network op; a temporary
        #    S3 outage raises S3Error, retried automatically by the task).
        source_suffix = PurePosixPath(source_key).suffix or ".bin"
        input_path = f"{tmp_dir}/source{source_suffix}"
        s3.download_file(source_key, input_path)
        _record_s3_outcome("download", "success")
        logger.info("Job %s downloaded source %s", job_id, source_key)

        # 3. Run the media transforms (Pillow / FFmpeg). The processors write
        #    the output next to ``output_base`` with a format-matching extension.
        output_base = f"{tmp_dir}/output"
        if media_type == MediaType.IMAGE.value:
            meta = image_processor.process_image(input_path, output_base, operations)
        elif media_type == MediaType.VIDEO.value:
            meta = video_processor.process_video(input_path, output_base, operations)
        else:
            raise UnsupportedMediaError(
                f"Unsupported media type '{media_type}' (expected 'image' or 'video')."
            )

        result_path = meta["output_path"]
        extension = FORMAT_EXTENSIONS.get(meta.get("format", ""), "bin")
        final_result_key = result_key or generate_object_key("processed", f"result.{extension}")

        # 4. Upload the processed result (network op, retried automatically).
        content_type = infer_content_type(PurePosixPath(result_path).name)
        s3.upload_file(
            result_path,
            final_result_key,
            extra_args={"ContentType": content_type or "application/octet-stream"},
        )
        _record_s3_outcome("upload", "success")
        logger.info("Job %s uploaded result to %s", job_id, final_result_key)

        # 5. Upload any generated thumbnails (video jobs).
        thumbnail_keys = []
        for index, thumb_path in enumerate(meta.get("thumbnails", []), start=1):
            thumb_key = generate_object_key("processed/thumbnails", f"thumb_{index}.jpg")
            s3.upload_file(thumb_path, thumb_key, extra_args={"ContentType": "image/jpeg"})
            _record_s3_outcome("upload", "success")
            thumbnail_keys.append(thumb_key)
            logger.info("Job %s uploaded thumbnail to %s", job_id, thumb_key)

    # 6. Persist the outcome and mark completed so status polls can return it.
    metadata = {
        key: meta[key]
        for key in ("format", "width", "height", "duration", "size_bytes")
        if key in meta
    }
    result_url = build_cdn_url(final_result_key, settings.CLOUDFRONT_DOMAIN)
    thumbnail_urls = [
        build_cdn_url(key, settings.CLOUDFRONT_DOMAIN) for key in thumbnail_keys
    ]
    redis.set_result(
        job_id,
        json.dumps({
            "result_key": final_result_key,
            "result_url": result_url,
            "thumbnail_keys": thumbnail_keys,
            "thumbnail_urls": [url for url in thumbnail_urls if url],
            "metadata": metadata,
        }),
    )
    redis.update_status(job_id, JobStatus.COMPLETED.value)
    logger.info("Job %s completed", job_id)

    return {"job_id": job_id, "result_key": final_result_key, "thumbnail_keys": thumbnail_keys}


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
    media_type: str = "image",
    operations: Optional[list] = None,
    result_key: Optional[str] = None,
) -> dict:
    """Celery task that runs the full media pipeline with automatic retry.

    Transient network failures are propagated and automatically retried by
    Celery (backoff configured on ``NetworkAwareTask``); when retries are
    exhausted ``MediaProcessingTask.on_failure`` marks the job as failed.
    Permanent processing errors (corrupt media, invalid operations) fail
    immediately.
    """
    return run_job_pipeline(
        job_id,
        source_key,
        media_type=media_type,
        operations=operations,
        result_key=result_key,
    )


def run_offline_simulation(
    job_id: Optional[str] = None,
    source_key: str = "uploads/demo.png",
    media_type: str = "image",
    operations: Optional[list] = None,
    result_key: Optional[str] = None,
) -> dict:
    """Execute the pipeline inline (no broker/worker needed).

    Requires real S3/Redis access. The pipeline itself is broker-free, so it
    can also be exercised directly in tests by injecting fake services into
    :func:`run_job_pipeline`.
    """
    job_id = job_id or str(uuid.uuid4())
    logger.info("Offline simulation for job %s", job_id)
    return run_job_pipeline(
        job_id=job_id,
        source_key=source_key,
        media_type=media_type,
        operations=operations,
        result_key=result_key,
    )


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