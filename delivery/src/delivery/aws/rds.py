"""RDS instance start/stop operations with immediate status verification."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from ..errors import AbsentResourceError, MutationVerificationError, ReadError
from .readback import absent_or_read


def describe_db_instance(client: Any, db_instance_identifier: str) -> dict:
    """Describe a DB instance, absent only when it genuinely does not exist."""
    try:
        response = client.describe_db_instances(DBInstanceIdentifier=db_instance_identifier)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if absent_or_read(error) or code == "DBInstanceNotFound":
            raise AbsentResourceError(f"DB instance {db_instance_identifier} not found") from error
        raise ReadError(f"describe_db_instances failed for {db_instance_identifier}") from error
    instances = response.get("DBInstances") or []
    if not instances:
        raise AbsentResourceError(f"DB instance {db_instance_identifier} not found")
    return instances[0]


def _verified_status(client: Any, identifier: str, expected: str, action: str) -> dict:
    instance = describe_db_instance(client, identifier)
    status = instance.get("DBInstanceStatus")
    if status != expected:
        raise MutationVerificationError(
            f"DB instance {identifier} did not reach {expected} after {action} "
            f"(observed {status!r})"
        )
    return instance


def start_db_instance(client: Any, db_instance_identifier: str) -> dict:
    """Start a DB instance and verify it reports status available."""
    client.start_db_instance(DBInstanceIdentifier=db_instance_identifier)
    return _verified_status(client, db_instance_identifier, "available", "start")


def stop_db_instance(client: Any, db_instance_identifier: str) -> dict:
    """Stop a DB instance and verify it reports status stopped."""
    client.stop_db_instance(DBInstanceIdentifier=db_instance_identifier)
    return _verified_status(client, db_instance_identifier, "stopped", "stop")
