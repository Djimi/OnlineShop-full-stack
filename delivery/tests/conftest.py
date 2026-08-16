"""Shared helpers for the AWS adapter test suite."""

import pytest
from botocore.exceptions import ClientError


def client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "operation",
    )


@pytest.fixture(autouse=True)
def _no_ecr_retry_sleep(monkeypatch):
    """Never burn real time in the bounded ECR digest retry loop.

    ``ecr._batch_get_digests_retrying`` sleeps up to 5s between attempts for
    freshly pushed images; the retry is exercised by many fakes throughout
    the suite, so the sleep is a test-only no-op. The production delay
    (5.0s, 6 attempts) is unchanged.
    """
    monkeypatch.setattr("delivery.aws.ecr._sleep", lambda _: None)
