"""ECR lifecycle retention and rollback-window enforcement (Pass 3, subphase 3.8).

Subphase 3.8 keeps the immediate 10-release rollback window in ECR and S3 and
expires everything else on a documented schedule. The desired lifecycle policy
(``release/ecr/lifecycle-policy.json``) is applied identically to every backend
repository:

- rule priority 1 — keep the most recent 10 ``release-*`` images
  (``tagged`` + ``tagPrefixList: ["release-"]`` + ``imageCountMoreThan 10``);
- rule priority 2 — expire ``sha-*`` candidates approximately 30 days after push;
- rule priority 3 — expire the mutable ``main-latest`` convenience tag after 30 days;
- rule priority 4 — expire mutable ``branch-*`` convenience tags after 30 days;
- rule priority 5 — expire untagged images after a 14-day grace period.

ECR's evaluator semantics are modeled exactly as documented by AWS:

- every rule is evaluated against every image; an image is expired or kept by
  **exactly one or zero rules**;
- an image that matches a higher-priority rule's tagging requirements can
  **never** be expired by a lower-priority rule — even when the higher-priority
  rule keeps it (e.g. a ``release-*`` image inside the newest 10 is claimed by
  rule 1 and the candidate rules never apply to it);
- expiration is ordered by ``imagePushedAt``, older images first.

A lifecycle rule is therefore never modeled as a generic negative/exclusion
filter ("expire everything except releases"): ECR requires every ``tagged``
rule to carry an explicit ``tagPrefixList``/``tagPatternList`` (a bare
``tagStatus: tagged`` rule is rejected by the service), so the candidate
families (``sha-*``, ``main-latest``, ``branch-*``) MUST be enumerated and the
multi-tag protection of official digests (which carry both ``sha-*`` and
``release-*``) comes solely from rule ordering. Every tagged rule selects
**exactly one** tag prefix: AWS documents that a multi-entry
``tagPrefixList``/``tagPatternList`` selects only images carrying ALL the
listed tags ("only the images with all specified tags are selected"), so a
merged list would silently select nothing and would diverge from the model —
``policy_issues`` rejects any multi-entry prefix list
(``POLICY_TAGPREFIX_MULTI``), and each candidate family gets its own
single-prefix rule, which is unambiguous under the documented semantics.
``policy_issues`` rejects any policy that would break the ordering (keep-10 not
first, ``tagStatus: any``, ``excludeTaggedImages``, a tagged rule without a
prefix list, a second untagged selector, duplicate tag prefixes, an uncovered
candidate family).

The module provides:

- ``policy_issues``           — validate the desired lifecycle policy document;
- ``evaluate_images``         — model ECR's first-match-wins evaluation for a
                                repository state (the offline preview: the exact
                                candidate image IDs/tags a rule would expire);
- ``preview_issues``          — validate ECR's own
                                ``start/get-lifecycle-policy-preview`` results
                                against the modeled evaluation; a disagreement
                                or a protected image expiring fails closed;
- ``audit_rollback_window``   — the read-only retention audit: the exact 10
                                (or all when fewer exist) immediately
                                rollback-capable releases, failing when any
                                required backend/frontend artifact is missing
                                (never claims an older metadata-only release);
- ``policy_coverage_issues``  — cross-check that the push-order keep-10
                                protects every version-order window release;
- ``frontend_retention_issues`` — the S3 prefix retention plan model: the
                                ``_releases/v<version>/`` window is never
                                deleted for the currently deployed or previous
                                known-good release; GitHub Release assets are
                                the long-term source after the window expires;
- ``retention_classes_issues`` — the GitHub artifact retention classes:
                                releases/manifests/SBOMs/checksums/audit
                                evidence are indefinite, candidate-only
                                artifacts 30 days, staging-failure diagnostics
                                and release result records per their shorter
                                operational retention.

All functions are pure and fixture-tested; shell wrappers
(``release/bin/audit-retention-window.sh``,
``release/bin/preview-retention-policy.sh``,
``release/bin/apply-retention-policy.sh``) gather state and pass validated JSON
files. ECR lifecycle evaluation is delayed (a lifecycle evaluation can take up
to 24 hours to run) and images referenced by manifest lists or OCI referrers
are not selected by lifecycle rules — see
``explanations/RETENTION-DECISIONS.md``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from . import components as rc
from .rollback import ROLLBACK_WINDOW, release_artifacts_issues
from .traceability import _observed_error, validate_index

# The retention contract (single source of truth, mirrors the desired policy).
KEEP_RELEASE_COUNT = 10
CANDIDATE_RETENTION_DAYS = 30
UNTAGGED_GRACE_DAYS = 14
RELEASE_TAG_PREFIX = "release-"
# The non-official candidate tag families the policy must enumerate (ECR
# requires every tagged rule to carry an explicit tagPrefixList, so a generic
# "expire everything else" rule is not expressible — the families below are
# covered by rules 2 and 3 of the desired policy).
CANDIDATE_TAG_FAMILIES = ("sha-", "main-latest", "branch-")
DELAYED_EVALUATION_NOTE = (
    "ECR lifecycle evaluation is delayed: a policy change is applied by ECR's "
    "periodic evaluator and a lifecycle evaluation can take up to 24 hours to "
    "run; the audit/preview never assume an immediate effect"
)

# GitHub artifact retention classes (subphase 3.8, checkbox 5). The long-term
# store is the GitHub Release (manifest, SBOMs, checksums, sanitized audit/test
# evidence — indefinite); candidate-only artifacts and operational diagnostics
# have shorter retention configured in the workflows.
RETENTION_CLASSES: dict[str, Any] = {
    "github-release": "indefinite",
    "release-manifest": "indefinite",
    "sbom": "indefinite",
    "checksum": "indefinite",
    "audit-evidence": "indefinite",
    "candidate-artifact": 30,
    "staging-failure-diagnostics": 14,
    "rollback-result": 14,
}

_FRONTEND_PREFIX_RE = re.compile(r"^_releases/v([0-9]+\.[0-9]+\.[0-9]+)/$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass
class RetentionDecision:
    """Result of a retention decision.

    ``valid`` is False when any issue exists. ``data`` carries the
    machine-readable evaluation/audit result; a non-empty ``issues`` list means
    fail closed regardless of ``data``.
    """

    valid: bool
    data: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "data": self.data, "issues": self.issues}


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


def _parse_ts(value: Any) -> datetime:
    """Parse an ISO-8601 timestamp (``Z`` suffix tolerated; Python 3.10)."""
    if not isinstance(value, str):
        raise ValueError(f"invalid timestamp {value!r}")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Lifecycle policy validation (desired state)
# ---------------------------------------------------------------------------


def _rule_key(rule: Any) -> int | None:
    if not isinstance(rule, dict):
        return None
    priority = rule.get("rulePriority")
    return priority if isinstance(priority, int) else None


def policy_issues(policy: Any) -> RetentionDecision:
    """Validate the desired ECR lifecycle policy document.

    Enforced rules (fail closed; the policy is the desired state, so the exact
    layout is required):

    - ``rules`` is a non-empty list of ``{rulePriority, description,
      selection, action: {type: expire}}`` with unique integer priorities;
    - rule priority 1 is the ``release-*`` keep-10 rule (``tagged`` +
      ``tagPrefixList: ["release-"]`` + ``imageCountMoreThan 10``) — the
      keep-10 rule has the HIGHEST priority so retained multi-tag release
      images can never be selected by a lower-priority candidate rule;
    - every ``tagged`` rule must carry an explicit ``tagPrefixList`` (ECR
      schema: a bare ``tagStatus: tagged`` selection is rejected by the
      service — a generic "expire everything else" rule is not expressible);
    - the candidate families (``sha-``, ``main-latest``, ``branch-``) are each
      covered by a ``tagged`` + ``sinceImagePushed`` rule expiring after
      ``CANDIDATE_RETENTION_DAYS``, ordered after the keep-10 rule;
    - the single untagged rule is last and expires after ``UNTAGGED_GRACE_DAYS``;
    - no ``tagStatus: any`` (ambiguous selection), no ``excludeTaggedImages``
      (generic negative/exclusion semantics are never used), one untagged
      selector only, unique tag prefixes, and every ``tagged`` rule selects
      exactly ONE tag prefix (AWS documents that a multi-entry
      ``tagPrefixList`` selects only images carrying ALL the listed tags, so a
      merged list would silently select nothing).
    """
    issues: list[dict[str, str]] = []
    if not isinstance(policy, dict) or not isinstance(policy.get("rules"), list):
        return RetentionDecision(
            False,
            {},
            [_issue("POLICY_INVALID", "policy", "policy must be {rules: [...]}")],
        )
    rules = policy["rules"]
    if not rules:
        return RetentionDecision(
            False, {}, [_issue("POLICY_EMPTY", "policy.rules", "policy has no rules")]
        )

    priorities: list[int] = []
    tagged_prefixes: set[str] = set()
    untagged_count = 0
    release_rule_index: int | None = None
    candidate_rule_indexes: list[int] = []
    untagged_rule_index: int | None = None

    for position, rule in enumerate(rules):
        field_path = f"policy.rules[{position}]"
        if not isinstance(rule, dict):
            issues.append(_issue("POLICY_RULE_INVALID", field_path, "rule is not an object"))
            continue
        priority = rule.get("rulePriority")
        if not isinstance(priority, int) or priority < 1:
            issues.append(
                _issue(
                    "POLICY_RULE_PRIORITY_INVALID",
                    f"{field_path}.rulePriority",
                    f"rulePriority must be a positive integer, got {priority!r}",
                )
            )
            continue
        if priority in priorities:
            issues.append(
                _issue(
                    "POLICY_RULE_PRIORITY_INVALID",
                    f"{field_path}.rulePriority",
                    f"duplicate rulePriority {priority}; priorities must be unique",
                )
            )
        priorities.append(priority)
        action = rule.get("action")
        if not isinstance(action, dict) or action.get("type") != "expire":
            issues.append(
                _issue(
                    "POLICY_UNSUPPORTED_ACTION",
                    f"{field_path}.action",
                    f"action must be {{type: expire}}, got {action!r}",
                )
            )
        selection = rule.get("selection")
        if not isinstance(selection, dict):
            issues.append(
                _issue("POLICY_RULE_INVALID", f"{field_path}.selection", "selection missing")
            )
            continue
        tag_status = selection.get("tagStatus")
        count_type = selection.get("countType")
        count_number = selection.get("countNumber")
        if tag_status not in ("tagged", "untagged"):
            issues.append(
                _issue(
                    "POLICY_AMBIGUOUS_SELECTION",
                    f"{field_path}.selection.tagStatus",
                    f"tagStatus {tag_status!r} must be tagged or untagged; "
                    "'any' and negative/exclusion semantics are never used",
                )
            )
        if selection.get("excludeTaggedImages") is not None:
            issues.append(
                _issue(
                    "POLICY_EXCLUSION_FILTER",
                    f"{field_path}.selection.excludeTaggedImages",
                    "excludeTaggedImages is a generic negative/exclusion filter "
                    "and is never used; protection comes from rule ordering",
                )
            )
        tag_prefixes = selection.get("tagPrefixList")
        if tag_prefixes is not None:
            if tag_status != "tagged":
                issues.append(
                    _issue(
                        "POLICY_TAGPREFIX_ON_NON_TAGGED",
                        f"{field_path}.selection.tagPrefixList",
                        "tagPrefixList is only valid with tagStatus tagged",
                    )
                )
            if not isinstance(tag_prefixes, list) or not all(
                isinstance(prefix, str) for prefix in tag_prefixes
            ):
                issues.append(
                    _issue(
                        "POLICY_RULE_INVALID",
                        f"{field_path}.selection.tagPrefixList",
                        "tagPrefixList must be a list of strings",
                    )
                )
                tag_prefixes = []
            for prefix in tag_prefixes or []:
                if prefix in tagged_prefixes:
                    issues.append(
                        _issue(
                            "POLICY_PREFIX_OVERLAP",
                            f"{field_path}.selection.tagPrefixList",
                            f"tag prefix {prefix!r} is selected by more than one rule",
                        )
                    )
                tagged_prefixes.add(prefix)
            if len(tag_prefixes or []) > 1:
                # AWS documents that a multi-entry tagPrefixList/tagPatternList
                # selects only images carrying ALL the listed tags ("only the
                # images with all specified tags are selected") — a merged list
                # would silently select nothing (and diverge from the model's
                # any-match selection). Each family gets its own single-prefix
                # rule, which is unambiguous under the documented semantics.
                issues.append(
                    _issue(
                        "POLICY_TAGPREFIX_MULTI",
                        f"{field_path}.selection.tagPrefixList",
                        f"a tagged rule must select exactly one tag prefix, got "
                        f"{len(tag_prefixes)}; AWS's all-specified-tags semantics "
                        "make a merged multi-prefix rule select nothing — enumerate "
                        "each candidate family as its own rule",
                    )
                )
        elif tag_status == "tagged":
            # ECR schema: a tagged rule without tagPrefixList/tagPatternList is
            # rejected by the service — a generic age rule over ALL tagged
            # images (a negative/exclusion filter in disguise) is impossible,
            # which is exactly why the candidate families must be enumerated.
            issues.append(
                _issue(
                    "POLICY_TAGPREFIX_REQUIRED",
                    f"{field_path}.selection",
                    "a tagged rule must specify tagPrefixList (ECR schema); candidate "
                    "families are enumerated, never a generic exclusion",
                )
            )
        if tag_status == "untagged":
            untagged_count += 1
            untagged_rule_index = position
        if count_type == "imageCountMoreThan":
            if not isinstance(count_number, int) or count_number < 1:
                issues.append(
                    _issue(
                        "POLICY_RULE_INVALID",
                        f"{field_path}.selection.countNumber",
                        f"imageCountMoreThan requires a positive countNumber, got {count_number!r}",
                    )
                )
            if tag_status == "tagged" and tag_prefixes and RELEASE_TAG_PREFIX in tag_prefixes:
                release_rule_index = position
                if count_number != KEEP_RELEASE_COUNT:
                    issues.append(
                        _issue(
                            "POLICY_RELEASE_RULE_MISCONFIGURED",
                            f"{field_path}.selection.countNumber",
                            f"the release keep rule must keep {KEEP_RELEASE_COUNT} images, "
                            f"got countNumber {count_number}",
                        )
                    )
        elif count_type == "sinceImagePushed":
            count_unit = selection.get("countUnit")
            if count_unit != "days" or not isinstance(count_number, int) or count_number < 1:
                issues.append(
                    _issue(
                        "POLICY_RULE_INVALID",
                        f"{field_path}.selection",
                        "sinceImagePushed requires countUnit days and a positive countNumber",
                    )
                )
            if tag_status == "tagged":
                candidate_rule_indexes.append(position)
                if count_number != CANDIDATE_RETENTION_DAYS:
                    issues.append(
                        _issue(
                            "POLICY_CANDIDATE_RULE_MISCONFIGURED",
                            f"{field_path}.selection.countNumber",
                            f"non-official candidates must expire after "
                            f"{CANDIDATE_RETENTION_DAYS} days, got countNumber {count_number}",
                        )
                    )
            elif tag_status == "untagged" and count_number != UNTAGGED_GRACE_DAYS:
                issues.append(
                    _issue(
                        "POLICY_UNTAGGED_RULE_MISCONFIGURED",
                        f"{field_path}.selection.countNumber",
                        f"untagged images must expire after {UNTAGGED_GRACE_DAYS} days "
                        "(documented grace period), got countNumber {count_number}",
                    )
                )
        else:
            issues.append(
                _issue(
                    "POLICY_RULE_INVALID",
                    f"{field_path}.selection.countType",
                    f"countType {count_type!r} must be imageCountMoreThan or sinceImagePushed",
                )
            )

    if untagged_count != 1:
        issues.append(
            _issue(
                "POLICY_UNTAGGED_RULE_COUNT",
                "policy.rules",
                f"exactly one rule may select untagged images, got {untagged_count}",
            )
        )
    if release_rule_index != 0:
        issues.append(
            _issue(
                "POLICY_RELEASE_RULE_NOT_FIRST",
                "policy.rules",
                "the release-* keep-10 rule must have the HIGHEST priority (rulePriority 1): "
                "a retained multi-tag release image must never be selectable by a "
                "lower-priority candidate rule (ECR first-match-wins semantics)",
            )
        )
    # Every candidate family must be enumerated by a tagged age rule.
    covered_families = {
        prefix
        for rule in rules
        if isinstance(rule, dict)
        and isinstance(rule.get("selection"), dict)
        and rule["selection"].get("tagStatus") == "tagged"
        for prefix in rule["selection"].get("tagPrefixList") or []
    }
    for family in CANDIDATE_TAG_FAMILIES:
        if not any(
            family == prefix or prefix.startswith(family) or family.startswith(prefix)
            for prefix in covered_families
        ):
            issues.append(
                _issue(
                    "POLICY_CANDIDATE_RULE_MISCONFIGURED",
                    "policy.rules",
                    f"candidate tag family {family!r} is not enumerated by any 30-day "
                    "candidate rule",
                )
            )
    if untagged_rule_index is not None and untagged_rule_index != len(rules) - 1:
        issues.append(
            _issue(
                "POLICY_RULE_ORDER_INVALID",
                "policy.rules",
                "the untagged grace rule must be last (priority N)",
            )
        )
    if (
        candidate_rule_indexes
        and untagged_rule_index is not None
        and not all(
            release_rule_index < index < untagged_rule_index for index in candidate_rule_indexes
        )
    ):
        issues.append(
            _issue(
                "POLICY_RULE_ORDER_INVALID",
                "policy.rules",
                "rule order must be keep-10 release rule, candidate age rules, untagged "
                "grace rule (priorities 1, 2, ..., N)",
            )
        )

    return RetentionDecision(not issues, {}, issues)


# ---------------------------------------------------------------------------
# ECR first-match-wins evaluation model
# ---------------------------------------------------------------------------


def _selection_matches(selection: Any, tags: list[str]) -> bool:
    """ECR selection matching: an image matches when ANY of its tags matches."""
    tag_status = selection.get("tagStatus")
    if tag_status == "untagged":
        return len(tags) == 0
    if tag_status == "tagged":
        if not tags:
            return False
        prefixes = selection.get("tagPrefixList")
        if prefixes:
            return any(tag.startswith(prefix) for tag in tags for prefix in prefixes)
        return True
    return True


def _norm_images(images: Any) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    """Normalize the images input into ``{repository: [image, ...]}``.

    Accepts the observed ECR shape ``{repo: {images: [...]}, ...}`` or a bare
    image list. An image record needs an ``imageDigest`` and an
    ``imagePushedAt`` (ECR orders expiration by pushed-at time); anything else
    fails closed so a preview/audit is never computed from partial data.
    """
    issues: list[dict[str, str]] = []
    repos: dict[str, list[dict[str, Any]]] = {}
    if isinstance(images, dict):
        for repo, entry in images.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("images"), list):
                issues.append(
                    _issue("INVALID_IMAGE_STATE", f"ecr.{repo}", "expected {images: [...]}")
                )
                continue
            repos[repo] = entry["images"]
    elif isinstance(images, list):
        repos["_"] = images
    else:
        return {}, [
            _issue("INVALID_IMAGE_STATE", "images", "images input must be an object or list")
        ]

    for repo, records in repos.items():
        for index, record in enumerate(records):
            field_path = f"ecr.{repo}.images[{index}]"
            if not isinstance(record, dict):
                issues.append(_issue("INVALID_IMAGE_RECORD", field_path, "image is not an object"))
                continue
            digest = record.get("imageDigest")
            if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
                issues.append(
                    _issue(
                        "INVALID_IMAGE_RECORD",
                        f"{field_path}.imageDigest",
                        f"invalid digest {digest!r}",
                    )
                )
            pushed = record.get("imagePushedAt")
            if not pushed:
                issues.append(
                    _issue(
                        "INVALID_IMAGE_RECORD",
                        f"{field_path}.imagePushedAt",
                        "imagePushedAt is required (ECR orders expiration by pushed-at time)",
                    )
                )
            tags = record.get("imageTags") or []
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                issues.append(
                    _issue(
                        "INVALID_IMAGE_RECORD",
                        f"{field_path}.imageTags",
                        "imageTags must be a list of strings",
                    )
                )
    return repos, issues


def _rules_sorted(policy: Any) -> list[dict[str, Any]]:
    return sorted(policy.get("rules", []), key=_rule_key)


def evaluate_images(
    policy: Any, images: Any, reference_date: str | datetime | None = None
) -> RetentionDecision:
    """Model ECR's first-match-wins evaluation for one repository state.

    ``images``: ``{repository: {images: [...]}}`` (the observed ECR state).
    ``reference_date``: ISO timestamp the age rules are evaluated against
    (defaults to the current UTC time); fixtures pass a fixed date so the
    offline preview is deterministic.

    For every image the HIGHEST-priority rule whose selection matches the
    image's tags claims it (exactly one or zero rules apply):

    - ``imageCountMoreThan`` — among the images claimed by the rule, the
      newest ``countNumber`` by ``imagePushedAt`` are kept, older ones expire;
    - ``sinceImagePushed`` — a claimed image expires when it was pushed more
      than ``countNumber`` days before the reference date.

    The per-image result records ``action`` (``expire``/``keep``) and the
    ``appliedRulePriority``: a multi-tag release image claimed by the keep-10
    rule keeps priority 1 even when an age rule would match its tags — ECR
    never lets a lower-priority rule expire it.
    """
    policy_decision = policy_issues(policy)
    if not policy_decision.valid:
        return RetentionDecision(False, {}, policy_decision.issues)

    repos, image_issues = _norm_images(images)
    if image_issues:
        return RetentionDecision(False, {}, image_issues)

    reference = (
        _parse_ts(reference_date)
        if isinstance(reference_date, str)
        else (
            reference_date if isinstance(reference_date, datetime) else datetime.now(timezone.utc)
        )
    )
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    rules = _rules_sorted(policy)
    rule_by_priority = {_rule_key(rule): rule for rule in rules if _rule_key(rule) is not None}
    per_repo: dict[str, Any] = {}
    issues: list[dict[str, str]] = []

    for repo, records in repos.items():
        try:
            claimed = [
                (_claimed_rule(rules, record.get("imageTags") or []), record) for record in records
            ]
        except ValueError as exc:
            issues.append(_issue("INVALID_IMAGE_RECORD", f"ecr.{repo}", str(exc)))
            continue

        rule_to_records: dict[int | None, list[dict[str, Any]]] = {}
        for rule, record in claimed:
            rule_to_records.setdefault(_rule_key(rule), []).append(record)

        evaluated: list[dict[str, Any]] = []
        for rule, records_in_rule in rule_to_records.items():
            if rule is None:
                for record in records_in_rule:
                    evaluated.append(
                        {
                            "imageDigest": record.get("imageDigest"),
                            "imageTags": sorted(record.get("imageTags") or []),
                            "imagePushedAt": record.get("imagePushedAt"),
                            "appliedRulePriority": None,
                            "ruleDescription": "no matching rule",
                            "action": "keep",
                            "reason": "no lifecycle rule selects this image",
                        }
                    )
                continue
            rule_obj = rule_by_priority.get(rule)
            if rule_obj is None:
                continue
            selection = rule_obj.get("selection", {})
            if selection.get("countType") == "imageCountMoreThan":
                count = selection.get("countNumber")
                ordered = sorted(
                    records_in_rule,
                    key=lambda record: _parse_ts(record.get("imagePushedAt")),
                )
                keep = max(0, len(ordered) - count)
                for index, record in enumerate(ordered):
                    expiring = index < keep
                    evaluated.append(
                        {
                            "imageDigest": record.get("imageDigest"),
                            "imageTags": sorted(record.get("imageTags") or []),
                            "imagePushedAt": record.get("imagePushedAt"),
                            "appliedRulePriority": rule,
                            "ruleDescription": rule_obj.get("description", ""),
                            "action": "expire" if expiring else "keep",
                            "reason": (
                                f"rule {rule} keeps the newest {count} images; this image is "
                                f"{index + 1} of {len(ordered)} by push order"
                                if expiring
                                else f"rule {rule} keeps the newest {count} images; this image "
                                f"is within them (push position {index + 1})"
                            ),
                        }
                    )
            else:
                days = selection.get("countNumber")
                cutoff = reference - timedelta(days=days)
                for record in records_in_rule:
                    pushed = _parse_ts(record.get("imagePushedAt"))
                    if pushed.tzinfo is None:
                        pushed = pushed.replace(tzinfo=timezone.utc)
                    expiring = pushed < cutoff
                    evaluated.append(
                        {
                            "imageDigest": record.get("imageDigest"),
                            "imageTags": sorted(record.get("imageTags") or []),
                            "imagePushedAt": record.get("imagePushedAt"),
                            "appliedRulePriority": rule,
                            "ruleDescription": rule_obj.get("description", ""),
                            "action": "expire" if expiring else "keep",
                            "reason": (
                                f"rule {rule} expires images pushed before {cutoff.isoformat()}; "
                                f"pushed {pushed.isoformat()}"
                                if expiring
                                else f"rule {rule} keeps images pushed after {cutoff.isoformat()}"
                            ),
                        }
                    )
        per_repo[repo] = {
            "images": evaluated,
            "expiring": [e for e in evaluated if e["action"] == "expire"],
        }

    return RetentionDecision(not issues, {"repositories": per_repo}, issues)


def _claimed_rule(rules: list[dict[str, Any]], tags: list[str]) -> dict[str, Any] | None:
    """The highest-priority rule whose selection matches the image's tags."""
    for rule in rules:
        if _selection_matches(rule.get("selection", {}), tags):
            return rule
    return None


