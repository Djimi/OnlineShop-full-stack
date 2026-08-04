#!/usr/bin/env bash
set -euo pipefail

# Read-only frontend hosting hardening verification (Pass 3, subphase 3.5).
# Proves the production frontend is served from an S3 REST origin behind a
# CloudFront Origin Access Control (OAC), that direct public bucket access is
# blocked, and that the SPA fallback (404 -> /index.html) is preserved.
#
# Desired state (fail closed on any drift):
#   - the S3 origin uses a REST endpoint, NOT the public website endpoint;
#   - the S3 origin is attached to an Origin Access Control;
#   - the bucket policy allows cloudfront.amazonaws.com s3:GetObject with
#     aws:SourceArn == the production distribution ARN and grants no
#     Principal "*" public read;
#   - the bucket public access block is fully enabled;
#   - the bucket website configuration is absent;
#   - the distribution has a 404 -> 200 /index.html custom error response.
#
# Identity preflight is mandatory. Nothing is ever mutated.
#
# Usage:
#   bash scripts/verify-frontend-oac.sh [--json]

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RELEASE_ROOT="$SCRIPT_DIR/../plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
# shellcheck source=config/production.env
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$SCRIPT_DIR/config/production.env"
# shellcheck source=lib/lifecycle.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$SCRIPT_DIR/lib/lifecycle.sh"

JSON_ONLY=0
case "${1:-}" in
  "") ;;
  --json) JSON_ONLY=1 ;;
  --help) echo "Usage: $0 [--json]"; exit 0 ;;
  *) echo "Usage: $0 [--json]" >&2; exit 1 ;;
esac

lc_init
lc_require_environment production
lc_verify_identity

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Distribution config (CloudFront is a global service; the mandatory
# eu-north-1 region value still reaches the global endpoint).
DIST=$("${LC_AWS[@]}" cloudfront get-distribution --id "$LC_CLOUDFRONT_DISTRIBUTION" \
  --query 'Distribution.DistributionConfig' --output json 2>/dev/null || echo "null")
[ "$DIST" != "null" ] || { echo "ERROR: cannot read CloudFront distribution $LC_CLOUDFRONT_DISTRIBUTION" >&2; exit 1; }

# Bucket policy: decode the embedded JSON string; absent -> unconfigured.
if POLICY_RAW=$("${LC_AWS[@]}" s3api get-bucket-policy --bucket "$LC_FRONTEND_BUCKET" --output json 2>/dev/null); then
  printf '%s' "$POLICY_RAW" | jq -r '.Policy' > "$TMP/policy.json"
else
  printf 'null' > "$TMP/policy.json"
fi

# Public access block: absent -> unconfigured.
if "${LC_AWS[@]}" s3api get-public-access-block --bucket "$LC_FRONTEND_BUCKET" >/dev/null 2>&1; then
  "${LC_AWS[@]}" s3api get-public-access-block --bucket "$LC_FRONTEND_BUCKET" \
    --output json > "$TMP/pab.json"
else
  printf 'null' > "$TMP/pab.json"
fi

# Website configuration: must be ABSENT in the hardened state.
if "${LC_AWS[@]}" s3api get-bucket-website --bucket "$LC_FRONTEND_BUCKET" >/dev/null 2>&1; then
  "${LC_AWS[@]}" s3api get-bucket-website --bucket "$LC_FRONTEND_BUCKET" \
    --output json > "$TMP/website.json"
else
  printf 'null' > "$TMP/website.json"
fi

printf '%s' "$DIST" > "$TMP/dist.json"
ARGS=(verify --distribution "$TMP/dist.json")
if [ "$(jq -r 'type' "$TMP/policy.json")" != "null" ]; then ARGS+=(--bucket-policy "$TMP/policy.json"); fi
if [ "$(jq -r 'type' "$TMP/pab.json")" != "null" ]; then ARGS+=(--public-access-block "$TMP/pab.json"); fi
if [ "$(jq -r 'type' "$TMP/website.json")" != "null" ]; then ARGS+=(--website "$TMP/website.json"); fi

CHECK=$(PYTHONPATH="$RELEASE_ROOT/src" python3 -m release_contract.frontend_hosting "${ARGS[@]}") || true
printf '%s' "$CHECK" | jq -e 'type == "object"' >/dev/null 2>&1 || {
  echo "ERROR: frontend hosting decision layer produced no valid result (see stderr)" >&2
  exit 1
}
VALID=$(printf '%s' "$CHECK" | jq -r '.valid')

if [ "$JSON_ONLY" = "1" ]; then
  jq -n \
    --argjson check "$CHECK" \
    --argjson distribution "$DIST" \
    --argjson policy "$(cat "$TMP/policy.json")" \
    --argjson publicAccessBlock "$(cat "$TMP/pab.json")" \
    --argjson website "$(cat "$TMP/website.json")" \
    '{valid: $check.valid, issues: $check.issues, distribution: $distribution,
      bucketPolicy: $policy, publicAccessBlock: $publicAccessBlock, website: $website}'
  exit "$([ "$VALID" = "true" ] && echo 0 || echo 1)"
fi

echo "=== Frontend hosting hardening (read-only) ==="
printf '%s' "$CHECK" | jq -r '
  if .valid then "OK: S3 REST origin + OAC in place, public access blocked, SPA fallback preserved."
  else "DRIFT detected:",
    (.issues[] | "  [\(.code)] \(.field): \(.message)")
  end'
if [ "$VALID" != "true" ]; then
  exit 1
fi
