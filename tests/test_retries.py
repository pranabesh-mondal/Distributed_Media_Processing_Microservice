"""Tests for the retry helper and exception taxonomy."""

import pytest

from app.services.retries import (
    PermanentNetworkError,
    TransientNetworkError,
    retry_with_backoff,
)


def test_success_on_first_attempt():
    calls = []

    def func():
        calls.append(1)
        return "ok"

    wrapped = retry_with_backoff(func, max_retries=3, base_delay=0.01)
    assert wrapped() == "ok"
    assert len(calls) == 1


def test_retries_transient_then_succeeds():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TransientNetworkError("temporary")
        return "recovered"

    wrapped = retry_with_backoff(flaky, max_retries=5, base_delay=0.01)
    assert wrapped() == "recovered"
    assert attempts["n"] == 3


def test_gives_up_after_max_retries():
    attempts = {"n": 0}

    def always_transient():
        attempts["n"] += 1
        raise TransientNetworkError("down")

    wrapped = retry_with_backoff(always_transient, max_retries=2, base_delay=0.01)
    with pytest.raises(TransientNetworkError):
        wrapped()
    assert attempts["n"] == 3  # 1 initial + 2 retries


def test_permanent_errors_are_not_retried():
    attempts = {"n": 0}

    def permanent():
        attempts["n"] += 1
        raise PermanentNetworkError("bad input")

    wrapped = retry_with_backoff(permanent, max_retries=5, base_delay=0.01)
    with pytest.raises(PermanentNetworkError):
        wrapped()
    assert attempts["n"] == 1  # fail fast


def test_custom_retry_on_types():
    class CustomError(Exception):
        pass

    attempts = {"n": 0}

    def custom():
        attempts["n"] += 1
        raise CustomError("boom")

    wrapped = retry_with_backoff(
        custom, max_retries=2, base_delay=0.01, retry_on=(CustomError,)
    )
    with pytest.raises(CustomError):
        wrapped()
    assert attempts["n"] == 3
