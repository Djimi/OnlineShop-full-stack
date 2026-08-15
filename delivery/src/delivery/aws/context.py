"""AWS context, identity preflight, and cached session/client factories."""

from __future__ import annotations

from typing import Any, Literal

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ReadError, ValidationError

Boto3Client = Any

_SESSIONS: dict[tuple[str | None, str], boto3.Session] = {}
_CLIENTS: dict[tuple[str | None, str, str], Boto3Client] = {}


class AwsContext(BaseModel):
    model_config = ConfigDict(strict=True)

    profile: str | None = None
    region: str
    account_id: str
    environment: Literal["production", "staging"]
    identifiers: dict[str, str] = Field(default_factory=dict)


def session_for(ctx: AwsContext) -> boto3.Session:
    """Return the cached boto3 session for the context's profile and region."""
    key = (ctx.profile, ctx.region)
    session = _SESSIONS.get(key)
    if session is None:
        session = boto3.Session(profile_name=ctx.profile, region_name=ctx.region)
        _SESSIONS[key] = session
    return session


def client_for(ctx: AwsContext, service: str) -> Boto3Client:
    """Return the cached boto3 client for the context and service."""
    key = (ctx.profile, ctx.region, service)
    client = _CLIENTS.get(key)
    if client is None:
        client = session_for(ctx).client(service)
        _CLIENTS[key] = client
    return client


def identity_preflight(ctx: AwsContext) -> str:
    """Verify the caller identity and account before any AWS work."""
    sts = client_for(ctx, "sts")
    try:
        response = sts.get_caller_identity()
    except ClientError as error:
        raise ReadError("get_caller_identity failed") from error
    account_id = response.get("Account")
    if account_id != ctx.account_id:
        raise ValidationError(
            f"identity account {account_id!r} does not match expected {ctx.account_id!r}"
        )
    return account_id


def require_environment(ctx: AwsContext, allowed: str | tuple[str, ...]) -> None:
    """Fail closed when the context environment is not in the allowed set."""
    if ctx.environment not in allowed:
        raise ValidationError(
            f"environment {ctx.environment!r} is not in allowed set {allowed}"
        )
