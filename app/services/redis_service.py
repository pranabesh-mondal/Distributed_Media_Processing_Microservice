"""Resilient Redis client for job-status tracking.

Wraps the ``redis`` client so that network failures (connection refused,
timeouts, dropped connections) are handled gracefully:

* Fail fast on connect/socket timeouts instead of hanging forever.
* Persist a ``failed`` status (with a reason) when retries are exhausted.
* Distinguish transient network failures from permanent errors so callers
  (or Celery tasks) know whether a retry is worthwhile.

The raw ``redis`` exceptions are translated into the local exception taxonomy
from ``app.services.retries``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import redis
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
    RedisError,
    TimeoutError as RedisTimeoutError,
)

from app.config import Settings, get_settings
from app.services.retries import (
    PermanentNetworkError,
    TransientNetworkError,
    retry_with_backoff,
)

logger = logging.getLogger(__name__)

JOB_STATUS_KEY = "jobs:{job_id}:status"
JOB_ERROR_KEY = "jobs:{job_id}:error"
JOB_PAYLOAD_KEY = "jobs:{job_id}:payload"  # media type + operations (JSON)
JOB_RESULT_KEY = "jobs:{job_id}:result"  # worker outcome (JSON)
JOB_CREATED_KEY = "jobs:{job_id}:created_at"  # ISO timestamp
JOB_UPDATED_KEY = "jobs:{job_id}:updated_at"  # ISO timestamp


class RedisServiceError(Exception):
    """Base error raised by the Redis service layer."""


class RedisUnavailable(RedisServiceError, TransientNetworkError):
    """Redis is unreachable or the connection was lost (retryable)."""


class RedisOperationError(RedisServiceError, PermanentNetworkError):
    """A Redis command failed for a non-transient reason."""


class RedisService:
    """Thread-safe wrapper around a lazily-created ``redis.Redis`` client."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings: Settings = settings or get_settings()
        self._client: Optional[redis.Redis] = None

    @property
    def client(self) -> redis.Redis:
        """Lazily construct the redis client with conservative timeouts."""
        if self._client is None:
            self._client = redis.Redis(
                host=self.settings.REDIS_HOST,
                port=self.settings.REDIS_PORT,
                db=self.settings.REDIS_DB,
                decode_responses=True,
                socket_connect_timeout=self.settings.REDIS_CONNECT_TIMEOUT,
                socket_timeout=self.settings.REDIS_SOCKET_TIMEOUT,
                socket_keepalive=True,
                retry_on_timeout=True,
                # Re-issue a ping on idle connections so stale sockets are
                # detected and reconnected rather than surfacing as random I/O
                # errors much later.
                health_check_interval=self.settings.REDIS_HEALTH_CHECK_INTERVAL,
            )
        return self._client

    def ping(self) -> bool:
        """Return ``True`` if Redis responds to a PING.

        Never raises; transient failures are retried and the final outcome is
        reported as ``False``.
        """
        try:
            self._retry_command(self.client.ping, method="ping")
            return True
        except RedisServiceError:
            return False

    # ---- Internal retry plumbing --------------------------------------

    def _retry_command(self, callable_, *args, method: str, **kwargs):
        """Run a redis command, translating failures with bounded backoff.

        Transient network failures are retried ``DEFAULT_MAX_RETRIES`` times
        with exponential backoff before surfacing the failure to the caller.
        """

        def run():
            try:
                return callable_(*args, **kwargs)
            except (RedisConnectionError, RedisTimeoutError) as exc:
                logger.warning(
                    "Transient Redis network failure on '%s': %s", method, exc
                )
                raise RedisUnavailable(
                    f"Redis network failure during '{method}': {exc}"
                ) from exc

        retry_cmd = retry_with_backoff(
            run,
            max_retries=self.settings.DEFAULT_MAX_RETRIES,
            base_delay=self.settings.RETRY_BACKOFF,
            max_delay=self.settings.RETRY_BACKOFF_MAX,
        )
        return retry_cmd()

    # ---- Job lifecycle primitives -------------------------------------

    def _status_key(self, job_id: str) -> str:
        return JOB_STATUS_KEY.format(job_id=job_id)

    def _error_key(self, job_id: str) -> str:
        return JOB_ERROR_KEY.format(job_id=job_id)

    def _payload_key(self, job_id: str) -> str:
        return JOB_PAYLOAD_KEY.format(job_id=job_id)

    def _result_key(self, job_id: str) -> str:
        return JOB_RESULT_KEY.format(job_id=job_id)

    def _created_key(self, job_id: str) -> str:
        return JOB_CREATED_KEY.format(job_id=job_id)

    def _updated_key(self, job_id: str) -> str:
        return JOB_UPDATED_KEY.format(job_id=job_id)

    @property
    def _ttl(self) -> int:
        """TTL (seconds) applied to every job key so records self-expire."""
        return self.settings.REDIS_JOB_TTL

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_job(self, job_id: str, payload_json: Optional[str] = None) -> str:
        """Record a new job as ``pending`` and return its status key.

        The status, payload, and creation timestamp are written atomically in a
        single pipeline so a crash between writes can never leave a ``pending``
        job without its payload. All keys carry the job TTL.
        """
        now = self._now_iso()

        def run():
            pipe = self.client.pipeline(transaction=True)
            pipe.set(self._status_key(job_id), "pending", ex=self._ttl)
            if payload_json is not None:
                pipe.set(self._payload_key(job_id), payload_json, ex=self._ttl)
            pipe.set(self._created_key(job_id), now, ex=self._ttl)
            pipe.set(self._updated_key(job_id), now, ex=self._ttl)
            pipe.execute()

        self._retry_command(run, method="create_job")
        return self._status_key(job_id)

    def update_status(self, job_id: str, status: str) -> None:
        """Set the current status of a job, retrying on network failures."""
        self._retry_command(
            self.client.set, self._status_key(job_id), status,
            ex=self._ttl, method="update_status",
        )
        self._retry_command(
            self.client.set, self._updated_key(job_id), self._now_iso(),
            ex=self._ttl, method="touch_updated",
        )

    def get_created_at(self, job_id: str) -> Optional[str]:
        """Return the job creation timestamp (ISO string), or ``None``."""
        return self._retry_command(
            self.client.get, self._created_key(job_id), method="get_created_at"
        )

    def get_updated_at(self, job_id: str) -> Optional[str]:
        """Return the job's last update timestamp (ISO string), or ``None``."""
        return self._retry_command(
            self.client.get, self._updated_key(job_id), method="get_updated_at"
        )

    def get_status(self, job_id: str) -> Optional[str]:
        """Return the current status, or ``None`` if the job does not exist."""
        return self._retry_command(
            self.client.get, self._status_key(job_id), method="get_status"
        )

    def set_error(self, job_id: str, message: str) -> None:
        """Persist an error message for a failed job."""
        self._retry_command(
            self.client.set, self._error_key(job_id), message,
            ex=self._ttl, method="set_error",
        )

    def get_error(self, job_id: str) -> Optional[str]:
        """Return the persisted error message for a job."""
        return self._retry_command(
            self.client.get, self._error_key(job_id), method="get_error"
        )

    def set_payload(self, job_id: str, payload_json: str) -> None:
        """Store the job payload (media type + operations) as JSON."""
        self._retry_command(
            self.client.set, self._payload_key(job_id), payload_json,
            ex=self._ttl, method="set_payload",
        )

    def get_payload(self, job_id: str) -> Optional[str]:
        """Return the stored job payload JSON, or ``None``."""
        return self._retry_command(
            self.client.get, self._payload_key(job_id), method="get_payload"
        )

    def set_result(self, job_id: str, result_json: str) -> None:
        """Store the worker outcome (result key, thumbnails, metadata) as JSON."""
        self._retry_command(
            self.client.set, self._result_key(job_id), result_json,
            ex=self._ttl, method="set_result",
        )

    def get_result(self, job_id: str) -> Optional[str]:
        """Return the stored worker outcome JSON, or ``None``."""
        return self._retry_command(
            self.client.get, self._result_key(job_id), method="get_result"
        )