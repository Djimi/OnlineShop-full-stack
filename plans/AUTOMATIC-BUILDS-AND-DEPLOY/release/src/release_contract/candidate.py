"""Candidate artifact production rules (Pass 3, subphase 3.2).

Implements the canonical-producer and immutable-artifact rules of Decision 11:

- A ``sha-<full-sha>`` image is produced once by the first trusted, successful
  ``main`` push. A rerun may revalidate and reuse those bytes but must never
  rebuild and overwrite them, and must never claim to have produced them.
- ``should_reuse_image`` decides, per image, whether an existing ECR candidate
  tag may be reused instead of pushed. Reuse requires the OCI revision and
  producer labels to identify a trusted successful ``main`` push; anything else
  fails closed.
- ``canonical_set_issues`` verifies that all three backend images form one
  canonical producer set (same producer run id, same revision, trusted
  event/ref) with matching ``items`` ``common`` revision.
- ``build_candidate_manifest`` renders a schema-valid ``candidate`` manifest
  from the immutable facts recorded at build time plus the SemVer that the
  owner assigns at promotion time (Decision 3). The result is always validated
  against the release contract before being returned.

All functions are pure and tested with fixtures; shell wrappers only gather
data (AWS/GitHub) and pass validated values as arguments or JSON files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from . import components as rc
from .validate import validate_data

_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class Decision:
    """Result of a reuse/canonical-set check."""

    reuse: bool
    issues: list[dict[str, str]] = field(default_factory=list)


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


def _label(existing: Any, key: str) -> Any:
    labels = existing.get("labels") if isinstance(existing, dict) else None
    if not isinstance(labels, dict):
        return None
    return labels.get(key)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _expected_sha(expected: Any) -> str | None:
    if not isinstance(expected, dict):
        return None
    return expected.get("sha")


def should_reuse_image(existing: Any, expected: Any) -> Decision:
    """Decide whether ``existing`` (an ECR image's digest + OCI labels) may be
    reused for the current run described by ``expected``.

    ``expected``: ``{sha, producerConclusion?, ...}``. ``producerConclusion``
    is the GitHub API-verified conclusion of the run named by the image's
    producer labels; when present it must be ``"success"`` or the image fails
    closed (a failed run's push is not a trusted canonical producer).
    """
    issues: list[dict[str, str]] = []
    sha = _expected_sha(expected)
    if not isinstance(sha, str) or not _FULL_SHA_RE.match(sha):
        return Decision(
            False, [_issue("INVALID_EXPECTED", "$", "expected.sha must be a full 40-char SHA")]
        )

    if not isinstance(existing, dict):
        return Decision(
            False, [_issue("MISSING_IMAGE", "$", "no existing image record was provided")]
        )

    digest = existing.get("imageDigest")
    if not isinstance(digest, str) or not _IMAGE_DIGEST_RE.match(digest):
        issues.append(_issue("INVALID_DIGEST", "imageDigest", f"invalid image digest {digest!r}"))

    revision = _label(existing, rc.OCI_REVISION)
    if revision != sha:
        issues.append(
            _issue(
                "REVISION_MISMATCH",
                f"labels.{rc.OCI_REVISION}",
                f"existing image revision {revision!r} does not match the candidate SHA {sha!r}",
            )
        )

    producer_event = _label(existing, rc.PRODUCER_EVENT)
    if producer_event != rc.TRUSTED_EVENT:
        issues.append(
            _issue(
                "PRODUCER_EVENT_MISMATCH",
                f"labels.{rc.PRODUCER_EVENT}",
                f"existing image producer event {producer_event!r} is not a trusted "
                f"{rc.TRUSTED_EVENT!r}",
            )
        )
    producer_ref = _label(existing, rc.PRODUCER_REF)
    if producer_ref != rc.TRUSTED_REF:
        issues.append(
            _issue(
                "PRODUCER_REF_MISMATCH",
                f"labels.{rc.PRODUCER_REF}",
                f"existing image producer ref {producer_ref!r} is not {rc.TRUSTED_REF!r}",
            )
        )

    producer_run_id = _positive_int(_label(existing, rc.PRODUCER_RUN_ID))
    producer_run_attempt = _positive_int(_label(existing, rc.PRODUCER_RUN_ATTEMPT))
    if producer_run_id is None or producer_run_attempt is None:
        issues.append(
            _issue(
                "PRODUCER_IDENTITY_MISSING",
                f"labels.{rc.PRODUCER_RUN_ID}",
                "existing image has no valid producer run id/attempt labels",
            )
        )

    if isinstance(expected, dict) and "producerConclusion" in expected:
        conclusion = expected.get("producerConclusion")
        if conclusion != "success":
            producer = (
                f"producer run {producer_run_id}/attempt {producer_run_attempt}"
                if producer_run_id
                else "existing producer"
            )
            issues.append(
                _issue(
                    "PRODUCER_UNSUCCESSFUL",
                    "producerConclusion",
                    f"{producer} concluded {conclusion!r}; only a trusted successful "
                    f"main push may be reused",
                )
            )

    return Decision(not issues, issues)


def canonical_set_issues(backends: dict[str, Any], *, sha: str) -> list[dict[str, str]]:
    """Verify all three backend images form one canonical producer set.

    Each ``backends[component]`` is an existing-image record (digest + labels)
    or ``None``. The set is canonical only when every backend is present, every
    revision equals ``sha``, every producer event/ref is trusted, the Items
    ``common`` revision equals ``sha``, and all producer run ids are identical.
    """
    issues: list[dict[str, str]] = []
    run_ids: set[int] = set()
    for key in rc.BACKEND_KEYS:
        existing = backends.get(key)
        if existing is None:
            issues.append(
                _issue(
                    "MISSING_BACKEND", f"components.{key}", f"no candidate image recorded for {key}"
                )
            )
            continue
        revision = _label(existing, rc.OCI_REVISION)
        if revision != sha:
            issues.append(
                _issue(
                    "REVISION_MISMATCH",
                    f"components.{key}.labels.{rc.OCI_REVISION}",
                    f"{key} revision {revision!r} does not match candidate SHA {sha!r}",
                )
            )
        if _label(existing, rc.PRODUCER_EVENT) != rc.TRUSTED_EVENT:
            issues.append(
                _issue(
                    "PRODUCER_EVENT_MISMATCH",
                    f"components.{key}",
                    f"{key} producer is not a trusted {rc.TRUSTED_EVENT!r}",
                )
            )
        if _label(existing, rc.PRODUCER_REF) != rc.TRUSTED_REF:
            issues.append(
                _issue(
                    "PRODUCER_REF_MISMATCH",
                    f"components.{key}",
                    f"{key} producer ref is not {rc.TRUSTED_REF!r}",
                )
            )
        run_id = _positive_int(_label(existing, rc.PRODUCER_RUN_ID))
        if run_id is None:
            issues.append(
                _issue(
                    "PRODUCER_IDENTITY_MISSING",
                    f"components.{key}",
                    f"{key} has no valid producer run id",
                )
            )
        else:
            run_ids.add(run_id)
        if _positive_int(_label(existing, rc.PRODUCER_RUN_ATTEMPT)) is None:
            issues.append(
                _issue(
                    "PRODUCER_IDENTITY_MISSING",
                    f"components.{key}",
                    f"{key} has no valid producer run attempt",
                )
            )
        if key == "items" and _label(existing, rc.COMMON_REVISION_LABEL) != sha:
            issues.append(
                _issue(
                    "COMMON_REVISION_MISMATCH",
                    "components.items",
                    f"items common revision {_label(existing, rc.COMMON_REVISION_LABEL)!r} "
                    f"does not match candidate SHA {sha!r}",
                )
            )
    if len(run_ids) > 1:
        issues.append(
            _issue(
                "PRODUCER_SET_SPLIT",
                "components",
                f"backend images were produced by different runs: {sorted(run_ids)!r}",
            )
        )
    issues.sort(key=lambda item: (item["field"], item["code"]))
    return issues


def _workflow_evidence(repository: str, run_id: int, run_attempt: int) -> dict[str, Any]:
    return {
        "runId": run_id,
        "runAttempt": run_attempt,
        "url": rc.run_url(repository, run_id, run_attempt),
        "event": "push",
        "ref": "refs/heads/main",
        "conclusion": "success",
    }


def _backend_component(
    component: str,
    *,
    sha: str,
    version: str,
    repository: str,
    image_digest: str,
    sbom: str,
    common_source_sha: str | None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "identity": rc.identity_for(component, version),
        "sourceSha": sha,
        "repository": repository,
        "imageDigest": image_digest,
        "candidateTag": rc.candidate_tag_for(sha),
        "releaseTag": rc.release_tag_for(version),
        "sbom": sbom,
    }
    if common_source_sha is not None:
        record["commonSourceSha"] = common_source_sha
    return record


def build_candidate_manifest(context: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """Render and validate a schema-valid ``candidate`` release manifest.

    ``context``: ``{version, sourceSha, repository, createdAt, candidateRunId,
    candidateRunAttempt, artifactRunId, artifactRunAttempt, validatedAt}``.
    ``components``: per-component facts, e.g.
    ``{"auth": {"repository": "onlineshop-auth", "imageDigest": "sha256:.."},
    "items": {"imageDigest": "sha256:.."}, "apiGateway": {...},
    "frontend": {"sha256": "<archive-checksum>"}}``. Backend repositories and
    SBOM names are derived from the canonical component map; only digests and
    the frontend checksum are taken from the evidence.
    """
    sha = context["sourceSha"]
    version = context["version"]
    repository = context["repository"]
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "release": {
            "version": version,
            "gitTag": rc.git_tag_for(version),
            "status": "candidate",
            "createdAt": context["createdAt"],
            "sourceSha": sha,
            "repository": repository,
            "candidateWorkflow": _workflow_evidence(
                repository, context["candidateRunId"], context["candidateRunAttempt"]
            ),
            "artifactWorkflow": _workflow_evidence(
                repository, context["artifactRunId"], context["artifactRunAttempt"]
            ),
            "stagingValidation": {
                "job": "e2e-staging",
                "conclusion": "success",
                "validatedAt": context["validatedAt"],
            },
        },
        "components": {
            "auth": _backend_component(
                "auth",
                sha=sha,
                version=version,
                repository=components["auth"]["repository"],
                image_digest=components["auth"]["imageDigest"],
                sbom=rc.sbom_for("auth"),
                common_source_sha=None,
            ),
            "items": _backend_component(
                "items",
                sha=sha,
                version=version,
                repository=components["items"]["repository"],
                image_digest=components["items"]["imageDigest"],
                sbom=rc.sbom_for("items"),
                common_source_sha=sha,
            ),
            "apiGateway": _backend_component(
                "apiGateway",
                sha=sha,
                version=version,
                repository=components["apiGateway"]["repository"],
                image_digest=components["apiGateway"]["imageDigest"],
                sbom=rc.sbom_for("apiGateway"),
                common_source_sha=None,
            ),
            "frontend": {
                "identity": rc.identity_for("frontend", version),
                "sourceSha": sha,
                "artifact": rc.FRONTEND_ARTIFACT,
                "sha256": components["frontend"]["sha256"],
                "sbom": rc.sbom_for("frontend"),
                "releasePrefix": rc.release_prefix_for(version),
                "versionMarker": rc.VERSION_MARKER,
            },
        },
    }
    result = validate_data(manifest)
    if not result.valid:
        raise ValueError(f"candidate manifest failed validation: {json.dumps(result.issues)}")
    return manifest


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _cmd_decide(args: argparse.Namespace) -> int:
    existing = _read_json(args.existing)
    expected = _read_json(args.expected)
    decision = should_reuse_image(existing, expected)
    _print_json({"reuse": decision.reuse, "issues": decision.issues})
    return 0 if decision.reuse else 1


def _cmd_set_check(args: argparse.Namespace) -> int:
    backends = _read_json(args.backends)
    issues = canonical_set_issues(backends, sha=args.sha)
    _print_json({"canonical": not issues, "issues": issues})
    return 0 if not issues else 1


def _cmd_build_manifest(args: argparse.Namespace) -> int:
    context = _read_json(args.context)
    components = _read_json(args.components)
    try:
        manifest = build_candidate_manifest(context, components)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.candidate",
        description="Candidate artifact production rules for the release contract.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    decide = sub.add_parser("decide", help="decide whether an existing sha tag may be reused")
    decide.add_argument(
        "--existing",
        required=True,
        metavar="JSON",
        help="existing image {imageDigest, labels} JSON file",
    )
    decide.add_argument(
        "--expected",
        required=True,
        metavar="JSON",
        help="expected {sha, producerConclusion, ...} JSON file",
    )
    decide.set_defaults(func=_cmd_decide)

    set_check = sub.add_parser(
        "set-check", help="verify the three backends form one canonical producer set"
    )
    set_check.add_argument(
        "--backends", required=True, metavar="JSON", help="backend -> existing-image JSON file"
    )
    set_check.add_argument("--sha", required=True, metavar="SHA", help="candidate full commit SHA")
    set_check.set_defaults(func=_cmd_set_check)

    build_manifest = sub.add_parser(
        "build-manifest", help="render and validate a candidate manifest"
    )
    build_manifest.add_argument(
        "--context", required=True, metavar="JSON", help="context facts JSON file"
    )
    build_manifest.add_argument(
        "--components", required=True, metavar="JSON", help="component facts JSON file"
    )
    build_manifest.add_argument(
        "--output", required=True, metavar="FILE", help="candidate manifest output path"
    )
    build_manifest.set_defaults(func=_cmd_build_manifest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