def keep_digests(policy: Any, images: Any) -> set[str]:
    """Digests protected by the keep-10 release rule (push order, newest 10)."""
    decision = evaluate_images(policy, images)
    if not decision.valid:
        return set()
    protected: set[str] = set()
    for _repo, entry in decision.data.get("repositories", {}).items():
        for image in entry.get("images", []):
            if image.get("appliedRulePriority") == 1 and image.get("action") == "keep":
                protected.add(image.get("imageDigest"))
    return protected


# ---------------------------------------------------------------------------
# ECR lifecycle-policy-preview validation
# ---------------------------------------------------------------------------


def _norm_preview(preview: Any) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    repos: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(preview, dict):
        return {}, [_issue("INVALID_PREVIEW", "preview", "preview input must be an object")]
    for repo, entry in preview.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("previewResults"), list):
            issues.append(
                _issue("INVALID_PREVIEW", f"preview.{repo}", "expected {previewResults: [...]}")
            )
            continue
        repos[repo] = entry["previewResults"]
    return repos, issues


def preview_issues(
    policy: Any,
    images: Any,
    preview: Any,
    protected: Any = None,
    reference_date: str | None = None,
) -> RetentionDecision:
    """Validate ECR's lifecycle-policy-preview results against the model.

    ``preview``: ``{repository: {previewResults: [{imageDigest, imageTags,
    imagePushedAt, action: {type}, appliedRulePriority}]}}`` — the shape of
    ``get-lifecycle-policy-preview``. ``protected``: an optional list of
    digests that must never expire (the rollback-window release digests).

    Fail closed when:

    - the modeled evaluation disagrees with the preview for any digest
      (``PREVIEW_DISAGREEMENT``) — the preview and the model must agree on
      exactly which images a rule expires;
    - a protected digest is selected for expiration (``PROTECTED_IMAGE_EXPIRING``)
      — the keep-10 rule or the preview policy would break the rollback window;
    - a preview result references an image unknown to the observed state
      (``PREVIEW_UNKNOWN_IMAGE``);
    - any image bearing a ``release-*`` tag is selected by a non-official rule
      (``RELEASE_RULE_NOT_APPLIED``) — retained multi-tag release images are
      claimed by the keep-10 rule and can never be selected by lower-priority
      candidate rules (ECR first-match-wins semantics).
    """
    policy_decision = policy_issues(policy)
    if not policy_decision.valid:
        return RetentionDecision(False, {}, policy_decision.issues)
    repos, preview_issues_ = _norm_preview(preview)
    if preview_issues_:
        return RetentionDecision(False, {}, preview_issues_)

    modeled = evaluate_images(policy, images, reference_date)
    if not modeled.valid:
        return RetentionDecision(False, {}, modeled.issues)

    protected_set = set(protected or [])
    issues: list[dict[str, str]] = []
    data: dict[str, Any] = {}

    for repo, results in repos.items():
        repo_model = modeled.data.get("repositories", {}).get(repo, {})
        modeled_by_digest: dict[str, dict[str, Any]] = {}
        for image in repo_model.get("images", []):
            modeled_by_digest[image["imageDigest"]] = image
        preview_by_digest: dict[str, dict[str, Any]] = {}
        for result in results:
            digest = result.get("imageDigest")
            if not digest:
                issues.append(
                    _issue(
                        "INVALID_PREVIEW", f"preview.{repo}", "preview result without imageDigest"
                    )
                )
                continue
            preview_by_digest[digest] = result
            if digest not in modeled_by_digest:
                issues.append(
                    _issue(
                        "PREVIEW_UNKNOWN_IMAGE",
                        f"preview.{repo}.{digest}",
                        f"preview result references image {digest} unknown to the observed state",
                    )
                )
                continue
            modeled_image = modeled_by_digest[digest]
            action = result.get("action") or {}
            action_type = action.get("type") if isinstance(action, dict) else result.get("action")
            expiring = str(action_type).upper() == "EXPIRE"
            priority = result.get("appliedRulePriority")
            modeled_expiring = modeled_image["action"] == "expire"
            modeled_priority = modeled_image["appliedRulePriority"]
            if expiring and priority != modeled_priority:
                issues.append(
                    _issue(
                        "PREVIEW_DISAGREEMENT",
                        f"preview.{repo}.{digest}",
                        f"preview expires {digest} with rule priority {priority}, the model "
                        f"applies priority {modeled_priority}",
                    )
                )
            if expiring != modeled_expiring:
                if expiring and digest in protected_set:
                    issues.append(
                        _issue(
                            "PROTECTED_IMAGE_EXPIRING",
                            f"preview.{repo}.{digest}",
                            f"protected rollback-window digest {digest} is selected for "
                            "expiration by the previewed policy",
                        )
                    )
                else:
                    issues.append(
                        _issue(
                            "PREVIEW_DISAGREEMENT",
                            f"preview.{repo}.{digest}",
                            f"preview action for {digest} is "
                            f"{'expire' if expiring else 'keep'}, the model says "
                            f"{'expire' if modeled_expiring else 'keep'}",
                        )
                    )
            if expiring and not modeled_expiring:
                for tag in preview_by_digest[digest].get("imageTags") or []:
                    if tag.startswith(RELEASE_TAG_PREFIX):
                        issues.append(
                            _issue(
                                "RELEASE_RULE_NOT_APPLIED",
                                f"preview.{repo}.{digest}",
                                f"release-tagged image {digest} ({tag}) is selected by rule "
                                f"priority {priority}, not the keep-10 release rule; a "
                                "non-official rule must never select a release image",
                            )
                        )
            data.setdefault(repo, {})[digest] = {
                "preview": "expire" if expiring else "keep",
                "modeled": "expire" if modeled_expiring else "keep",
                "appliedRulePriority": priority,
            }
        for digest, modeled_image in modeled_by_digest.items():
            if modeled_image["action"] == "expire" and digest not in preview_by_digest:
                issues.append(
                    _issue(
                        "PREVIEW_DISAGREEMENT",
                        f"preview.{repo}.{digest}",
                        f"the model expires {digest} but the preview does not",
                    )
                )

    return RetentionDecision(not issues, {"comparison": data}, issues)


