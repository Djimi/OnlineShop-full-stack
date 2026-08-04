#!/usr/bin/env bash
set -euo pipefail

# Verifies that all three backend images form one canonical producer set
# (Pass 3, subphase 3.2, Decision 11).
#
# Every backend must have the candidate tag, its OCI revision must equal the
# candidate SHA, its producer event/ref must be a trusted main push, the Items
# `common` revision must equal the SHA, and all producer run ids must be
# identical (one canonical producer run, never a mixed set).
#
# Usage:
#   verify-producer-set.sh --sha <full-sha> --registry <registry>
#                          [--profile <p>] [--region <r>]
#
# Backend repositories are resolved from the canonical component map. Prints
# `canonical=true` on success; exits non-zero (fail closed) otherwise.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  sed -n '2,18p' "${BASH_SOURCE[0]}" >&2
}

SHA=""
REGISTRY=""
AWS_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) SHA="${2:-}"; shift 2 ;;
    --registry) REGISTRY="${2:-}"; shift 2 ;;
    --profile) AWS_ARGS+=(--profile "${2:-}"); shift 2 ;;
    --region) AWS_ARGS+=(--region "${2:-}"); shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$SHA" ] && [ -n "$REGISTRY" ] || { usage; exit 2; }

# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"
rl_assert_full_sha "$SHA" || { echo "ERROR: invalid --sha" >&2; exit 2; }

TAG="sha-$SHA"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

jq -n '{}' > "$TMP/backends.json"
for entry in "auth:onlineshop-auth" "items:onlineshop-items" "apiGateway:onlineshop-api-gateway"; do
  component="${entry%%:*}"
  repository="${entry##*:}"
  RECORD=$(bash "$RELEASE/bin/image-labels.sh" \
    --registry "$REGISTRY" --repository "$repository" --tag "$TAG" \
    "${AWS_ARGS[@]}" 2>/dev/null) || {
    echo "ERROR: cannot read labels for $repository:$TAG" >&2
    exit 1
  }
  TMP_RECORD="$TMP/$component.json"
  printf '%s' "$RECORD" > "$TMP_RECORD"
  jq --arg component "$component" --slurpfile rec "$TMP_RECORD" \
    '. + {($component): $rec[0]}' "$TMP/backends.json" > "$TMP/next.json"
  mv "$TMP/next.json" "$TMP/backends.json"
done

RESULT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.candidate set-check \
  --backends "$TMP/backends.json" --sha "$SHA") || {
  echo "ERROR: backend images do not form a canonical producer set (fail closed):" >&2
  printf '%s' "$RESULT" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

if [ "$(printf '%s' "$RESULT" | jq -r '.canonical')" = "true" ]; then
  echo "canonical=true"
else
  echo "ERROR: backend images do not form a canonical producer set (fail closed):" >&2
  printf '%s' "$RESULT" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
fi
