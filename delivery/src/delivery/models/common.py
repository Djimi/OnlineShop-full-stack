"""Shared record building blocks: source, build, artifact, and strict field types."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _parse_utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        if value.utcoffset() != timedelta(0):
            raise ValueError("datetime must be UTC")
    return value


UtcDateTime = Annotated[datetime, BeforeValidator(_parse_utc)]

PositiveInt = Annotated[int, Field(gt=0)]


class StrictRecord(BaseModel):
    model_config = ConfigDict(strict=True)


class Source(StrictRecord):
    # ``repository`` flows into GitHub API URL construction: constrain it to
    # owner/name where each part is URL-path-safe. ``@`` is tolerated because
    # the existing repository fixtures use an at-separated owner segment and
    # it is inert inside a URL path.
    repository: str = Field(pattern=r"^[A-Za-z0-9@_.-]+/[A-Za-z0-9@_.-]+$")
    branch: str
    ref: str
    fullSha: str = Field(pattern=r"^[0-9a-f]{40}$")


class Build(StrictRecord):
    workflowRunId: PositiveInt
    workflowRunAttempt: PositiveInt
    workflowUrl: str = Field(pattern=r"^https://")
    createdAt: UtcDateTime
    completedAt: UtcDateTime


class ArtifactRef(StrictRecord):
    repository: str
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
