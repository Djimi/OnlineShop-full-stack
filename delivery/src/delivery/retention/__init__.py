"""ECR lifecycle policy asset, fail-closed validator, and first-match-wins model.

Retention rules learned from the legacy Pass 3 implementation, applied in NEW
code only:

- ECR evaluates lifecycle rules by priority and an image is expired by
  exactly one or zero rules. An image whose tags match a higher-priority
  rule's tag selection is CLAIMED by that rule: the rule's count/age
  condition decides expire-vs-retain, and lower-priority rules never apply to
  it (first-match-wins). A multi-tag release image (sha-* + release-*) inside
  the newest 10 is therefore retained by rule 1 and the candidate rules never
  touch it.
- ECR documents that a multi-entry tagPrefixList selects only images carrying
  ALL the listed tags ("only the images with all specified tags are
  selected"), so a merged candidate-family rule would silently select
  nothing: every tagged rule carries exactly one prefix and the validator
  rejects merged lists (POLICY_TAGPREFIX_MULTI).
- ECR requires an explicit tagPrefixList on every tagged rule; a generic
  negative/exclusion filter is not expressible, so the candidate families are
  enumerated one rule each and any other tag is retained (fail-safe).
- ECR lifecycle evaluation is delayed (up to 24 hours) and images referenced
  by manifest lists or OCI referrers are not selected; operators preview with
  start/get-lifecycle-policy-preview before applying.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..errors import PolicyTagPrefixMulti, PolicyValidationError, ReadError, ValidationError

POLICY_ASSET = Path(__file__).parent / "ecr-lifecycle-policy.json"

_RELEASE_PREFIX = "release-"
_KEEP_RELEASE_IMAGES = 10

_DESIRED_RULES = (
    ("release-", "imageCountMoreThan", 10),
    ("sha-", "sinceImagePushed", 30),
    ("main-latest", "sinceImagePushed", 30),
    ("branch-", "sinceImagePushed", 30),
)
_DESIRED_UNTAGGED_DAYS = 14


def load_policy_text(path: str | None) -> tuple[str, dict, str]:
    """Resolve the policy JSON from ``--policy`` or the bundled desired asset.

    Returns ``(text, parsed, kind)`` where kind is ``desired`` for the bundled
    asset and ``provided`` for an operator-supplied file. The text is the
    canonical JSON encoding of the validated policy: apply sends exactly these
    bytes and the read-back compares them byte-for-byte, so file formatting
    never affects drift detection.
    """
    if path is None:
        kind = "desired"
        try:
            raw = POLICY_ASSET.read_text()
        except OSError as error:
            raise ReadError(f"cannot read the bundled policy asset: {error}") from error
    else:
        kind = "provided"
        try:
            raw = Path(path).read_text()
        except OSError as error:
            raise ValidationError(f"cannot read policy file {path}: {error}") from error
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PolicyValidationError(f"policy is not valid JSON: {error}") from error
    validate_policy(parsed)
    if kind == "desired":
        validate_desired_policy(parsed)
    return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False), parsed, kind


def validate_policy(policy: dict) -> None:
    """Fail-closed structural validation of an ECR lifecycle policy."""
    if not isinstance(policy, dict):
        raise PolicyValidationError("lifecycle policy must be a JSON object")
    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        raise PolicyValidationError("lifecycle policy must contain a non-empty rules list")
    if not all(isinstance(rule, dict) for rule in rules):
        raise PolicyValidationError("each lifecycle rule must be a JSON object")
    priorities = []
    for rule in rules:
        priority = rule.get("rulePriority")
        if isinstance(priority, bool) or not isinstance(priority, int) or priority <= 0:
            raise PolicyValidationError("rulePriority must be a positive integer")
        priorities.append(priority)
        if not isinstance(rule.get("description"), str) or not rule["description"]:
            raise PolicyValidationError(f"rule {priority} must have a non-empty description")
        selection = rule.get("selection")
        if not isinstance(selection, dict):
            raise PolicyValidationError(f"rule {priority} must have a selection object")
        tag_status = selection.get("tagStatus")
        if tag_status not in ("tagged", "untagged"):
            raise PolicyValidationError(f"rule {priority} tagStatus must be tagged or untagged")
        prefix_list = selection.get("tagPrefixList")
        if tag_status == "tagged":
            if not isinstance(prefix_list, list) or not prefix_list:
                raise PolicyValidationError(
                    f"rule {priority}: tagged rules must carry an explicit tagPrefixList"
                )
            if len(prefix_list) != 1:
                raise PolicyTagPrefixMulti(
                    f"rule {priority}: tagPrefixList must contain exactly one prefix; "
                    "ECR selects only images carrying ALL listed tags, so a merged list "
                    "would silently select nothing"
                )
            if not isinstance(prefix_list[0], str) or not prefix_list[0]:
                raise PolicyValidationError(
                    f"rule {priority} tag prefix must be a non-empty string"
                )
        elif prefix_list is not None:
            raise PolicyValidationError(
                f"rule {priority}: untagged rules must not carry tagPrefixList"
            )
        count_type = selection.get("countType")
        count_number = selection.get("countNumber")
        if isinstance(count_number, bool) or not isinstance(count_number, int) or count_number <= 0:
            raise PolicyValidationError(f"rule {priority} countNumber must be a positive integer")
        if count_type == "imageCountMoreThan":
            if selection.get("countUnit") is not None:
                raise PolicyValidationError(
                    f"rule {priority}: imageCountMoreThan must not carry countUnit"
                )
        elif count_type == "sinceImagePushed":
            if selection.get("countUnit") != "days":
                raise PolicyValidationError(
                    f"rule {priority}: sinceImagePushed requires countUnit days"
                )
        else:
            raise PolicyValidationError(
                f"rule {priority} countType must be imageCountMoreThan or sinceImagePushed"
            )
        if rule.get("action") != {"type": "expire"}:
            raise PolicyValidationError(
                f"rule {priority} action must be exactly {{'type': 'expire'}}"
            )
    if priorities != list(range(1, len(priorities) + 1)):
        raise PolicyValidationError("rulePriority values must be consecutive starting at 1")


def validate_desired_policy(policy: dict) -> None:
    """Assert the bundled desired policy's retention properties (AD-16).

    Rule 1 keeps the newest 10 release-* images (HIGHEST priority); rules 2-4
    expire the sha-*, main-latest, and branch-* candidate families after 30
    days, one single-prefix rule each; rule 5 expires untagged images after a
    14-day grace period.
    """
    rules = policy["rules"]
    if len(rules) != 5:
        raise PolicyValidationError("desired policy must have exactly 5 rules")
    for index, (prefix, count_type, count_number) in enumerate(_DESIRED_RULES):
        rule = rules[index]
        selection = rule["selection"]
        if selection["tagStatus"] != "tagged" or selection["tagPrefixList"] != [prefix]:
            raise PolicyValidationError(
                f"desired rule {index + 1} must select tagged images with prefix {prefix!r}"
            )
        if selection["countType"] != count_type or selection["countNumber"] != count_number:
            raise PolicyValidationError(
                f"desired rule {index + 1} must use {count_type} {count_number}"
            )
        if count_type == "sinceImagePushed" and selection["countUnit"] != "days":
            raise PolicyValidationError(f"desired rule {index + 1} must use days")
    untagged = rules[4]["selection"]
    if untagged != {
        "tagStatus": "untagged",
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "countNumber": _DESIRED_UNTAGGED_DAYS,
    }:
        raise PolicyValidationError(
            f"desired rule 5 must expire untagged images after {_DESIRED_UNTAGGED_DAYS} days"
        )


def _tag_selection_matches(selection: dict, tags: list[str]) -> bool:
    if selection["tagStatus"] == "untagged":
        return not tags
    prefix = selection["tagPrefixList"][0]
    return any(tag.startswith(prefix) for tag in tags)


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    raise PolicyValidationError(f"image pushedAt must be a datetime, got {type(value).__name__}")


def model_expirations(
    policy: dict, images: list[dict], reference: datetime | None = None
) -> list[dict]:
    """Model the policy with ECR's first-match-wins semantics.

    Returns one decision per image: ``{"digest", "tags", "expire",
    "rulePriority"}`` where rulePriority is None when no rule claims the image
    (retained). An image is claimed by the first (highest-priority) rule whose
    tag selection matches; the rule's count/age condition then decides
    expire-vs-retain and lower-priority rules never apply to that image.
    """
    rules = sorted(policy["rules"], key=lambda rule: rule["rulePriority"])
    reference = reference if reference is not None else datetime.now(UTC)
    decisions = []
    for image in images:
        claimed = None
        for rule in rules:
            if _tag_selection_matches(rule["selection"], image["tags"]):
                claimed = rule
                break
        expire = False
        if claimed is not None:
            selection = claimed["selection"]
            if selection["countType"] == "imageCountMoreThan":
                matching = [
                    img for img in images if _tag_selection_matches(selection, img["tags"])
                ]
                ranked = sorted(
                    matching,
                    key=lambda img: (_to_datetime(img["pushedAt"]), img["digest"]),
                    reverse=True,
                )
                position = [img["digest"] for img in ranked].index(image["digest"]) + 1
                expire = position > selection["countNumber"]
            else:
                pushed = _to_datetime(image["pushedAt"])
                expire = (reference - pushed).total_seconds() >= selection["countNumber"] * 86400
        decisions.append(
            {
                "digest": image["digest"],
                "tags": list(image["tags"]),
                "expire": expire,
                "rulePriority": claimed["rulePriority"] if claimed is not None else None,
            }
        )
    return decisions


def protected_release_tags(window_release_ids: list[str], images: list[dict]) -> list[str]:
    """The protected release-* tag set for one repository.

    A tag is protected when it belongs to a window release (current + up to
    three previous) or when it is a release-* tag on an image inside the
    newest-10 keep margin of rule 1.
    """
    release_tagged = [
        image
        for image in images
        if any(tag.startswith(_RELEASE_PREFIX) for tag in image["tags"])
    ]
    newest = sorted(
        release_tagged,
        key=lambda image: (_to_datetime(image["pushedAt"]), image["digest"]),
        reverse=True,
    )[: _KEEP_RELEASE_IMAGES]
    protected = set(window_release_ids)
    for image in newest:
        protected.update(tag for tag in image["tags"] if tag.startswith(_RELEASE_PREFIX))
    return sorted(protected)
