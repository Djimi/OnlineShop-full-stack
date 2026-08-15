"""AWS adapters: bounded, read-back-verified access to AWS services."""

from ..errors import (
    AbsentResourceError,
    MutationVerificationError,
    ReadError,
    ValidationError,
    WaiterTimeoutError,
)
from .cloudfront import create_invalidation, get_distribution
from .context import (
    AwsContext,
    client_for,
    identity_preflight,
    require_environment,
    session_for,
)
from .ecr import (
    batch_get_image_digests,
    get_lifecycle_policy,
    get_lifecycle_policy_preview,
    put_image,
    put_lifecycle_policy,
    repository_digest,
    start_lifecycle_policy_preview,
)
from .ecs import (
    describe_services,
    describe_task_definition,
    primary_deployment,
    register_task_definition,
    running_digests,
    service_deployment,
    update_service,
    wait_for_deployment,
)
from .rds import describe_db_instance, start_db_instance, stop_db_instance
from .readback import absent_or_read, mutate_and_read_back
from .s3 import get_object_sha256, list_objects, object_exists, put_object
from .secrets import secret_reference
from .waiters import ThrottlingError, bounded_waiter, with_retry

__all__ = [
    "AbsentResourceError",
    "AwsContext",
    "MutationVerificationError",
    "ReadError",
    "ThrottlingError",
    "ValidationError",
    "WaiterTimeoutError",
    "absent_or_read",
    "batch_get_image_digests",
    "bounded_waiter",
    "client_for",
    "create_invalidation",
    "describe_db_instance",
    "describe_services",
    "describe_task_definition",
    "get_distribution",
    "get_lifecycle_policy",
    "get_lifecycle_policy_preview",
    "get_object_sha256",
    "identity_preflight",
    "list_objects",
    "mutate_and_read_back",
    "object_exists",
    "primary_deployment",
    "put_image",
    "put_lifecycle_policy",
    "put_object",
    "register_task_definition",
    "repository_digest",
    "require_environment",
    "running_digests",
    "secret_reference",
    "service_deployment",
    "session_for",
    "start_db_instance",
    "start_lifecycle_policy_preview",
    "stop_db_instance",
    "update_service",
    "wait_for_deployment",
    "with_retry",
]
