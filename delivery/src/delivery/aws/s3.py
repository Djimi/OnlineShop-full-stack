"""S3 object operations with checksum verification and read-back."""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Any

from botocore.exceptions import ClientError

from ..errors import AbsentResourceError, MutationVerificationError, ReadError
from .readback import absent_or_read, mutate_and_read_back


def _base64_to_hex(checksum: str) -> str:
    try:
        return base64.b64decode(checksum).hex()
    except (ValueError, binascii.Error) as error:
        raise ReadError("invalid base64 ChecksumSHA256 value") from error


def object_exists(client: Any, bucket: str, key: str) -> bool:
    """True when the object exists, False only on genuine 404 absence."""
    try:
        client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if absent_or_read(error):
            return False
        raise ReadError(f"head_object failed for s3://{bucket}/{key}") from error
    return True


def get_object_sha256(client: Any, bucket: str, key: str) -> str:
    """Return the canonical hex SHA-256, preferring server-side checksums."""
    try:
        head = client.head_object(
            Bucket=bucket, Key=key, ChecksumMode="ENABLED"
        )
    except ClientError as error:
        if absent_or_read(error):
            raise AbsentResourceError(f"s3://{bucket}/{key} not found") from error
        raise ReadError(f"head_object failed for s3://{bucket}/{key}") from error
    checksum = head.get("ChecksumSHA256")
    if checksum:
        return _base64_to_hex(checksum)
    try:
        body = client.get_object(Bucket=bucket, Key=key).get("Body")
    except ClientError as error:
        raise ReadError(f"get_object failed for s3://{bucket}/{key}") from error
    if body is None:
        raise ReadError(f"get_object returned no body for s3://{bucket}/{key}")
    return hashlib.sha256(body.read()).hexdigest()


def put_object(client: Any, bucket: str, key: str, body: bytes) -> str:
    """Upload with a SHA-256 checksum and verify size and checksum read-back."""
    head = mutate_and_read_back(
        lambda: client.put_object(Bucket=bucket, Key=key, Body=body, ChecksumAlgorithm="SHA256"),
        lambda: client.head_object(
            Bucket=bucket, Key=key, ChecksumMode="ENABLED"
        ),
        label=f"s3://{bucket}/{key}",
        check=lambda observed: observed.get("ContentLength") == len(body)
        and bool(observed.get("ChecksumSHA256")),
    )
    canonical = _base64_to_hex(head["ChecksumSHA256"])
    if canonical != hashlib.sha256(body).hexdigest():
        raise MutationVerificationError(f"s3://{bucket}/{key} checksum mismatch after put")
    return canonical


def list_objects(client: Any, bucket: str, prefix: str) -> list[dict]:
    """List object summaries under a prefix."""
    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return response.get("Contents") or []
