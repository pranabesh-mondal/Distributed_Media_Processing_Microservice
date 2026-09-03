"""Retry and network-failure handling utilities.

Provides a small exception taxonomy that distinguishes *transient* network
failures (connection refused, timeouts, broker/backend temporarily down) from
*permanent* failures, plus a ``retry_with_backoff`` helper that retries the
former with exponential backoff so the surrounding code does not retry things
that can never succeed.

Transient failures should be retried; permanent failures should fail fast.
"""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable, Iterable, Tuple, Type

logger = logging.getLogger(__name__)


class TransientNetworkError(Exception):
    """Base for failures caused by a temporary network condition.

    Op may succeed if retried after a short delay (e.g. a broker or cache
    that is restarting, a dropped connection, or an S3 request timeout).
    """


class PermanentNetworkError(Exception):
    """Base for failures that will never succeed on retry.

    Examples: authentication/authorization errors, missing objects, invalid
    configuration. These should surface immediately (or be sent to a dead
    letter) rather than being retried.
    """


def is_transient(exc: BaseException) -> bool:
    """Return ``True`` if an exception represents a retryable network failure."""
    if isinstance(exc, TransientNetworkError):
        return True
    # Allow downstream libraries to contribute their own transient exceptions.
    for marker in getattr(exc, "__transient_network__", []):
        if isinstance(exc, marker):
            return True
    return False


def retry_with_backoff(
    func: Callable[..., T],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retry_on: Iterable[Type[BaseException]] = (TransientNetworkError,),
) -> Callable[..., T]:
    """Retry ``func`` with exponential backoff on transient failures.

    Suitable for non-Celery call sites (Redis, pre-signed URL generation,
    dispatch helpers). Retries only when the raised exception is an instance
    of one of ``retry_on``; any other exception propagates immediately.

    Args:
        max_retries: Total attempts capped at ``1 + max_retries`` (1 initial +
            ``max_retries`` retries).
        base_delay: Backoff delay for the first retry, in seconds.
        max_delay: Upper bound for the backoff delay, in seconds.
        retry_on: Exception types considered transient.

    Returns:
        The return value of ``func`` once it succeeds.

    Raises:
        The last exception if all retries are exhausted.
    """
    retry_types: Tuple[Type[BaseException], ...] = tuple(retry_on)

    @wraps(func)
    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        attempt = 0
        while True:
            try:
                result = func(*args, **kwargs)
                if attempt > 0:
                    logger.info(
                        "Recovered in %s after %d retries",
                        func.__name__,
                        attempt,
                    )
                return result
            except retry_types as exc:
                attempt += 1
                if attempt > max_retries:
                    logger.error(
                        "Giving up on %s after %d attempts: %s",
                        func.__name__,
                        attempt,
                        exc,
                    )
                    raise
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                logger.warning(
                    "Retrying %s (attempt %d/%d) in %.2fs due to transient "
                    "failure: %s",
                    func.__name__,
                    attempt,
                    max_retries,
                    delay,
                    exc,
                )
                time.sleep(delay)

    return wrapper