# ---------------------------------------------------------------------------
# Rollback-window retention audit (read-only)
# ---------------------------------------------------------------------------


def _officials_sorted(index: Any) -> list[dict[str, Any]]:
    """Official manifests newest-first by numeric SemVer (never index order)."""
    manifests = index.get("manifests", []) if isinstance(index, dict) else []
    officials = [
        m
        for m in manifests
        if isinstance(m, dict) and m.get("release", {}).get("status") == "official"
    ]

    def _version_key(manifest: dict[str, Any]) -> tuple[int, int, int]:
        parts = str(manifest.get("release", {}).get("version", "0.0.0")).split(".")
        return tuple(int(part) for part in parts[:3])  # type: ignore[return-value]

    return sorted(officials, key=_version_key, reverse=True)


def _window_versions(index: Any) -> list[str]:
    """The newest-10 (or all) official versions — the immediate rollback window."""
    officials = _officials_sorted(index)
    window = officials[: min(ROLLBACK_WINDOW, len(officials))]
    return [m.get("release", {}).get("version") for m in window]


def audit_rollback_window(index: Any, observed: Any) -> RetentionDecision:
    """Read-only retention audit of the immediate rollback window.

    Lists the exact ``ROLLBACK_WINDOW`` (10) newest official releases — or all
    of them when fewer than 10 exist — and fails closed when any required
    backend/frontend artifact of a window release is missing or mismatched
    (``RETENTION_ARTIFACT_MISSING``/``RETENTION_ARTIFACT_MISMATCH``). A
    release outside the window (whose artifacts may legitimately have expired)
    is reported in ``outsideWindow`` and is NEVER claimed to be immediately
    rollback-capable — an older metadata-only release can never pass this
    audit. The completeness checks reuse the 3.6 rollback target-set model
    (``release_artifacts_issues``): every backend ``release-<version>`` tag
    must resolve to the manifest digest AND the immutable frontend prefix
    marker must exist and match.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(index, dict) or not isinstance(observed, dict):
        return RetentionDecision(
            False,
            {},
            [_issue("OBSERVED_MISSING", "observed", "no observed state was provided")],
        )
    issues.extend(validate_index(index))
    read_error = _observed_error(observed)
    if read_error:
        issues.append(_issue("OBSERVED_READ_ERROR", "observed", read_error))

    officials = _officials_sorted(index)
    window = officials[: min(ROLLBACK_WINDOW, len(officials))]
    outside_window = officials[ROLLBACK_WINDOW:] if len(officials) > ROLLBACK_WINDOW else []

    window_releases: list[dict[str, Any]] = []
    rollback_capable: list[str] = []
    for manifest in window:
        version = manifest.get("release", {}).get("version")
        release_issues = []
        for artifact_issue in release_artifacts_issues(manifest, observed):
            code = (
                "RETENTION_ARTIFACT_MISSING"
                if artifact_issue["code"] == "TARGET_ARTIFACT_MISSING"
                else "RETENTION_ARTIFACT_MISMATCH"
            )
            release_issues.append({**artifact_issue, "code": code})
        if release_issues:
            issues.extend(release_issues)
        else:
            rollback_capable.append(version)
        window_releases.append(
            {
                "version": version,
                "rollbackCapable": not release_issues,
                "issues": release_issues,
            }
        )

    data: dict[str, Any] = {
        "released": len(officials),
        "window": len(window),
        "rollbackCapable": rollback_capable,
        "windowReleases": window_releases,
        "outsideWindow": [m.get("release", {}).get("version") for m in outside_window],
        "delayedEvaluation": DELAYED_EVALUATION_NOTE,
    }
    return RetentionDecision(not issues, data, issues)


def policy_coverage_issues(policy: Any, index: Any, observed: Any) -> RetentionDecision:
    """Cross-check the push-order keep-10 against the version-order window.

    ECR's keep-10 rule protects the 10 most recently PUSHED ``release-*``
    images; the rollback window is the 10 NEWEST VERSIONS. With in-order
    promotion the two sets coincide; an out-of-order promotion or a backport
    would push an older version after the newest 10 and its images could fall
    outside the push-order protection. Every window release's backend digests
    must be covered by the keep-10 push-order set or the audit fails closed
    (``POLICY_WINDOW_GAP``).
    """
    issues: list[dict[str, str]] = []
    policy_decision = policy_issues(policy)
    if not policy_decision.valid:
        return RetentionDecision(False, {}, policy_decision.issues)
    audit = audit_rollback_window(index, observed)
    if audit.issues:
        return RetentionDecision(False, {}, audit.issues)

    protected = keep_digests(policy, observed.get("ecr", {}) if isinstance(observed, dict) else {})
    window_versions = _window_versions(index)
    for manifest in _officials_sorted(index):
        if manifest.get("release", {}).get("version") not in window_versions:
            continue
        components = manifest.get("components", {})
        for key in rc.BACKEND_KEYS:
            component = components.get(key, {})
            digest = component.get("imageDigest")
            if digest and digest not in protected:
                issues.append(
                    _issue(
                        "POLICY_WINDOW_GAP",
                        f"components.{key}",
                        f"release {manifest.get('release', {}).get('version')} digest {digest} "
                        "is not covered by the keep-10 push-order protection; an out-of-order "
                        "promotion or backport could let the policy expire a window release",
                    )
                )

    data: dict[str, Any] = {
        "window": window_versions,
        "keep10Protected": sorted(protected),
        "covered": not issues,
    }
    return RetentionDecision(not issues, data, issues)


# ---------------------------------------------------------------------------
# Frontend prefix retention (S3)
# ---------------------------------------------------------------------------


def frontend_retention_issues(state: Any) -> RetentionDecision:
    """Validate the frontend ``_releases/v<version>/`` prefix retention plan.

    ``state``: ``{prefixes: [{prefix, version, exists}], protectedVersions:
    [...], currentRelease, previousKnownGood, proposedDeletions: [prefix]}``.

    The immutable prefixes are retained for the same latest-10 immediate
    rollback window as ECR. The currently deployed release and the previous
    known-good release are protected regardless of the window. A proposed
    deletion of a protected prefix, a deletion of an unknown prefix, or a
    missing prefix for a protected version all fail closed. GitHub Release
    assets remain the long-term source after the S3 window expires, so an
    unprotected prefix may be deleted.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(state, dict):
        return RetentionDecision(
            False,
            {},
            [_issue("FRONTEND_STATE_MISSING", "frontend", "no frontend retention state provided")],
        )
    prefixes = state.get("prefixes")
    if not isinstance(prefixes, list):
        issues.append(
            _issue("FRONTEND_STATE_MISSING", "frontend.prefixes", "prefixes list is required")
        )
        prefixes = []

    by_version: dict[str, str] = {}
    seen_prefixes: set[str] = set()
    for entry in prefixes:
        if not isinstance(entry, dict) or not entry.get("prefix"):
            issues.append(
                _issue("FRONTEND_STATE_MISSING", "frontend.prefixes", "prefix entry without prefix")
            )
            continue
        prefix = entry["prefix"]
        seen_prefixes.add(prefix)
        match = _FRONTEND_PREFIX_RE.match(prefix)
        if not match:
            issues.append(
                _issue(
                    "FRONTEND_PREFIX_INVALID",
                    f"frontend.prefixes.{prefix}",
                    f"prefix {prefix!r} is not an immutable _releases/v<version>/ prefix",
                )
            )
            continue
        version = entry.get("version") or match.group(1)
        by_version[version] = prefix
        if entry.get("exists") is False:
            issues.append(
                _issue(
                    "FRONTEND_RETENTION_GAP",
                    f"frontend.prefixes.{prefix}",
                    f"immutable frontend prefix {prefix} is missing",
                )
            )

    protected_versions = set(state.get("protectedVersions") or [])
    for label, version in (
        ("currentRelease", state.get("currentRelease")),
        ("previousKnownGood", state.get("previousKnownGood")),
    ):
        if version:
            protected_versions.add(version)
            if version not in by_version:
                issues.append(
                    _issue(
                        "FRONTEND_RETENTION_GAP",
                        f"frontend.{label}",
                        f"{label} release {version} has no immutable _releases/v{version}/ prefix",
                    )
                )

    deletions = state.get("proposedDeletions")
    if not isinstance(deletions, list):
        issues.append(
            _issue(
                "FRONTEND_STATE_MISSING",
                "frontend.proposedDeletions",
                "proposedDeletions list is required",
            )
        )
        deletions = []
    expirable: list[str] = []
    for prefix in deletions:
        if not isinstance(prefix, str):
            issues.append(
                _issue(
                    "FRONTEND_PREFIX_INVALID", "frontend.proposedDeletions", "deletion not a string"
                )
            )
            continue
        if prefix not in seen_prefixes:
            issues.append(
                _issue(
                    "FRONTEND_UNKNOWN_PREFIX",
                    f"frontend.proposedDeletions.{prefix}",
                    f"proposed deletion of unknown prefix {prefix!r}",
                )
            )
            continue
        match = _FRONTEND_PREFIX_RE.match(prefix)
        version = match.group(1) if match else None
        if version in protected_versions:
            issues.append(
                _issue(
                    "FRONTEND_PROTECTED_DELETE",
                    f"frontend.proposedDeletions.{prefix}",
                    f"prefix {prefix} belongs to protected release {version} (rollback "
                    "window / currently deployed / previous known-good) and must be retained",
                )
            )
        else:
            expirable.append(prefix)

    for version, prefix in by_version.items():
        if version not in protected_versions and prefix not in expirable:
            expirable.append(prefix)

    data: dict[str, Any] = {
        "protectedVersions": sorted(protected_versions),
        "expirablePrefixes": sorted(expirable),
        "longTermSource": "GitHub Release assets remain the long-term store after the "
        "S3/ECR window expires",
    }
    return RetentionDecision(not issues, data, issues)


