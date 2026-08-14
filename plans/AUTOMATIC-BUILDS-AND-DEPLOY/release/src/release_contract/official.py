"""Resolve the canonical Git tag for the release currently deployed.

The production snapshot is taken before a promotion can mutate ECS or the
frontend.  Its previous-release identity must therefore come from the live
frontend marker, not from whichever GitHub tag happens to sort highest.  This
module owns the response-shape and identity checks for the paginated GitHub
tags response; the shell wrapper resolves and peels the exact Git ref.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from .semver import is_valid as is_valid_semver

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_MAX_VERSION_LENGTH = 32


class OfficialTagError(ValueError):
    """Raised when the paginated tags response cannot identify one tag."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def resolve_official_tag(pages: Any, version: Any, source_sha: Any) -> dict[str, str]:
    """Resolve the exact canonical tag named by the live marker.

    ``gh api --paginate --slurp`` produces ``[[tag, ...], [tag, ...], ...]``.
    Only the one canonical ``v<version>`` name derived from the live marker is
    considered.  Other, newer tags are deliberately ignored.  A missing,
    duplicated/conflicting, malformed, or SHA-mismatched canonical tag fails
    closed.
    """

    if (
        not isinstance(version, str)
        or len(version) > _MAX_VERSION_LENGTH
        or not is_valid_semver(version)
    ):
        raise OfficialTagError(
            "LIVE_VERSION_INVALID",
            f"live frontend marker has an invalid canonical version: {version!r}",
        )
    if not isinstance(source_sha, str) or not _FULL_SHA_RE.fullmatch(source_sha):
        raise OfficialTagError(
            "LIVE_SOURCE_SHA_INVALID",
            f"live frontend marker has an invalid full source SHA: {source_sha!r}",
        )
    if (
        not isinstance(pages, list)
        or not pages
        or any(not isinstance(page, list) for page in pages)
    ):
        raise OfficialTagError(
            "TAG_API_SHAPE",
            "GitHub tags response must be a non-empty array of page arrays",
        )

    expected_tag = f"v{version}"
    candidates: list[dict[str, str]] = []
    for page_index, page in enumerate(pages, start=1):
        for item_index, item in enumerate(page, start=1):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise OfficialTagError(
                    "TAG_API_SHAPE",
                    f"GitHub tags page {page_index} item {item_index} is not a tag object",
                )
            if item["name"] != expected_tag:
                continue
            commit = item.get("commit")
            sha = commit.get("sha") if isinstance(commit, dict) else None
            if not isinstance(sha, str) or not _FULL_SHA_RE.fullmatch(sha):
                raise OfficialTagError(
                    "TAG_SHA_INVALID",
                    f"canonical tag {expected_tag!r} has no valid full commit SHA",
                )
            if sha != source_sha:
                raise OfficialTagError(
                    "TAG_SHA_MISMATCH",
                    f"canonical tag {expected_tag!r} resolves to {sha!r}, expected live "
                    f"source SHA {source_sha!r}",
                )
            candidates.append({"tag": expected_tag, "version": version, "sha": sha})

    if not candidates:
        raise OfficialTagError(
            "TAG_NOT_FOUND",
            f"GitHub tags response contains no canonical tag {expected_tag!r} for the live release",
        )
    if any(candidate != candidates[0] for candidate in candidates[1:]):
        # This is defensive today because the SHA is checked above, but keeps
        # duplicate observations fail-closed if the returned shape grows.
        raise OfficialTagError(
            "TAG_DUPLICATE_CONFLICT",
            f"canonical tag {expected_tag!r} appears with conflicting identity",
        )
    return candidates[0]


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="resolve the live release's canonical Git tag")
    parser.add_argument("tags", metavar="TAGS_JSON", help="slurped gh tags response")
    parser.add_argument("--version", required=True, help="version from the live frontend marker")
    parser.add_argument(
        "--source-sha", required=True, help="source SHA from the live frontend marker"
    )
    args = parser.parse_args(argv)
    try:
        with open(args.tags, encoding="utf-8") as handle:
            pages = json.load(handle)
        result = resolve_official_tag(pages, args.version, args.source_sha)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"official-tag: TAG_API_SHAPE: cannot read tags response: {exc}", file=sys.stderr)
        return 1
    except OfficialTagError as exc:
        print(f"official-tag: {exc.code}: {exc.message}", file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
