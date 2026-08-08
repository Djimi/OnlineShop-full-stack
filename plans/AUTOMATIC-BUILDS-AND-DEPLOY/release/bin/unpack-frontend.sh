#!/usr/bin/env bash
set -euo pipefail

# Safely extracts a `frontend-dist.tar.gz` candidate artifact (Pass 3,
# subphase 3.2). Rejects traversal, links, and device/FIFO/socket entries
# *before* extraction using the release_contract.frontend validator, then
# extracts with normalized ownership and verifies the sorted per-file checksum
# manifest against the extracted tree.
#
# Usage:
#   unpack-frontend.sh --archive <frontend-dist.tar.gz>
#                      --manifest <frontend-dist.sha256>
#                      --dest <dir>
#
# Exit codes: 0 success, 1 unsafe archive/verification failure, 2 usage error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  sed -n '2,16p' "${BASH_SOURCE[0]}" >&2
}

ARCHIVE=""
MANIFEST=""
DEST=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive) ARCHIVE="${2:-}"; shift 2 ;;
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --dest) DEST="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$ARCHIVE" ] && [ -n "$DEST" ] || { usage; exit 2; }
[ -n "$MANIFEST" ] && [ -f "$MANIFEST" ] || { echo "ERROR: manifest not found: ${MANIFEST:-<none>}" >&2; exit 2; }
[ -f "$ARCHIVE" ] || { echo "ERROR: archive not found: $ARCHIVE" >&2; exit 1; }
mkdir -p "$DEST"

# Fail closed before extraction: traversal, links, and device entries are
# rejected here (never in a post-extraction cleanup step).
VALIDATE_OUTPUT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.frontend validate --archive "$ARCHIVE") || {
  echo "ERROR: unsafe frontend archive rejected:" >&2
  echo "$VALIDATE_OUTPUT" >&2
  exit 1
}
echo "$VALIDATE_OUTPUT" | jq -e '.valid == true' >/dev/null || {
  echo "ERROR: unsafe frontend archive rejected:" >&2
  echo "$VALIDATE_OUTPUT" >&2
  exit 1
}

tar -xzf "$ARCHIVE" -C "$DEST" --no-same-owner --no-same-permissions

if [ -n "$MANIFEST" ]; then
  VERIFY_OUTPUT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.frontend verify-manifest --manifest "$MANIFEST" --root "$DEST") || {
    echo "ERROR: frontend checksum manifest verification failed:" >&2
    echo "$VERIFY_OUTPUT" >&2
    exit 1
  }
  echo "$VERIFY_OUTPUT" | jq -e '.valid == true' >/dev/null || {
    echo "ERROR: frontend checksum manifest verification failed:" >&2
    echo "$VERIFY_OUTPUT" >&2
    exit 1
  }
fi

echo "Extracted and verified $ARCHIVE into $DEST"
