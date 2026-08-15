"""Bounded polling and transient-error retry helpers."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from botocore.exceptions import ClientError

from ..errors import ReadError, ValidationError, WaiterTimeoutError

T = TypeVar("T")

_TRANSIENT_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "RequestLimitExceeded",
}


class ThrottlingError(ReadError):
    code = "THROTTLING"


def bounded_waiter(
    poll: Callable[[], bool],
    *,
    label: str,
    timeout_seconds: float,
    interval_seconds: float = 10,
) -> bool:
    """Poll until true or the bounded deadline expires."""
    if not 0 < timeout_seconds <= 3600:
        raise ValidationError(f"timeout_seconds must be in (0, 3600], got {timeout_seconds}")
    if not 0.5 <= interval_seconds <= 120:
        raise ValidationError(f"interval_seconds must be in [0.5, 120], got {interval_seconds}")
    deadline = time.monotonic() + timeout_seconds
    while True:
        if poll():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WaiterTimeoutError(label)
        time.sleep(min(interval_seconds, remaining))


def with_retry(fn: Callable[[], T], *, attempts: int = 3, base_delay: float = 1.0) -> T:
    """Retry only transient throttling ClientErrors with bounded backoff."""
    for attempt in range(attempts):
        try:
            return fn()
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code not in _TRANSIENT_CODES:
                raise
            if attempt == attempts - 1:
                raise ThrottlingError("request throttled after retries") from error
            time.sleep(base_delay * (2**attempt))
