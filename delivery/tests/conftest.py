"""Shared helpers for the AWS adapter test suite."""

from botocore.exceptions import ClientError


def client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "operation",
    )
