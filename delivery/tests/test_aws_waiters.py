"""Tests for bounded polling and transient-error retry helpers."""

import pytest
from conftest import client_error

from delivery.aws.waiters import ThrottlingError, bounded_waiter, with_retry
from delivery.errors import ValidationError, WaiterTimeoutError


def test_bounded_waiter_ok_when_poll_immediately_true():
    assert bounded_waiter(lambda: True, label="ready", timeout_seconds=1, interval_seconds=0.5)


def test_bounded_waiter_polls_until_true():
    state = {"count": 0}

    def poll():
        state["count"] += 1
        return state["count"] >= 2

    assert bounded_waiter(poll, label="ready", timeout_seconds=1, interval_seconds=0.5)
    assert state["count"] == 2


def test_bounded_waiter_times_out():
    with pytest.raises(WaiterTimeoutError):
        bounded_waiter(lambda: False, label="never", timeout_seconds=1, interval_seconds=0.5)


@pytest.mark.parametrize("timeout", [0, -1, 3601])
def test_bounded_waiter_rejects_invalid_timeout(timeout):
    with pytest.raises(ValidationError):
        bounded_waiter(lambda: True, label="x", timeout_seconds=timeout)


@pytest.mark.parametrize("interval", [0.1, 0.4, 120.5])
def test_bounded_waiter_rejects_invalid_interval(interval):
    with pytest.raises(ValidationError):
        bounded_waiter(lambda: True, label="x", timeout_seconds=1, interval_seconds=interval)


def test_with_retry_succeeds_after_transient_errors():
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        if calls["count"] < 3:
            raise client_error("ThrottlingException")
        return "ok"

    assert with_retry(fn, base_delay=0.001) == "ok"
    assert calls["count"] == 3


def test_with_retry_gives_up_with_throttling_error():
    def fn():
        raise client_error("RequestLimitExceeded")

    with pytest.raises(ThrottlingError):
        with_retry(fn, attempts=3, base_delay=0.001)


def test_with_retry_exhausts_exactly_attempts():
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        raise client_error("TooManyRequestsException")

    with pytest.raises(ThrottlingError):
        with_retry(fn, attempts=4, base_delay=0.001)
    assert calls["count"] == 4


def test_with_retry_never_retries_non_transient_errors():
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        raise client_error("AccessDenied")

    with pytest.raises(Exception) as excinfo:
        with_retry(fn, attempts=3, base_delay=0.001)
    assert calls["count"] == 1
    assert excinfo.value.response["Error"]["Code"] == "AccessDenied"


def test_with_retry_never_retries_non_client_errors():
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        raise ValueError("boom")

    with pytest.raises(ValueError):
        with_retry(fn, attempts=3, base_delay=0.001)
    assert calls["count"] == 1
