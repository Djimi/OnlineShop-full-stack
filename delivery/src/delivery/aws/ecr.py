"""ECR image and lifecycle policy operations with read-back verification."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from ..errors import AbsentResourceError, MutationVerificationError, ReadError
from .readback import absent_or_read, mutate_and_read_back


def repository_digest(client: Any, repository: str, tag: str) -> str:
    """Resolve a tag to its immutable image digest."""
    try:
        response = client.batch_get_image(
            repositoryName=repository, imageIds=[{"imageTag": tag}]
        )
    except ClientError as error:
        if absent_or_read(error):
            raise AbsentResourceError(f"image {repository}:{tag} not found") from error
        raise ReadError(f"batch_get_image failed for {repository}:{tag}") from error
    images = response.get("images") or []
    if not images:
        raise AbsentResourceError(f"image {repository}:{tag} not found")
    digest = images[0].get("imageDigest")
    if not digest:
        raise ReadError(f"batch_get_image returned no digest for {repository}:{tag}")
    return digest


def batch_get_image_digests(client: Any, repository: str, tags: list[str]) -> dict[str, str]:
    """Resolve many tags at once, failing closed when a requested tag is missing."""
    try:
        response = client.batch_get_image(
            repositoryName=repository, imageIds=[{"imageTag": tag} for tag in tags]
        )
    except ClientError as error:
        raise ReadError(f"batch_get_image failed for {repository}") from error
    digests: dict[str, str] = {}
    for image in response.get("images") or []:
        tag = image.get("imageTag")
        digest = image.get("imageDigest")
        if tag and digest:
            digests[tag] = digest
    missing = [tag for tag in tags if tag not in digests]
    if missing:
        raise AbsentResourceError(
            f"images missing from batch_get_image response for {repository}: "
            f"{', '.join(missing)}"
        )
    return digests


def put_image(client: Any, repository: str, tag: str, image_manifest: bytes) -> dict:
    """Mint an image tag server-side from the recorded manifest bytes."""
    try:
        return client.put_image(
            repositoryName=repository, imageTag=tag, imageManifest=image_manifest
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code == "RepositoryPolicyValidationException":
            raise MutationVerificationError(f"ECR rejected image {repository}:{tag}") from error
        raise


def get_lifecycle_policy(client: Any, repository: str) -> str:
    """Read the lifecycle policy text, absent when no policy is set."""
    try:
        response = client.get_lifecycle_policy(repositoryName=repository)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if absent_or_read(error) or code == "LifecyclePolicyNotFoundException":
            raise AbsentResourceError(f"lifecycle policy for {repository} not found") from error
        raise ReadError(f"get_lifecycle_policy failed for {repository}") from error
    policy = response.get("lifecyclePolicyText")
    if policy is None:
        raise ReadError(f"get_lifecycle_policy returned no text for {repository}")
    return policy


def put_lifecycle_policy(client: Any, repository: str, policy_json: str) -> str:
    """Apply a lifecycle policy and verify the read-back matches byte for byte."""
    return mutate_and_read_back(
        lambda: client.put_lifecycle_policy(
            repositoryName=repository, lifecyclePolicyText=policy_json
        ),
        lambda: get_lifecycle_policy(client, repository),
        label=f"lifecycle policy for {repository}",
        expected=policy_json,
    )


def start_lifecycle_policy_preview(client: Any, repository: str) -> str:
    """Start a read-only lifecycle policy preview and return its id."""
    response = client.start_lifecycle_policy_preview(repositoryName=repository)
    preview_id = response.get("lifecyclePolicyPreviewId")
    if not preview_id:
        raise ReadError(f"start_lifecycle_policy_preview returned no id for {repository}")
    return preview_id


def get_lifecycle_policy_preview(client: Any, repository: str, preview_id: str) -> dict:
    """Fetch the preview results for a started preview."""
    return client.get_lifecycle_policy_preview(
        repositoryName=repository, lifecyclePolicyPreviewId=preview_id
    )
