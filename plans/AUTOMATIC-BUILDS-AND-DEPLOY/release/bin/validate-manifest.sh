#!/usr/bin/env bash
set -euo pipefail

# Thin CLI wrapper over the release manifest validator. All arguments are passed
# to Python through argv (an argument array), never interpolated into command
# strings. Exit codes match the Python CLI contract: 0 valid, 1 invalid,
# 2 usage/IO error (missing manifest, missing schema, malformed argument).
#
# Usage: validate-manifest.sh <manifest.json> [--schema <schema.json>]
#        validate-manifest.sh <manifest.json> --check-checksum <sha256>

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$SCRIPT_DIR/release-input.sh"

RELEASE_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)

# Usage/IO errors exit 2 per the documented contract; manifest invalidity is
# reported by the Python CLI as exit 1, so the two must never be conflated.
rl_usage_error() {
  echo "ERROR: $*" >&2
  exit 2
}

SCHEMA_ARG=""
SCHEMA=""
MANIFEST=""
CHECK_SUM=""
PASSTHROUGH=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --schema)
      [ "$#" -ge 2 ] || rl_usage_error "--schema requires a path argument"
      rl_assert_regular_file "$2" || exit 2
      SCHEMA_ARG="--schema"
      SCHEMA="$2"
      shift 2
      ;;
    --check-checksum)
      [ "$#" -ge 2 ] || rl_usage_error "--check-checksum requires a SHA-256 argument"
      rl_assert_sha256_hex "$2" || exit 2
      CHECK_SUM="$2"
      shift 2
      ;;
    -h | --help)
      echo "Usage: $0 <manifest.json> [--schema <schema.json>] [--check-checksum <sha256>]" >&2
      exit 0
      ;;
    --)
      shift
      while [ "$#" -gt 0 ]; do
        [ -z "$MANIFEST" ] || rl_usage_error "only one manifest path is allowed"
        MANIFEST="$1"
        shift
      done
      ;;
    -*)
      # Forward any other Python CLI flag (e.g. --human) verbatim through argv.
      PASSTHROUGH+=("$1")
      shift
      ;;
    *)
      [ -z "$MANIFEST" ] || rl_usage_error "only one manifest path is allowed"
      MANIFEST="$1"
      shift
      ;;
  esac
done

[ -n "$MANIFEST" ] || { echo "Usage: $0 <manifest.json> [--schema <schema.json>] [--check-checksum <sha256>]" >&2; exit 2; }
rl_assert_regular_file "$MANIFEST" || exit 2

ARGS=("$MANIFEST")
if [ -n "$SCHEMA_ARG" ]; then
  ARGS+=("--schema" "$SCHEMA")
fi
if [ -n "${CHECK_SUM:-}" ]; then
  ARGS+=("--check-checksum" "$CHECK_SUM")
fi
if [ "${#PASSTHROUGH[@]}" -gt 0 ]; then
  ARGS+=("${PASSTHROUGH[@]}")
fi

PYTHONPATH="$RELEASE_ROOT/src" exec python3 -m release_contract.cli "${ARGS[@]}"
