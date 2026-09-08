"""Celery application configuration with network-failure resilience.

Central place where the Celery app is configured so workers and the FastAPI
app share the same broker/backend and retry policy. Includes helpers that
make dispatching resilient to a temporarily unreachable broker.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import Celery, Task
from kombu.exceptions import OperationalError

from app.config import get_settings
from app.services.retries import TransientNetworkError, retry_with_backoff

logger = logging.getLogger(__name__)

settings = get_settings()

# A single Celery app used by both the API layer (to enqueue) and the workers
# (to consume). Configuration is kept consistent from the settings object.
celery_app = Celery(
    "distributed_media_processing",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    # Transmission format.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Acknowledge work only after execution succeeds so a worker crash does not
    # drop a job; tasks are retried / redelivered instead.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Avoid prefetching many tasks at once (important for CPU-heavy media work).
    worker_prefetch_multiplier=1,
    task_queue_max_priority=10,
    # Hard and soft time limits per task.
    task_time_limit=900,
    task_soft_time_limit=840,
    # Result backend cleanup.
    result_expires=settings.CELERY_RESULT_EXPIRES,
    # Default retry policy applied to tasks decorated with autoretry_for.
    task_default_retry_delay=settings.WORKER_RETRY_BACKOFF,
    task_max_retries=settings.WORKER_MAX_RETRIES,
    # Keep retrying to connect to the broker so workers survive broker restarts.
    broker_connection_retry=True,
    broker_connection_max_retries=10,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=5,
    broker_transport_options={
        "max_retries": 10,
        "interval_start": 2,
        "interval_step": 2,
        "interval_max": 30,
    },
    # Default queue consumed by workers.
    task_default_queue=settings.CELERY_QUEUE,
)

# Tasks are discovered automatically from the ``app.workers`` package.
celery_app.autodiscover_tasks(["app.workers"], force=True)

# A sanitized broker URL for logging (credentials masked).
def _mask_url(url: str) -> str:
    """Mask any credentials embedded in a connection URL."""
    import re

    return re.sub(r"(//[^:/@]+):([^@]+)@", r"\1:***@", url)


logger.info("Celery configured with broker=%s backend=%s",
            _mask_url(settings.CELERY_BROKER_URL),
            _mask_url(settings.CELERY_RESULT_BACKEND))


class NetworkAwareTask(Task):
    """Base task that translates transient failures into Celery retries.

    Any exception inheriting from :class:`TransientNetworkError` is retried
    with exponential backoff (up to ``WORKER_MAX_RETRIES``); other exceptions
    fail immediately.
    """

    autoretry_for = (TransientNetworkError,)
    retry_backoff = settings.WORKER_RETRY_BACKOFF
    retry_backoff_max = settings.WORKER_RETRY_BACKOFF_MAX
    retry_jitter = True
    max_retries = settings.WORKER_MAX_RETRIES

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error("Task %s (%s) failed permanently: %s",
                     self.name, task_id, exc)
        super().on_failure(exc, task_id, args, kwargs, einfo)


def dispatch_task(task, *args, **kwargs) -> "Any":
    """Enqueue a task, surviving a briefly-unreachable broker.

    If the broker is temporarily down, the dispatch is retried with backoff
    (bounded by ``DEFAULT_MAX_RETRIES``). A permanent failure re-raises the
    original ``OperationalError`` so the caller knows the job was not queued.
    """
    retry_dispatch = retry_with_backoff(
        lambda: task.apply_async(*args, **kwargs),
        max_retries=settings.DEFAULT_MAX_RETRIES,
        base_delay=settings.RETRY_BACKOFF,
        max_delay=settings.RETRY_BACKOFF_MAX,
        retry_on=(OperationalError,),
    )
    try:
        async_result = retry_dispatch()
    except OperationalError as exc:
        logger.error(
            "Permanent broker failure dispatching %s: %s", task.name, exc
        )
        raise
    logger.info("Dispatched task %s async_id=%s",
                task.name, async_result.id)
    return async_result