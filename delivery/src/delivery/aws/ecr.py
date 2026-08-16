"""ECR image and lifecycle policy operations with read-back verification."""

from __future__ import annotations

import time
from typing import Any

from botocore.exceptions import ClientError

from ..errors import AbsentResourceError, MutationVerificationError, ReadError
from .readback import absent_or_read, mutate_and_read_back

_BATCH_GET_ATTEMPTS = 6
_BATCH_GET_DELAY_SECONDS = 5.0


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _batch_get_digests_retrying(
    client: Any,
    repository: str,
    tags: list[str],
    *,
    attempts: int = _BATCH_GET_ATTEMPTS,
    delay_seconds: float = _BATCH_GET_DELAY_SECONDS,
) -> dict[str, str]:
    """Repeatedly resolve tags until every requested digest is visible.

    A freshly pushed multi-arch index can lag batch_get_image visibility, so
    empty or incomplete responses are retried for a bounded number of attempts.
    Exhausted retries raise ReadError: absence after a push is not provable.
    ClientErrors propagate to the caller for its own classification.
    """
    for attempt in range(attempts):
        response = client.batch_get_image(
            repositoryName=repository, imageIds=[{"imageTag": tag} for tag in tags]
        )
        digests: dict[str, str] = {}
        for image in response.get("images") or []:
            tag = image.get("imageTag")
            digest = image.get("imageDigest")
            if tag and digest:
                digests[tag] = digest
        missing = [tag for tag in tags if tag not in digests]
        if not missing:
            return digests
        if attempt < attempts - 1:
            _sleep(delay_seconds)
    raise ReadError(
        f"images not visible in {repository} after {attempts} attempts: "
        f"{', '.join(missing)} — a freshly pushed image may still be propagating"
    )


def repository_digest(client: Any, repository: str, tag: str) -> str:
    """Resolve a tag to its immutable image digest."""
    try:
        digests = _batch_get_digests_retrying(client, repository, [tag])
    except ClientError as error:
        if absent_or_read(error):
            raise AbsentResourceError(f"image {repository}:{tag} not found") from error
        raise ReadError(f"batch_get_image failed for {repository}:{tag}") from error
    return digests[tag]


def batch_get_image_digests(client: Any, repository: str, tags: list[str]) -> dict[str, str]:
    """Resolve many tags at once, failing closed when a requested tag is missing."""
    try:
        return _batch_get_digests_retrying(client, repository, tags)
    except ClientError as error:
        raise ReadError(f"batch_get_image failed for {repository}") from error


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


def list_images(client: Any, repository: str) -> list[dict]:
    """List image details (digest, tags, pushedAt) with pagination, fail-closed on shape."""
    images: list[dict] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"repositoryName": repository}
        if token:
            kwargs["nextToken"] = token
        try:
            response = client.describe_images(**kwargs)
        except ClientError as error:
            raise ReadError(f"describe_images failed for {repository}") from error
        for image in response.get("imageDetails") or []:
            if not isinstance(image, dict):
                raise ReadError(f"describe_images returned a malformed entry for {repository}")
            digest = image.get("imageDigest")
            pushed = image.get("imagePushedAt")
            if not isinstance(digest, str) or not digest:
                raise ReadError(f"describe_images entry without imageDigest for {repository}")
            if pushed is None:
                raise ReadError(f"describe_images entry without imagePushedAt for {repository}")
            tags = image.get("imageTags") or []
            if not all(isinstance(tag, str) and tag for tag in tags):
                raise ReadError(f"describe_images entry with malformed imageTags for {repository}")
            images.append({"digest": digest, "tags": list(tags), "pushedAt": pushed})
        token = response.get("nextToken")
        if not token:
            return images


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
