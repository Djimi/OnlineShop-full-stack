"""Safe frontend archive handling (Pass 3, subphase 3.2).

The frontend candidate is a checksummed ``frontend-dist.tar.gz`` produced
reproducibly with ``VITE_API_URL=''``. Two concerns are implemented here with
deterministic, fixture-tested logic:

- ``validate_archive`` rejects traversal, links, and device/file special
  entries *before* extraction (absolute paths, ``..`` segments, symlinks,
  hardlinks, character/block devices, FIFOs, sockets).
- ``verify_checksum_manifest`` checks the sorted per-file ``frontend-dist.sha256``
  manifest against an extracted tree: every entry is well-formed, safe, sorted,
  exists, and matches its recorded SHA-256.

The shell wrappers (``release/bin/package-frontend.sh``,
``release/bin/unpack-frontend.sh``) call these routines; nothing security-
sensitive is parsed with ad-hoc shell string handling.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tarfile
from typing import Any

from . import checksums

_CHECKSUM_LINE_RE = re.compile(r"^([0-9a-f]{64})  (.*)$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _path_issues(member: tarfile.TarInfo) -> list[dict[str, str]]:
    """Return safety issues for a single tar member."""
    issues: list[dict[str, str]] = []
    name = member.name
    if name.startswith("/") or _DRIVE_RE.match(name):
        issues.append(
            {
                "code": "ABSOLUTE_PATH",
                "field": name,
                "message": f"tar entry has an absolute path: {name!r}",
            }
        )
    parts = name.split("/")
    if ".." in parts:
        issues.append(
            {
                "code": "TRAVERSAL_PATH",
                "field": name,
                "message": f"tar entry escapes the destination: {name!r}",
            }
        )
    if member.islnk() or member.issym():
        issues.append(
            {"code": "LINK_ENTRY", "field": name, "message": f"tar entry is a link: {name!r}"}
        )
    if member.ischr() or member.isblk() or member.isfifo():
        issues.append(
            {
                "code": "DEVICE_ENTRY",
                "field": name,
                "message": f"tar entry is a device/FIFO: {name!r}",
            }
        )
    if member.type == ord("S"):  # GNU socket/sparse typeflag; tarfile has no SOCKTYPE constant
        issues.append(
            {"code": "SOCKET_ENTRY", "field": name, "message": f"tar entry is a socket: {name!r}"}
        )
    return issues


def validate_archive(archive_path: str) -> tuple[bool, list[dict[str, str]]]:
    """Return ``(ok, issues)`` for a frontend archive before extraction.

    Raises nothing; archive/IO errors are reported as issues so callers fail
    closed deterministically.
    """
    issues: list[dict[str, str]] = []
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                issues.extend(_path_issues(member))
    except (tarfile.TarError, OSError) as exc:
        return False, [{"code": "ARCHIVE_ERROR", "field": archive_path, "message": str(exc)}]
    return (not issues, issues)


def verify_checksum_manifest(manifest_path: str, root: str) -> tuple[bool, list[dict[str, str]]]:
    """Verify a sorted per-file checksum manifest against an extracted tree.

    Every line must be ``<64-hex>  <relative-path>``, the manifest must be
    sorted by path, and each referenced file must exist under ``root`` with a
    matching SHA-256. Returns ``(ok, issues)``.
    """
    issues: list[dict[str, str]] = []
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            lines = [line.rstrip("\n") for line in handle if line.strip()]
    except OSError as exc:
        return False, [{"code": "READ_ERROR", "field": manifest_path, "message": str(exc)}]

    entries: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        match = _CHECKSUM_LINE_RE.match(line)
        if not match:
            issues.append(
                {
                    "code": "MALFORMED_MANIFEST_LINE",
                    "field": manifest_path,
                    "message": f"line {index + 1} is not '<sha256>  <path>': {line!r}",
                }
            )
            continue
        digest, rel_path = match.groups()
        if rel_path.startswith("/") or _DRIVE_RE.match(rel_path) or ".." in rel_path.split("/"):
            issues.append(
                {
                    "code": "UNSAFE_MANIFEST_PATH",
                    "field": rel_path,
                    "message": f"unsafe manifest path: {rel_path!r}",
                }
            )
            continue
        entries.append((rel_path, digest))

    paths = [entry[0] for entry in entries]
    if sorted(paths) != paths:
        issues.append(
            {
                "code": "UNSORTED_MANIFEST",
                "field": manifest_path,
                "message": "checksum manifest is not sorted by path",
            }
        )

    for rel_path, digest in entries:
        target = os.path.join(root, rel_path)
        if not os.path.isfile(target):
            issues.append(
                {
                    "code": "MANIFEST_FILE_MISSING",
                    "field": rel_path,
                    "message": f"manifest references missing file: {rel_path}",
                }
            )
            continue
        actual = checksums.sha256_file(target)
        if actual != digest:
            issues.append(
                {
                    "code": "MANIFEST_CHECKSUM_MISMATCH",
                    "field": rel_path,
                    "message": f"file {rel_path} sha256 {actual} != recorded {digest}",
                }
            )

    issues.sort(key=lambda item: (item["field"], item["code"]))
    return (not issues, issues)


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _cmd_validate(args: argparse.Namespace) -> int:
    ok, issues = validate_archive(args.archive)
    print(json.dumps({"valid": ok, "issues": issues}, sort_keys=True))
    return 0 if ok else 1


def _cmd_verify_manifest(args: argparse.Namespace) -> int:
    ok, issues = verify_checksum_manifest(args.manifest, args.root)
    print(json.dumps({"valid": ok, "issues": issues}, sort_keys=True))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.frontend",
        description="Safe frontend archive validation and checksum verification.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate a tar.gz archive before extraction")
    validate.add_argument("--archive", required=True, metavar="FILE")
    validate.set_defaults(func=_cmd_validate)

    verify_manifest = sub.add_parser(
        "verify-manifest", help="verify a sorted per-file checksum manifest"
    )
    verify_manifest.add_argument("--manifest", required=True, metavar="FILE")
    verify_manifest.add_argument("--root", required=True, metavar="DIR")
    verify_manifest.set_defaults(func=_cmd_verify_manifest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
