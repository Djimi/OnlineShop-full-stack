"""ECR release tag promotion and verification rules (Pass 3, subphase 3.3).

A ``release-<version>`` tag is minted server-side from the already recorded
candidate image ``sha-<full-sha>``: the exact manifest bytes already stored in
ECR are re-tagged with ``ecr:PutImage``. The image is never pulled or rebuilt
(the plan's "promote; never rebuild" rule). Because the backend repositories
are configured ``IMMUTABLE_WITH_EXCLUSION``, a ``release-*`` tag can never be
overwritten once minted — the decision here makes sure it is only ever created
from bytes that exactly match the recorded candidate digest.

- ``promote_release_decision`` decides, per backend image, whether the release
  tag must be minted (``action=mint``), already exists pointing at exactly the
  recorded bytes (``action=reuse``; idempotent resume of an interrupted
  promotion), or must fail closed (release tag exists but resolves to different
  bytes; candidate tag missing or mismatched).
- ``verify_release_digest`` is the post-mutation read-back: both the candidate
  and release tags must resolve to the exact recorded digest.

All functions are pure and fixture-tested; shell wrappers gather the ECR state
and pass validated values as JSON files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from . import components as rc
from .semver import is_valid as is_valid_semver

_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class PromoteDecision:
    """Result of a release-tag promotion decision.

    ``action`` is ``"mint"`` or ``"reuse"``; a non-empty ``issues`` list means
    fail closed regardless of ``action`` (callers treat any issue as an error).
    """

    action: str
    issues: list[dict[str, str]] = field(default_factory=list)


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


def _valid_expected(expected: Any) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    """Validate the ``expected`` record; return ``(issues, normalized)``."""
    issues: list[dict[str, str]] = []
    if not isinstance(expected, dict):
        return [_issue("INVALID_EXPECTED", "$", "expected must be an object")], None

    version = expected.get("version")
    if not is_valid_semver(version):
        issues.append(_issue("INVALID_VERSION", "version", f"invalid canonical SemVer {version!r}"))
    source_sha = expected.get("sourceSha")
    if not isinstance(source_sha, str) or not _FULL_SHA_RE.match(source_sha):
        issues.append(_issue("INVALID_SHA", "sourceSha", f"invalid source SHA {source_sha!r}"))
    repository = expected.get("repository")
    if not isinstance(repository, str) or repository not in rc.REPOSITORIES.values():
        issues.append(
            _issue(
                "INVALID_REPOSITORY",
                "repository",
                f"invalid ECR repository {repository!r}; "
                f"expected one of {sorted(rc.REPOSITORIES.values())}",
            )
        )
    image_digest = expected.get("imageDigest")
    if not isinstance(image_digest, str) or not _IMAGE_DIGEST_RE.match(image_digest):
        issues.append(
            _issue("INVALID_DIGEST", "imageDigest", f"invalid recorded digest {image_digest!r}")
        )
    candidate_tag = expected.get("candidateTag")
    if candidate_tag != rc.candidate_tag_for(source_sha or ""):
        issues.append(
            _issue(
                "CANDIDATE_TAG_MISMATCH",
                "candidateTag",
                f"candidate tag {candidate_tag!r} must be sha-<sourceSha>",
            )
        )
    release_tag = expected.get("releaseTag")
    if release_tag != rc.release_tag_for(version or ""):
        issues.append(
            _issue(
                "RELEASE_TAG_MISMATCH",
                "releaseTag",
                f"release tag {release_tag!r} must be release-<version>",
            )
        )
    return issues, expected


def promote_release_decision(existing: Any, expected: Any) -> PromoteDecision:
    """Decide whether the release tag may be minted from the candidate tag.

    ``existing``: ``{"candidateDigest": <sha256:...>|None, "releaseDigest":
    <sha256:...>|None}`` where ``None`` means the tag is absent in ECR.
    ``expected``: ``{version, sourceSha, repository, imageDigest,
    candidateTag, releaseTag}`` from the validated manifest/evidence.

    Rules (fail closed):
    - the expected record must be internally consistent (SemVer/SHA/digest/tags);
    - the candidate tag must exist and resolve to the recorded digest
      (never promote bytes ECR does not have or that differ from evidence);
    - a release tag that already resolves to the recorded digest is an
      idempotent resume (``reuse``), never a re-mint;
    - a release tag resolving to anything else is a conflict that fails closed
      (immutable tags must never be overwritten, even by accident).
    """
    issues, norm = _valid_expected(expected)
    if norm is None:
        return PromoteDecision("mint", issues)

    if not isinstance(existing, dict):
        issues.append(_issue("MISSING_EXISTING", "$", "no existing ECR state was provided"))
        return PromoteDecision("mint", issues)

    candidate_digest = existing.get("candidateDigest")
    release_digest = existing.get("releaseDigest")

    if not isinstance(candidate_digest, str) or not _IMAGE_DIGEST_RE.match(candidate_digest):
        issues.append(
            _issue(
                "CANDIDATE_TAG_MISSING",
                "candidateDigest",
                f"candidate tag {norm['candidateTag']} is absent or invalid in ECR",
            )
        )
    elif candidate_digest != norm["imageDigest"]:
        issues.append(
            _issue(
                "CANDIDATE_DIGEST_MISMATCH",
                "candidateDigest",
                f"candidate tag resolves to {candidate_digest}, expected {norm['imageDigest']}",
            )
        )

    if release_digest is None:
        action = "mint"
    elif isinstance(release_digest, str) and _IMAGE_DIGEST_RE.match(release_digest):
        if release_digest == norm["imageDigest"]:
            action = "reuse"
        else:
            action = "mint"
            issues.append(
                _issue(
                    "RELEASE_TAG_CONFLICT",
                    "releaseDigest",
                    f"release tag {norm['releaseTag']} already exists at {release_digest}, "
                    f"expected {norm['imageDigest']}; immutable tags are never overwritten",
                )
            )
    else:
        issues.append(
            _issue(
                "RELEASE_TAG_INVALID",
                "releaseDigest",
                f"release tag resolves to an invalid digest {release_digest!r}",
            )
        )

    return PromoteDecision(action, issues)


def verify_release_digest(existing: Any, expected: Any) -> PromoteDecision:
    """Post-mutation read-back: both tags must resolve to the recorded digest.

    Same ``existing``/``expected`` shapes as :func:`promote_release_decision`.
    Any mismatch fails closed; the recorded digest is authoritative and is never
    inferred from the tag strings.
    """
    issues, norm = _valid_expected(expected)
    if norm is None:
        return PromoteDecision("mint", issues)
    if not isinstance(existing, dict):
        issues.append(_issue("MISSING_EXISTING", "$", "no existing ECR state was provided"))
        return PromoteDecision("mint", issues)

    candidate_digest = existing.get("candidateDigest")
    release_digest = existing.get("releaseDigest")
    if not isinstance(candidate_digest, str) or candidate_digest != norm["imageDigest"]:
        issues.append(
            _issue(
                "CANDIDATE_DIGEST_MISMATCH",
                "candidateDigest",
                f"candidate tag resolves to {candidate_digest!r}, expected {norm['imageDigest']}",
            )
        )
    if not isinstance(release_digest, str) or release_digest != norm["imageDigest"]:
        issues.append(
            _issue(
                "RELEASE_DIGEST_MISMATCH",
                "releaseDigest",
                f"release tag resolves to {release_digest!r}, expected {norm['imageDigest']}",
            )
        )
    return PromoteDecision("reuse", issues)


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _cmd_decide(args: argparse.Namespace) -> int:
    existing = _read_json(args.existing)
    expected = _read_json(args.expected)
    decision = promote_release_decision(existing, expected)
    _print_json({"action": decision.action, "issues": decision.issues})
    return 0 if not decision.issues else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    existing = _read_json(args.existing)
    expected = _read_json(args.expected)
    decision = verify_release_digest(existing, expected)
    _print_json({"valid": not decision.issues, "issues": decision.issues})
    return 0 if not decision.issues else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.ecr",
        description="Server-side ECR release tag promotion rules.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    decide = sub.add_parser("decide", help="decide mint / reuse / fail-closed")
    decide.add_argument(
        "--existing",
        required=True,
        metavar="JSON",
        help="existing {candidateDigest, releaseDigest} JSON file",
    )
    decide.add_argument(
        "--expected",
        required=True,
        metavar="JSON",
        help=(
            "expected {version, sourceSha, repository, imageDigest, candidateTag, "
            "releaseTag} JSON file"
        ),
    )
    decide.set_defaults(func=_cmd_decide)

    verify = sub.add_parser("verify", help="verify both tags resolve to the recorded digest")
    verify.add_argument(
        "--existing",
        required=True,
        metavar="JSON",
        help="existing {candidateDigest, releaseDigest} JSON file",
    )
    verify.add_argument(
        "--expected", required=True, metavar="JSON", help="expected record JSON file"
    )
    verify.set_defaults(func=_cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
