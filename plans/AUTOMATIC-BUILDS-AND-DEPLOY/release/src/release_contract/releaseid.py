"""Release identity uniqueness and interrupted-promotion resume rules
(Pass 3, subphase 3.3).

Before any promotion mutation, the workflow must prove that the release
identity it is about to create is either free or already exactly matches the
validated manifest:

- A GitHub ``v<version>`` tag that points at a different commit than the
  candidate fails closed (another release already owns that identity).
- An ECR ``release-<version>`` tag that resolves to a different digest than the
  manifest fails closed (immutable tags are never overwritten).
- A frontend release-prefix ``release.json`` marker whose version/SHA/checksum
  differs from the manifest fails closed.

When every existing partial object exactly matches the manifest, an interrupted
promotion may resume idempotently (``action=resume``); when nothing exists the
promotion may proceed (``action=proceed``). Any mismatch is a collision and the
caller must fail closed before making any mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from . import components as rc
from .semver import is_valid as is_valid_semver
from .validate import validate_data


@dataclass
class IdentityDecision:
    """Result of a release identity collision check.

    ``action`` is ``"proceed"`` or ``"resume"``. A non-empty ``issues`` list
    means fail closed regardless of ``action`` (never mutate on a collision).
    """

    action: str
    issues: list[dict[str, str]] = field(default_factory=list)


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


def _frontend_marker(version: str, source_sha: str, frontend_sha256: str) -> dict[str, str]:
    """The canonical ``release.json`` version-marker content for a release."""
    return {
        "version": version,
        "sourceSha": source_sha,
        "frontendSha256": frontend_sha256,
    }


def frontend_marker(manifest: Any) -> dict[str, str]:
    """Return the version-marker content a promotion must write for ``manifest``."""
    release = manifest.get("release", {})
    frontend = manifest.get("components", {}).get("frontend", {})
    return _frontend_marker(
        release.get("version", ""), release.get("sourceSha", ""), frontend.get("sha256", "")
    )


def release_identity_issues(manifest: Any, observed: Any) -> IdentityDecision:
    """Validate that no collision blocks creating the manifest's release identity.

    ``manifest`` is the validated candidate/official manifest. ``observed`` is
    the read-only current state:

    .. code-block:: json

        {
          "gitTag": {"exists": true, "sha": "<full-sha>"},
          "ecr": {
            "onlineshop-auth": {"releaseDigest": "sha256:..."},
            "onlineshop-items": {"releaseDigest": null},
            "onlineshop-api-gateway": {"releaseDigest": null}
          },
          "frontend": {
            "markerExists": true,
            "marker": {"version": "...", "sourceSha": "...", "frontendSha256": "..."}
          }
        }

    Returns ``IdentityDecision(action, issues)``; ``action`` is ``resume`` when
    at least one partial object exists and every existing object matches,
    ``proceed`` when nothing exists, and any issue means fail closed.
    """
    result = validate_data(manifest)
    if not result.valid:
        return IdentityDecision(
            "proceed",
            [_issue("MANIFEST_INVALID", "$", f"manifest is not schema-valid: {result.issues!r}")],
        )

    release = manifest.get("release", {})
    components = manifest.get("components", {})
    version = release.get("version")
    source_sha = release.get("sourceSha")
    git_tag = release.get("gitTag")

    if not is_valid_semver(version):
        return IdentityDecision(
            "proceed",
            [_issue("INVALID_VERSION", "release.version", f"invalid version {version!r}")],
        )
    if not isinstance(source_sha, str) or len(source_sha) != 40:
        return IdentityDecision(
            "proceed",
            [_issue("INVALID_SHA", "release.sourceSha", f"invalid source SHA {source_sha!r}")],
        )

    issues: list[dict[str, str]] = []
    anything_exists = False

    if not isinstance(observed, dict):
        return IdentityDecision(
            "proceed",
            [_issue("INVALID_OBSERVED", "$", "observed state must be an object")],
        )

    # GitHub v<version> tag.
    git_observed = observed.get("gitTag")
    if isinstance(git_observed, dict) and git_observed.get("exists"):
        anything_exists = True
        observed_sha = git_observed.get("sha")
        if observed_sha != source_sha:
            issues.append(
                _issue(
                    "GIT_TAG_CONFLICT",
                    "gitTag",
                    f"git tag {git_tag} already exists at {observed_sha!r}, "
                    f"expected {source_sha!r}",
                )
            )

    # ECR release-<version> tags per backend.
    ecr_observed = observed.get("ecr")
    if not isinstance(ecr_observed, dict):
        issues.append(_issue("INVALID_OBSERVED", "ecr", "observed.ecr must be an object"))
    else:
        for component in rc.BACKEND_KEYS:
            repository = components.get(component, {}).get("repository")
            release_tag = components.get(component, {}).get("releaseTag")
            image_digest = components.get(component, {}).get("imageDigest")
            repo_state = ecr_observed.get(repository) if repository else None
            if not isinstance(repo_state, dict):
                continue
            release_digest = repo_state.get("releaseDigest")
            if release_digest is not None:
                anything_exists = True
                if release_digest != image_digest:
                    issues.append(
                        _issue(
                            "ECR_RELEASE_TAG_CONFLICT",
                            f"ecr.{repository}",
                            f"release tag {release_tag} resolves to {release_digest}, "
                            f"manifest records {image_digest}",
                        )
                    )

    # Frontend release-prefix version marker.
    frontend = components.get("frontend", {})
    marker_exists = False
    marker = None
    if isinstance(observed.get("frontend"), dict):
        marker_exists = bool(observed["frontend"].get("markerExists"))
        marker = observed["frontend"].get("marker")
    if marker_exists:
        anything_exists = True
        expected_marker = _frontend_marker(version, source_sha, frontend.get("sha256", ""))
        if not isinstance(marker, dict) or any(
            marker.get(key) != expected_marker[key] for key in expected_marker
        ):
            issues.append(
                _issue(
                    "FRONTEND_PREFIX_CONFLICT",
                    "frontend.marker",
                    f"frontend prefix {frontend.get('releasePrefix')} contains a version "
                    f"marker that does not match the manifest: {marker!r}",
                )
            )

    if issues:
        return IdentityDecision("proceed", issues)
    return IdentityDecision("resume" if anything_exists else "proceed", [])


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _cmd_decide(args: argparse.Namespace) -> int:
    if not os.path.isfile(args.manifest):
        print(f"ERROR: manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    manifest = _read_json(args.manifest)
    observed = _read_json(args.observed)
    decision = release_identity_issues(manifest, observed)
    _print_json({"action": decision.action, "issues": decision.issues})
    return 0 if not decision.issues else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.releaseid",
        description="Release identity collision and resume rules.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    decide = sub.add_parser("decide", help="decide proceed / resume / fail-closed")
    decide.add_argument(
        "--manifest", required=True, metavar="FILE", help="validated manifest JSON file"
    )
    decide.add_argument(
        "--observed",
        required=True,
        metavar="JSON",
        help="observed release-identity state JSON file",
    )
    decide.set_defaults(func=_cmd_decide)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