# ---------------------------------------------------------------------------
# GitHub retention classes (checkbox 5)
# ---------------------------------------------------------------------------


def retention_classes_issues(config: Any) -> RetentionDecision:
    """Validate the GitHub artifact retention-class configuration.

    ``config``: ``{classes: {<class>: "indefinite" | <days>}}``. Releases,
    final manifests, SBOMs, checksums, and sanitized audit/test evidence are
    retained indefinitely; candidate-only artifacts for 30 days; staging-failure
    diagnostics and release result records per their existing shorter
    operational retention (14 days). Unknown classes, missing classes, and
    wrong values fail closed.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(config, dict) or not isinstance(config.get("classes"), dict):
        return RetentionDecision(
            False,
            {},
            [
                _issue(
                    "RETENTION_CLASS_CONFIG_INVALID", "classes", "config must be {classes: {...}}"
                )
            ],
        )
    classes = config["classes"]
    for name, expected in RETENTION_CLASSES.items():
        actual = classes.get(name)
        if actual is None:
            issues.append(
                _issue(
                    "RETENTION_CLASS_MISSING",
                    f"classes.{name}",
                    f"retention class {name} is missing",
                )
            )
            continue
        if expected == "indefinite":
            if actual != "indefinite":
                issues.append(
                    _issue(
                        "RETENTION_CLASS_MISMATCH",
                        f"classes.{name}",
                        f"retention class {name} must be retained indefinitely, got {actual!r}",
                    )
                )
        else:
            if actual != expected:
                issues.append(
                    _issue(
                        "RETENTION_CLASS_MISMATCH",
                        f"classes.{name}",
                        f"retention class {name} must be {expected} days, got {actual!r}",
                    )
                )
    for name in classes:
        if name not in RETENTION_CLASSES:
            issues.append(
                _issue(
                    "RETENTION_CLASS_UNKNOWN",
                    f"classes.{name}",
                    f"unknown retention class {name!r}",
                )
            )

    data: dict[str, Any] = {
        "classes": {
            name: ("indefinite" if value == "indefinite" else int(value))
            for name, value in RETENTION_CLASSES.items()
        }
    }
    return RetentionDecision(not issues, data, issues)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _emit(decision: RetentionDecision) -> int:
    print(json.dumps(decision.to_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if decision.valid else 1


def _cmd_validate_policy(args: argparse.Namespace) -> int:
    return _emit(policy_issues(_read_json(args.policy)))


def _cmd_evaluate(args: argparse.Namespace) -> int:
    return _emit(
        evaluate_images(_read_json(args.policy), _read_json(args.images), args.reference_date)
    )


def _cmd_validate_preview(args: argparse.Namespace) -> int:
    protected = None
    if args.protected:
        protected = _read_json(args.protected)
    return _emit(
        preview_issues(
            _read_json(args.policy),
            _read_json(args.images),
            _read_json(args.preview),
            protected,
            args.reference_date,
        )
    )


def _cmd_audit(args: argparse.Namespace) -> int:
    return _emit(audit_rollback_window(_read_json(args.index), _read_json(args.observed)))


def _cmd_coverage(args: argparse.Namespace) -> int:
    return _emit(
        policy_coverage_issues(
            _read_json(args.policy), _read_json(args.index), _read_json(args.observed)
        )
    )


def _cmd_frontend(args: argparse.Namespace) -> int:
    return _emit(frontend_retention_issues(_read_json(args.state)))


def _cmd_classes(args: argparse.Namespace) -> int:
    return _emit(retention_classes_issues(_read_json(args.config)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.retention",
        description=(
            "ECR lifecycle retention and rollback-window enforcement (Pass 3, subphase 3.8)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate_policy = sub.add_parser(
        "validate-policy", help="validate the desired lifecycle policy"
    )
    validate_policy.add_argument("--policy", required=True, metavar="JSON")
    validate_policy.set_defaults(func=_cmd_validate_policy)

    evaluate = sub.add_parser(
        "evaluate", help="model ECR's first-match-wins evaluation (offline preview)"
    )
    evaluate.add_argument("--policy", required=True, metavar="JSON")
    evaluate.add_argument("--images", required=True, metavar="JSON")
    evaluate.add_argument(
        "--reference-date", metavar="ISO", help="deterministic evaluation date (default: now)"
    )
    evaluate.set_defaults(func=_cmd_evaluate)

    validate_preview = sub.add_parser(
        "validate-preview", help="validate ECR lifecycle-policy-preview results against the model"
    )
    validate_preview.add_argument("--policy", required=True, metavar="JSON")
    validate_preview.add_argument("--images", required=True, metavar="JSON")
    validate_preview.add_argument("--preview", required=True, metavar="JSON")
    validate_preview.add_argument(
        "--protected", metavar="JSON", help="list of digests that must never expire"
    )
    validate_preview.add_argument("--reference-date", metavar="ISO")
    validate_preview.set_defaults(func=_cmd_validate_preview)

    audit = sub.add_parser("audit", help="read-only rollback-window retention audit")
    audit.add_argument("--index", required=True, metavar="JSON")
    audit.add_argument("--observed", required=True, metavar="JSON")
    audit.set_defaults(func=_cmd_audit)

    coverage = sub.add_parser(
        "coverage", help="keep-10 push-order coverage of the version-order window"
    )
    coverage.add_argument("--policy", required=True, metavar="JSON")
    coverage.add_argument("--index", required=True, metavar="JSON")
    coverage.add_argument("--observed", required=True, metavar="JSON")
    coverage.set_defaults(func=_cmd_coverage)

    frontend = sub.add_parser("frontend-retention", help="validate the S3 prefix retention plan")
    frontend.add_argument("--state", required=True, metavar="JSON")
    frontend.set_defaults(func=_cmd_frontend)

    classes = sub.add_parser("retention-classes", help="validate the GitHub retention classes")
    classes.add_argument("--config", required=True, metavar="JSON")
    classes.set_defaults(func=_cmd_classes)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
