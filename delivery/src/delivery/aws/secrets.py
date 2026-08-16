"""Secret references only: the engine resolves ARNs and never reads secret values.

Per OP-GEN-04 secrets enter ECS task definitions as full-ARN
`secrets[].valueFrom` references; no delivery flow needs secret values, so this
adapter deliberately exposes no value-reading API.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from ..errors import ReadError


def secret_reference(secrets_client: Any, secret_name_or_arn: str) -> str:
    """Resolve a secret name to its full ARN, never to its value."""
    if secret_name_or_arn.startswith("arn:"):
        return secret_name_or_arn
    try:
        response = secrets_client.describe_secret(SecretId=secret_name_or_arn)
    except ClientError as error:
        raise ReadError(f"describe_secret failed for {secret_name_or_arn}") from error
    arn = response.get("ARN")
    if not arn:
        raise ReadError(f"describe_secret returned no ARN for {secret_name_or_arn}")
    return arn
