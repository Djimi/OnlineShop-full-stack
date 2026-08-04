"""Command-line validation of OnlineShop release manifests.

Usage:
    validate-manifest <manifest.json> [--schema <schema.json>]
                     [--check-checksum <expected-sha256>] [--human]

Exit codes:
    0  manifest is valid (and, when given, the checksum matches)
    1  manifest is invalid (issues are printed to stdout as JSON)
    2  usage or I/O error (message on stderr)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .validate import SCHEMA_PATH, validate_file


def _default_schema_path() -> str:
    return SCHEMA_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate-manifest",
        description=(
            "Validate an OnlineShop release manifest against the versioned "
            "release contract."
        ),
    )
    parser.add_argument(
        "manifest", metavar="MANIFEST", help="path to the release manifest JSON file"
    )
    parser.add_argument(
        "--schema",
        default=_default_schema_path(),
        metavar="SCHEMA",
        help="path to the release manifest JSON Schema (default: bundled schema)",
    )
    parser.add_argument(
        "--check-checksum",
        metavar="SHA256",
        help="expected manifest SHA-256; the run fails when the document checksum differs",
    )
    parser.add_argument(
        "--human",
        action="store_true",
        help="also print human-readable issue lines to stderr",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not os.path.isfile(args.manifest):
        print(f"error: no such file: {args.manifest}", file=sys.stderr)
        return 2
    if not os.path.isfile(args.schema):
        print(f"error: no such schema file: {args.schema}", file=sys.stderr)
        return 2

    result = validate_file(args.manifest, schema_path=args.schema)

    checksum_ok = True
    if args.check_checksum:
        expected = args.check_checksum
        actual = result.checksum
        checksum_ok = actual is not None and actual == expected
        if not checksum_ok:
            result.valid = False
            result.issues.append(
                {
                    "code": "CHECKSUM_MISMATCH",
                    "field": "$",
                    "message": f"manifest checksum {actual!r} does not match expected {expected!r}",
                }
            )

    output: dict[str, Any] = {
        "valid": result.valid,
        "file": args.manifest,
        "schemaVersion": 1,
        "issues": result.issues,
        "checksum": result.checksum,
    }
    if not result.valid:
        output["errorCount"] = len(result.issues)

    if args.human:
        for issue in result.issues:
            print(f"[{issue['code']}] {issue['field']}: {issue['message']}", file=sys.stderr)

    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if result.valid else 1


if __name__ == "__main__":
    sys.exit(main())
