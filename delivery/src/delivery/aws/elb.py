"""Read-only Elastic Load Balancing helpers (staging E2E entry point)."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from ..errors import AbsentResourceError, ReadError


def describe_load_balancer(client: Any, name: str) -> dict:
    """Describe a load balancer by name, absent only when it does not exist."""
    try:
        response = client.describe_load_balancers(Names=[name])
    except ClientError as error:
        raise ReadError(f"describe_load_balancers failed for {name}") from error
    balancers = response.get("LoadBalancers") or []
    if not balancers:
        raise AbsentResourceError(f"load balancer {name} not found")
    return balancers[0]


def load_balancer_dns_name(client: Any, name: str) -> str:
    """Return the DNS name of a load balancer, failing closed when absent."""
    dns_name = describe_load_balancer(client, name).get("DNSName")
    if not isinstance(dns_name, str) or not dns_name:
        raise ReadError(f"load balancer {name} has no DNSName")
    return dns_name


def describe_target_health(client: Any, target_group_arn: str) -> list[dict]:
    """Describe target health states of a target group (read-only)."""
    try:
        response = client.describe_target_health(TargetGroupArn=target_group_arn)
    except ClientError as error:
        raise ReadError(
            f"describe_target_health failed for {target_group_arn}"
        ) from error
    return response.get("TargetHealthDescriptions") or []
