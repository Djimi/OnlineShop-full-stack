"""RDS instance start/stop operations and tag read/mutation with read-back."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from ..errors import AbsentResourceError, MutationVerificationError, ReadError
from .readback import absent_or_read, mutate_and_read_back


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


def db_instance_arn(instance: dict) -> str:
    """Return the DBInstanceArn, failing closed when absent."""
    arn = instance.get("DBInstanceArn")
    if not arn:
        raise ReadError(
            f"DB instance {instance.get('DBInstanceIdentifier')!r} has no DBInstanceArn"
        )
    return arn


def list_tags_for_resource(client: Any, arn: str) -> dict[str, str]:
    """List RDS tags keyed by name; read failures are errors, never absence."""
    try:
        response = client.list_tags_for_resource(ResourceName=arn)
    except ClientError as error:
        raise ReadError(f"list_tags_for_resource failed for {arn}") from error
    tags = response.get("TagList") or []
    return {tag["Key"]: tag["Value"] for tag in tags if tag.get("Key")}


def add_tags_to_resource(client: Any, arn: str, tags: dict[str, str]) -> dict[str, str]:
    """Add/overwrite tags and verify the full tag read-back matches."""
    expected = dict(tags)
    return mutate_and_read_back(
        lambda: client.add_tags_to_resource(
            ResourceName=arn, Tags=[{"Key": key, "Value": value} for key, value in tags.items()]
        ),
        lambda: list_tags_for_resource(client, arn),
        label=f"add_tags_to_resource for {arn}",
        check=lambda observed: all(observed.get(key) == value for key, value in expected.items()),
    )


def remove_tags_from_resource(client: Any, arn: str, keys: list[str]) -> dict[str, str]:
    """Remove tags and verify the keys are absent from the read-back."""
    return mutate_and_read_back(
        lambda: client.remove_tags_from_resource(ResourceName=arn, TagKeys=keys),
        lambda: list_tags_for_resource(client, arn),
        label=f"remove_tags_from_resource for {arn}",
        check=lambda observed: all(key not in observed for key in keys),
    )


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
