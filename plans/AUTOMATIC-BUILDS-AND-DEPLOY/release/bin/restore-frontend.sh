#!/usr/bin/env bash
set -euo pipefail

# Frontend rollback restore (Pass 3, subphase 3.6). Re-points the live S3/CloudFront
# root to the retained immutable release prefix of the selected official release
# (the bytes published assets-first during its original promotion). Nothing is
# re-packaged or re-uploaded; only the live `release.json` marker and
# `index.html` are restored from `_releases/v<version>/`, and the SPA entry paths
# are invalidated:
#
#   1. fetch the immutable prefix `release.json` + `index.html` and verify the
#      prefix marker matches the target manifest (the selection already proved
#      the prefix exists at the recorded bytes);
#   2. publish the prefix marker + `index.html` to the live root WITHOUT --delete
#      (old hashed assets are retained — every historical asset stays readable);
#   3. invalidate the SPA entry paths on CloudFront (/* is acceptable);
#   4. read back the live-root marker and verify it matches the manifest.
#
# Every mutation is immediately read back. `--dry-run` only validates the plan.
#
# Usage:
#   restore-frontend.sh --manifest <target-official-manifest.json>
#     --bucket <s3-bucket> --distribution <cf-id>
#     [--dry-run] [--profile dpm-profile] [--region eu-north-1]
#
# Exit 0 on success; 1 on any fail-closed check; 2 on usage/IO error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,28p' "${BASH_SOURCE[0]}" >&2
}

MANIFEST=""
BUCKET=""
DISTRIBUTION=""
DRY_RUN=0
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --bucket) BUCKET="${2:-}"; shift 2 ;;
    --distribution) DISTRIBUTION="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

[ -n "$MANIFEST" ] && [ -n "$BUCKET" ] && [ -n "$DISTRIBUTION" ] || { usage; exit 2; }
rl_assert_regular_file "$MANIFEST" || exit 2
[ -f "$REPO_ROOT/scripts/config/production.env" ] || {
  echo "ERROR: missing scripts/config/production.env" >&2
  exit 1
}
# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/config/production.env"
[ "$LC_PROFILE" = "dpm-profile" ] && [ "$LC_REGION" = "eu-north-1" ] || {
  echo "ERROR: scripts/config/production.env profile/region drift" >&2
  exit 1
}

VERSION=$(jq -r '.release.version' "$MANIFEST")
PREFIX=$(jq -r '.components.frontend.releasePrefix' "$MANIFEST")
MARKER=$(jq -r '.components.frontend.versionMarker' "$MANIFEST")
rl_assert_semver "$VERSION" || exit 2

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- Mandatory identity preflight ------------------------------------------
set +e
IDENTITY_ACCOUNT=$(aws sts get-caller-identity "${AWS_ARGS[@]}" --query 'Account' --output text 2>"$TMP/identity.err")
RC=$?
set -e
if [ "$RC" -ne 0 ] || [ -z "$IDENTITY_ACCOUNT" ]; then
  echo "ERROR: identity preflight failed (aws sts get-caller-identity):" >&2
  sed -n '1,3p' "$TMP/identity.err" >&2 || true
  exit 1
fi
[ "$IDENTITY_ACCOUNT" = "$LC_ACCOUNT_ID" ] || {
  echo "ERROR: identity preflight failed; account $IDENTITY_ACCOUNT != $LC_ACCOUNT_ID" >&2
  exit 1
}

# The restore plan must be valid before any mutation (restore-only: no --delete,
# prefix present, marker + index last, invalidation required).
jq -n \
  --arg prefix "$PREFIX" --arg marker "$MARKER" \
  '{steps: ["fetch-prefix", "live-marker-index", "invalidate", "verify"],
    deleteFlag: false, fromPrefix: $prefix, marker: $marker, indexHtml: "index.html"}' \
  > "$TMP/plan.json"
PLAN_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.rollback frontend-restore \
  --plan "$TMP/plan.json") || true
printf '%s' "$PLAN_ISSUES" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: invalid frontend restore plan (fail closed):" >&2
  printf '%s' "$PLAN_ISSUES" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run: would restore frontend $VERSION from $PREFIX (marker $MARKER)" >&2
  echo "restore-frontend: OK (dry-run)"
  exit 0
fi

# 1. Fetch the exact historical bytes from the immutable prefix and verify the
#    prefix marker matches the manifest.
aws s3api get-object "${AWS_ARGS[@]}" --bucket "$BUCKET" \
  --key "${PREFIX}${MARKER}" "$TMP/prefix-marker.json" >/dev/null || {
  echo "ERROR: immutable prefix marker ${PREFIX}${MARKER} is unavailable; cannot restore (fail closed)" >&2
  exit 1
}
aws s3api get-object "${AWS_ARGS[@]}" --bucket "$BUCKET" \
  --key "${PREFIX}index.html" "$TMP/prefix-index.html" >/dev/null || {
  echo "ERROR: immutable prefix index.html is unavailable; cannot restore (fail closed)" >&2
  exit 1
}
jq -e --arg v "$VERSION" \
  --arg sha "$(jq -r '.release.sourceSha' "$MANIFEST")" \
  --arg frontendSha256 "$(jq -r '.components.frontend.sha256' "$MANIFEST")" \
  '.version == $v and .sourceSha == $sha and .frontendSha256 == $frontendSha256' \
  "$TMP/prefix-marker.json" >/dev/null || {
  echo "ERROR: immutable prefix marker does not match the target manifest (fail closed)" >&2
  exit 1
}

# 2. Publish the prefix marker + index.html to the live root (assets are already
#    retained there; no --delete anywhere).
aws s3 cp "${AWS_ARGS[@]}" "$TMP/prefix-marker.json" "s3://$BUCKET/$MARKER" \
  --content-type application/json --checksum-algorithm SHA256
aws s3 cp "${AWS_ARGS[@]}" "$TMP/prefix-index.html" "s3://$BUCKET/index.html" \
  --content-type text/html --checksum-algorithm SHA256

# 3. CloudFront invalidation of the SPA entry paths + read back the id.
INVALIDATION_ID=$(aws cloudfront create-invalidation "${AWS_ARGS[@]}" \
  --distribution-id "$DISTRIBUTION" --paths "/*" \
  --query 'Invalidation.Id' --output text)
[ -n "$INVALIDATION_ID" ] && [ "$INVALIDATION_ID" != "None" ] || {
  echo "ERROR: CloudFront invalidation was not created" >&2
  exit 1
}

# 4. Read-back verification: the live-root marker must match the target manifest.
aws s3api get-object "${AWS_ARGS[@]}" --bucket "$BUCKET" --key "$MARKER" \
  "$TMP/live-read.json" >/dev/null
jq -e --arg v "$VERSION" \
  --arg sha "$(jq -r '.release.sourceSha' "$MANIFEST")" \
  --arg frontendSha256 "$(jq -r '.components.frontend.sha256' "$MANIFEST")" \
  '.version == $v and .sourceSha == $sha and .frontendSha256 == $frontendSha256' \
  "$TMP/live-read.json" >/dev/null || {
  echo "ERROR: read-back live frontend marker does not match the target manifest" >&2
  exit 1
}

echo "restore-frontend: OK"
echo "version=$VERSION prefix=$PREFIX invalidation=$INVALIDATION_ID"
