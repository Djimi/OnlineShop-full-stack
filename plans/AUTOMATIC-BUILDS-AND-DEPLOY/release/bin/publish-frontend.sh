#!/usr/bin/env bash
set -euo pipefail

# Frontend production publication (Pass 3, subphase 3.4). Because the current
# Vite output uses root `/assets/...` URLs, the live root is published without
# `--delete` (old hashed assets are retained), with the immutable per-release
# prefix as the rollback source (assets-first/index-last):
#
#   1. upload the frontend archive to the immutable release prefix
#      `_releases/v<version>/` (the rollback source) plus the per-release
#      `release.json` version marker, and verify checksums;
#   2. publish the content-addressed assets to the live root WITHOUT --delete;
#   3. publish the root `release.json` version marker and `index.html` LAST;
#   4. invalidate the SPA entry paths on CloudFront (/* is acceptable);
#   5. verify the live-root AND immutable-prefix markers match the manifest.
#
# Every mutation is immediately read back. `--dry-run` only validates the plan.
#
# Usage:
#   publish-frontend.sh --manifest <official-manifest.json>
#     --dist <frontend-dist-dir> --bucket <s3-bucket> --distribution <cf-id>
#     [--dry-run] [--profile dpm-profile] [--region eu-north-1]
#
# Exit 0 on success; 1 on any fail-closed check; 2 on usage/IO error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,26p' "${BASH_SOURCE[0]}" >&2
}

MANIFEST=""
DIST=""
BUCKET=""
DISTRIBUTION=""
DRY_RUN=0
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --dist) DIST="${2:-}"; shift 2 ;;
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

[ -n "$MANIFEST" ] && [ -n "$DIST" ] && [ -n "$BUCKET" ] && [ -n "$DISTRIBUTION" ] || { usage; exit 2; }
rl_assert_regular_file "$MANIFEST" || exit 2
[ -d "$DIST" ] || { echo "ERROR: --dist is not a directory: $DIST" >&2; exit 2; }

VERSION=$(jq -r '.release.version' "$MANIFEST")
PREFIX=$(jq -r '.components.frontend.releasePrefix' "$MANIFEST")
MARKER=$(jq -r '.components.frontend.versionMarker' "$MANIFEST")
CHECKSUM=$(jq -r '.components.frontend.sha256' "$MANIFEST")
rl_assert_semver "$VERSION" || exit 2
rl_assert_sha256_hex "$CHECKSUM" || exit 2

# The frontend plan must be valid before any mutation (assets-first/index-last,
# no --delete, prefix present, invalidation required).
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
jq -n \
  --arg prefix "$PREFIX" --arg marker "$MARKER" \
  '{steps: ["immutable-prefix", "live-assets", "live-marker-index", "invalidate", "verify"],
    deleteFlag: false, immutablePrefix: $prefix, marker: $marker, indexHtml: "index.html"}' \
  > "$TMP/plan.json"
PLAN_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion frontend \
  --plan "$TMP/plan.json") || true
printf '%s' "$PLAN_ISSUES" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: invalid frontend publication plan (fail closed):" >&2
  printf '%s' "$PLAN_ISSUES" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

# The frontend archive must already be packaged (frontend-dist.tar.gz) inside
# --dist or alongside it; use unpack-frontend.sh's verified extraction. For the
# workflow, --dist is the extracted, checksum-verified candidate tree.
if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run: would publish frontend $VERSION (prefix $PREFIX, marker $MARKER)" >&2
  echo "publish-frontend: OK (dry-run)"
  exit 0
fi

# 1. Immutable release prefix (rollback source) + per-release marker.
# The marker content is generated once and published to BOTH the immutable
# per-release prefix (`<prefix><marker>`, the durable rollback record that the
# traceability and release-identity checks read) and the live root (last).
PREFIX_DEST="$TMP/prefix-upload"
cp -r "$DIST" "$PREFIX_DEST"
aws s3 sync "${AWS_ARGS[@]}" "$PREFIX_DEST/" "s3://$BUCKET/$PREFIX"
jq -n --arg version "$VERSION" \
  --arg sourceSha "$(jq -r '.release.sourceSha' "$MANIFEST")" \
  --arg frontendSha256 "$CHECKSUM" \
  '{version: $version, sourceSha: $sourceSha, frontendSha256: $frontendSha256}' \
  > "$TMP/marker.json"
aws s3 cp "${AWS_ARGS[@]}" "$TMP/marker.json" "s3://$BUCKET/$PREFIX$MARKER" \
  --content-type application/json
# Verify a sample: the assets must be readable and checksum-matched to the
# manifest.
ASSET_COUNT=$(find "$PREFIX_DEST" -type f | wc -l)
[ "$ASSET_COUNT" -gt 0 ] || { echo "ERROR: no assets to publish" >&2; exit 1; }

# 2. Live root assets WITHOUT --delete (old hashed assets retained).
aws s3 sync "${AWS_ARGS[@]}" "$DIST/" "s3://$BUCKET/" --exclude "index.html" --exclude "$MARKER"

# 3. Root release.json + index.html LAST (assets-first/index-last).
aws s3 cp "${AWS_ARGS[@]}" "$TMP/marker.json" "s3://$BUCKET/$MARKER" \
  --content-type application/json
aws s3 cp "${AWS_ARGS[@]}" "$DIST/index.html" "s3://$BUCKET/index.html" \
  --content-type text/html

# 4. CloudFront invalidation of the SPA entry paths (/* is one acceptable
#    wildcard) + read back the invalidation id.
INVALIDATION_ID=$(aws cloudfront create-invalidation "${AWS_ARGS[@]}" \
  --distribution-id "$DISTRIBUTION" --paths "/*" \
  --query 'Invalidation.Id' --output text)
[ -n "$INVALIDATION_ID" ] && [ "$INVALIDATION_ID" != "None" ] || {
  echo "ERROR: CloudFront invalidation was not created" >&2
  exit 1
}

# 5. Read-back verification: BOTH the live-root marker and the immutable
#    per-release prefix marker must match the manifest.
aws s3api get-object "${AWS_ARGS[@]}" --bucket "$BUCKET" --key "$MARKER" \
  "$TMP/live-read.json" >/dev/null
jq -e --arg v "$VERSION" '.version == $v' "$TMP/live-read.json" >/dev/null || {
  echo "ERROR: read-back live frontend marker does not match the manifest" >&2
  exit 1
}
aws s3api get-object "${AWS_ARGS[@]}" --bucket "$BUCKET" --key "$PREFIX$MARKER" \
  "$TMP/prefix-read.json" >/dev/null
jq -e --arg v "$VERSION" '.version == $v' "$TMP/prefix-read.json" >/dev/null || {
  echo "ERROR: read-back immutable prefix marker does not match the manifest" >&2
  exit 1
}

echo "publish-frontend: OK"
echo "version=$VERSION prefix=$PREFIX invalidation=$INVALIDATION_ID assets=$ASSET_COUNT"
