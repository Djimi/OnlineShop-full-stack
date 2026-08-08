#!/usr/bin/env bash
set -euo pipefail

# Digest-pins a production task definition revision (Pass 3, subphase 3.5).
# Copies the current definition and replaces ONLY the intended containers'
# image fields with `<registry>/<repository>@sha256:<digest>` references, then
# proves the transform is safe:
#   - every re-imaged container is digest-pinned (no floating tags);
#   - nothing except `image` changed anywhere (no unrelated runtime drift);
#   - no container was added or removed;
#   - every secrets[].valueFrom is preserved as a FULL arn:aws:secretsmanager:
#     ARN and is never repeated as plaintext in environment/command.
#
# Usage:
#   sanitize-task-definition.sh --input <td.json> --output <new-td.json> \
#     --set-image auth=<registry>/onlineshop-auth@sha256:<hex> \
#     [--set-image items=<registry>/onlineshop-items@sha256:<hex> ...]
#
# Exit 0 when the sanitized definition is written and the diff is clean;
# 1 when validation fails; 2 on usage/IO error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" >&2
}

INPUT=""
OUTPUT=""
SET_IMAGES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --input) INPUT="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --set-image) SET_IMAGES+=("$2"); shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

[ -n "$INPUT" ] && [ -n "$OUTPUT" ] || { usage; exit 2; }
[ "${#SET_IMAGES[@]}" -gt 0 ] || { echo "ERROR: at least one --set-image is required" >&2; usage; exit 2; }
rl_assert_regular_file "$INPUT" || exit 2

ARGS=(--input "$INPUT" --output "$OUTPUT")
for entry in "${SET_IMAGES[@]}"; do
  ARGS+=(--set-image "$entry")
done

RESULT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.sanitize sanitize "${ARGS[@]}") || {
  printf '%s' "$RESULT" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  echo "ERROR: task definition sanitization failed" >&2
  exit 1
}
printf '%s' "$RESULT" | jq -e '.valid == true' >/dev/null || {
  printf '%s' "$RESULT" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  echo "ERROR: task definition sanitization diff is not clean" >&2
  exit 1
}

CHANGED=$(printf '%s' "$RESULT" | jq -r '.changedFields | length')
echo "OK: sanitized task definition written to $OUTPUT ($CHANGED field(s) changed, image-only)."
printf '%s' "$RESULT" | jq -r '.changedFields[] | "  changed: \(.)"'
