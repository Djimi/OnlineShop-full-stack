"""Mutation read-back verification and absence classification."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from botocore.exceptions import ClientError

from ..errors import MutationVerificationError

T = TypeVar("T")

_ABSENT_CODES = {
    "404",
    "NoSuchKey",
    "NoSuchEntity",
    "NotFoundException",
    "ResourceNotFoundException",
    "ImageNotFoundException",
    "RepositoryNotFoundException",
}


def absent_or_read(error: BaseException) -> bool:
    """True only for genuine absence ClientError codes."""
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "")
        return code in _ABSENT_CODES
    return False


def mutate_and_read_back(
    mutate: Callable[[], object],
    read: Callable[[], T],
    *,
    label: str,
    check: Callable[[T], bool] | None = None,
    expected: object | None = None,
) -> T:
    """Run a mutation, read it back, and fail closed on drift or read errors."""
    mutate()
    try:
        result = read()
    except Exception as error:
        raise MutationVerificationError(label) from error
    if check is not None and not check(result):
        raise MutationVerificationError(label)
    if expected is not None and result != expected:
        raise MutationVerificationError(label)
    return result
