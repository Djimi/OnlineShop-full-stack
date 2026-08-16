"""CloudFront distribution and invalidation operations with read-back."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from botocore.exceptions import ClientError

from ..errors import ReadError
from .readback import mutate_and_read_back


def get_distribution(client: Any, distribution_id: str) -> dict:
    """Read a distribution, wrapping any failure as a read error."""
    try:
        return client.get_distribution(Id=distribution_id)
    except ClientError as error:
        raise ReadError(f"get_distribution failed for {distribution_id}") from error


def create_invalidation(client: Any, distribution_id: str, paths: list[str]) -> dict:
    """Create an invalidation and verify the read-back reports the same id."""
    invalidation_id: str | None = None

    def mutate() -> None:
        nonlocal invalidation_id
        response = client.create_invalidation(
            DistributionId=distribution_id,
            InvalidationBatch={
                "Paths": {"Quantity": len(paths), "Items": list(paths)},
                "CallerReference": uuid4().hex,
            },
        )
        invalidation_id = (response.get("Invalidation") or {}).get("Id")
        if not invalidation_id:
            raise ReadError(f"create_invalidation returned no id for {distribution_id}")

    def read() -> dict:
        response = client.get_invalidation(DistributionId=distribution_id, Id=invalidation_id)
        return response.get("Invalidation") or {}

    return mutate_and_read_back(
        mutate,
        read,
        label=f"invalidation for {distribution_id}",
        check=lambda observed: observed.get("Id") == invalidation_id,
    )
