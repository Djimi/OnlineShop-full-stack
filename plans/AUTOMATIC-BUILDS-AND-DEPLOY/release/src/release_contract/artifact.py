"""GitHub artifact identity resolution and evidence bundle verification.

Subphase 3.2 records the exact GitHub artifact ID and the GitHub
service-reported artifact digest (the SHA-256 of the uploaded artifact archive,
returned by ``actions/upload-artifact@v4`` as the ``artifact-digest`` output)
produced by the candidate workflow run. Promotion (subphase 3.4) consumes the
bundle by exact run id, attempt, artifact id, and name — rejecting expired or
duplicate artifacts — and verifies the recorded service digest against the
downloaded archive as well as the checksummed bundle contents.

This module provides the pure, fixture-tested pieces:

- ``artifact_name_for`` / ``parse_artifact_name``: the deterministic artifact
  name ``candidate-evidence-<full-sha>-<run-attempt>`` encodes the run attempt
  so duplicate attempts never collide.
- ``select_artifact``: from a GitHub ``actions/runs/{id}/artifacts`` listing,
  select the single artifact matching (run id, name); reject duplicates,
  expired artifacts, and tampered names.
- ``verify_artifact_digest``: compare the recorded GitHub service-reported
  digest against the SHA-256 of a downloaded artifact archive.
- ``verify_evidence_bundle``: verify the extracted bundle's required files,
  the sorted checksums file, and the recorded frontend archive checksum.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from typing import Any

from . import checksums

_ARTIFACT_NAME_RE = re.compile(r"^candidate-evidence-[0-9a-f]{40}-[1-9][0-9]*$")

# Required files inside a candidate evidence bundle (subphase 3.2).
REQUIRED_BUNDLE_FILES = (
    "candidate-evidence.json",
    "frontend-dist.tar.gz",
    "frontend-dist.sha256",
    "auth.spdx.json",
    "items.spdx.json",
    "api-gateway.spdx.json",
    "frontend.spdx.json",
    "checksums.txt",
)

CHECKSUM_LINE_RE = re.compile(r"^[0-9a-f]{64}  [^\n]+$")


def artifact_name_for(source_sha: str, run_attempt: int) -> str:
    """Return the deterministic candidate evidence artifact name."""
    return f"candidate-evidence-{source_sha}-{run_attempt}"


def parse_artifact_name(name: str) -> dict[str, str] | None:
    """Parse an artifact name into ``{sourceSha, runAttempt}`` or return None."""
    match = _ARTIFACT_NAME_RE.match(name)
    if not match:
        return None
    sha, attempt = name.split("-", 2)[2].rsplit("-", 1)
    return {"sourceSha": sha, "runAttempt": attempt}


def select_artifact(artifacts: list[dict[str, Any]], *, run_id: int, name: str) -> dict[str, Any]:
    """Select the single artifact for ``(run_id, name)``.

    Raises ``ValueError`` when the artifact is missing, expired, or when more
    than one artifact matches (a duplicate must never be silently picked).
    """
    matches: list[dict[str, Any]] = []
    for artifact in artifacts:
        workflow_run = artifact.get("workflow_run")
        artifact_run_id = workflow_run.get("id") if isinstance(workflow_run, dict) else None
        if artifact_run_id != run_id or artifact.get("name") != name:
            continue
        matches.append(artifact)

    if not matches:
        raise ValueError(f"no artifact named {name!r} for run {run_id}")
    if len(matches) > 1:
        raise ValueError(
            f"duplicate artifacts for {name!r} in run {run_id}: {[m.get('id') for m in matches]}"
        )

    artifact = matches[0]
    if artifact.get("expired") is True:
        raise ValueError(f"artifact {name!r} (id {artifact.get('id')}) is expired")
    expires_at = artifact.get("expires_at")
    if isinstance(expires_at, str):
        parsed = _parse_rfc3339(expires_at)
        if parsed is not None and parsed <= _dt.datetime.now(_dt.timezone.utc):
            raise ValueError(f"artifact {name!r} (id {artifact.get('id')}) expired at {expires_at}")
    return artifact


def _parse_rfc3339(value: str) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def verify_artifact_digest(recorded_digest: str, downloaded_sha256: str) -> bool:
    """Return whether a downloaded artifact archive matches the recorded digest.

    ``recorded_digest`` is the GitHub service-reported ``artifact-digest``
    (SHA-256 of the uploaded archive); ``downloaded_sha256`` is the SHA-256 of
    the archive as actually downloaded. Exact match means the downloaded bytes
    are the bytes GitHub reported uploading.
    """
    return bool(recorded_digest) and recorded_digest == downloaded_sha256


def verify_evidence_bundle(
    bundle_dir: str, expected_frontend_sha256: str | None = None
) -> tuple[bool, list[dict[str, str]]]:
    """Verify a candidate evidence bundle on disk.

    Checks that every required file exists, that ``checksums.txt`` is sorted
    and well-formed with entries matching real bundle files, and — when
    provided — that the frontend archive SHA-256 matches the recorded value.
    Returns ``(ok, issues)``.
    """
    import os

    issues: list[dict[str, str]] = []
    for name in REQUIRED_BUNDLE_FILES:
        if not os.path.isfile(os.path.join(bundle_dir, name)):
            issues.append(
                {
                    "code": "MISSING_BUNDLE_FILE",
                    "field": name,
                    "message": f"missing evidence file: {name}",
                }
            )

    checksums_path = os.path.join(bundle_dir, "checksums.txt")
    if os.path.isfile(checksums_path):
        try:
            with open(checksums_path, encoding="utf-8") as handle:
                lines = [line.rstrip("\n") for line in handle if line.strip()]
        except OSError as exc:
            issues.append({"code": "READ_ERROR", "field": "checksums.txt", "message": str(exc)})
            lines = []
        seen: list[str] = []
        for index, line in enumerate(lines):
            if not CHECKSUM_LINE_RE.match(line):
                issues.append(
                    {
                        "code": "MALFORMED_CHECKSUM_LINE",
                        "field": "checksums.txt",
                        "message": f"line {index + 1} is not '<sha256>  <file>': {line!r}",
                    }
                )
                continue
            rel_path = line.split("  ", 1)[1]
            if not os.path.isfile(os.path.join(bundle_dir, rel_path)):
                issues.append(
                    {
                        "code": "CHECKSUM_FILE_MISSING",
                        "field": rel_path,
                        "message": f"checksum references missing file: {rel_path}",
                    }
                )
            seen.append(line)
        paths_in_order = [line.split("  ", 1)[1] for line in seen]
        if sorted(paths_in_order) != paths_in_order:
            issues.append(
                {
                    "code": "UNSORTED_CHECKSUMS",
                    "field": "checksums.txt",
                    "message": "checksums.txt is not sorted by path",
                }
            )

    if expected_frontend_sha256 is not None:
        archive = os.path.join(bundle_dir, "frontend-dist.tar.gz")
        if os.path.isfile(archive):
            actual = checksums.sha256_file(archive)
            if actual != expected_frontend_sha256:
                issues.append(
                    {
                        "code": "ARCHIVE_CHECKSUM_MISMATCH",
                        "field": "frontend-dist.tar.gz",
                        "message": f"archive sha256 {actual} does not match recorded "
                        f"{expected_frontend_sha256}",
                    }
                )
        else:
            issues.append(
                {
                    "code": "MISSING_BUNDLE_FILE",
                    "field": "frontend-dist.tar.gz",
                    "message": "cannot verify missing frontend archive",
                }
            )

    issues.sort(key=lambda item: (item["field"], item["code"]))
    return (not issues, issues)


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _cmd_select(args: argparse.Namespace) -> int:
    artifacts = _read_json(args.artifacts)
    try:
        selected = select_artifact(artifacts, run_id=args.run_id, name=args.name)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"id": selected.get("id"), "name": selected.get("name")}, sort_keys=True))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    ok, issues = verify_evidence_bundle(
        args.bundle_dir, expected_frontend_sha256=args.frontend_sha256
    )
    print(json.dumps({"valid": ok, "issues": issues}, sort_keys=True))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.artifact",
        description="GitHub artifact identity resolution and evidence verification.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    select = sub.add_parser("select", help="select a single artifact by run id and name")
    select.add_argument(
        "--artifacts", required=True, metavar="JSON", help="GitHub artifacts listing JSON file"
    )
    select.add_argument("--run-id", required=True, type=int, metavar="RUN_ID")
    select.add_argument("--name", required=True, metavar="NAME")
    select.set_defaults(func=_cmd_select)

    verify = sub.add_parser("verify", help="verify an extracted evidence bundle")
    verify.add_argument("--bundle-dir", required=True, metavar="DIR")
    verify.add_argument("--frontend-sha256", metavar="SHA256", default=None)
    verify.set_defaults(func=_cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
