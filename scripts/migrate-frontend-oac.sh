#!/usr/bin/env bash
set -euo pipefail

# S3 REST origin + CloudFront OAC migration/hardening tool (Pass 3, subphase
# 3.5). Replaces the public S3 website origin with an S3 REST origin behind an
# Origin Access Control, blocks direct public bucket access, and preserves the
# SPA fallback through the distribution's 404 -> /index.html custom error
# response.
#
# Every mutation is immediately followed by a describe/get/list read-back and
# the run fails closed on any drift. Identity preflight is mandatory. The run
# is fully planned and **NOT applied in subphase 3.5** — application happens in
# the consolidated verification pass after a fail-closed
# `scripts/verify-frontend-oac.sh` gate.
#
# Usage:
#   bash scripts/migrate-frontend-oac.sh --dry-run        # print the plan only
#   bash scripts/migrate-frontend-oac.sh --apply          # mutate + read back
#
# `--apply` requires --profile/--region defaults (dpm-profile/eu-north-1) and
# the production environment guard; it is intentionally explicit so an
# operator cannot migrate the frontend by accident.

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RELEASE_ROOT="$SCRIPT_DIR/../plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
# shellcheck source=config/production.env
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$SCRIPT_DIR/config/production.env"
# shellcheck source=lib/lifecycle.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$SCRIPT_DIR/lib/lifecycle.sh"

MODE=""
case "${1:-}" in
  --dry-run) MODE="dry-run" ;;
  --apply) MODE="apply" ;;
  --help) echo "Usage: $0 (--dry-run | --apply)"; exit 0 ;;
  *) echo "Usage: $0 (--dry-run | --apply)" >&2; exit 1 ;;
esac

lc_init
lc_require_environment production
lc_verify_identity

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

OAC_NAME="onlineshop-frontend-oac"
REST_DOMAIN="$LC_FRONTEND_BUCKET.s3.eu-north-1.amazonaws.com"
BUCKET_ARN="arn:aws:s3:::$LC_FRONTEND_BUCKET"
DISTRIBUTION_ARN="arn:aws:cloudfront::$LC_ACCOUNT_ID:distribution/$LC_CLOUDFRONT_DISTRIBUTION"

DIST=$("${LC_AWS[@]}" cloudfront get-distribution --id "$LC_CLOUDFRONT_DISTRIBUTION" \
  --query 'Distribution.DistributionConfig' --output json 2>/dev/null || echo "null")
[ "$DIST" != "null" ] || { echo "ERROR: cannot read CloudFront distribution $LC_CLOUDFRONT_DISTRIBUTION" >&2; exit 1; }

PLAN=$(PYTHONPATH="$RELEASE_ROOT/src" python3 -m release_contract.frontend_hosting plan \
  --distribution <(printf '%s' "$DIST"))

if [ "$MODE" = "dry-run" ]; then
  echo "=== Frontend OAC migration plan (dry-run; no mutation) ==="
  printf '%s' "$PLAN" | jq -r '.plan[] | "\(.step). \(.mutation)\n     read back: \(.readBack)"'
  exit 0
fi

echo "=== Frontend OAC migration (apply) ==="
fail() {
  echo "ERROR: $*" >&2
  exit 1
}

# 0. No-lockout preconditions: the current bucket policy must already permit
#    the post-switch fetch (public read or the CloudFront OAC) so the origin
#    switch cannot create an outage window. Fail before ANY mutation.
if POLICY_RAW=$("${LC_AWS[@]}" s3api get-bucket-policy --bucket "$LC_FRONTEND_BUCKET" \
  --output json 2>/dev/null); then
  printf '%s' "$POLICY_RAW" | jq -r '.Policy' > "$TMP/current-policy.json"
else
  printf 'null' > "$TMP/current-policy.json"
fi
PRECOND=$(PYTHONPATH="$RELEASE_ROOT/src" python3 -m release_contract.frontend_hosting \
  preconditions --bucket-policy "$TMP/current-policy.json") || true
printf '%s' "$PRECOND" | jq -e 'type == "object"' >/dev/null 2>&1 || {
  echo "ERROR: OAC migration precondition decision layer produced no valid result (see stderr)" >&2
  exit 1
}
printf '%s' "$PRECOND" | jq -e '.valid == true' >/dev/null || {
  echo "ERROR: OAC migration preconditions not met (no-lockout gate):" >&2
  printf '%s' "$PRECOND" | jq -r '.issues[] | "  [\(.code)] \(.message)"' >&2
  exit 1
}
echo "Preconditions OK: current bucket policy cannot lock out CloudFront after the origin switch."

# 1. Create the Origin Access Control (idempotent by name lookup first).
EXISTING_OAC=$("${LC_AWS[@]}" cloudfront list-origin-access-controls \
  --query "OriginAccessControlList.Items[?Name==\`$OAC_NAME\`].Id | [0]" --output text 2>/dev/null || true)
if lc_is_present "$EXISTING_OAC"; then
  OAC_ID="$EXISTING_OAC"
  echo "Reusing existing Origin Access Control $OAC_ID."
  "${LC_AWS[@]}" cloudfront get-origin-access-control --id "$OAC_ID" >/dev/null || fail "OAC read-back failed for $OAC_ID"
else
  OAC_ID=$("${LC_AWS[@]}" cloudfront create-origin-access-control \
    --origin-access-control-config "Name=$OAC_NAME,Description=OnlineShop frontend S3 REST origin (Pass 3.5),SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3" \
    --query 'OriginAccessControl.Id' --output text)
  "${LC_AWS[@]}" cloudfront get-origin-access-control --id "$OAC_ID" >/dev/null || fail "OAC creation read-back failed"
  echo "Created Origin Access Control $OAC_ID."
fi

# 2. Update the distribution: point the S3 origin at the REST endpoint and
#    attach the OAC, preserving every other setting.
DIST_CONFIG_ETAG=$("${LC_AWS[@]}" cloudfront get-distribution-config --id "$LC_CLOUDFRONT_DISTRIBUTION" \
  --query 'ETag' --output text)
DIST_CONFIG=$("${LC_AWS[@]}" cloudfront get-distribution-config --id "$LC_CLOUDFRONT_DISTRIBUTION" \
  --query 'DistributionConfig' --output json)
S3_ORIGIN=$(printf '%s' "$DIST_CONFIG" | jq \
  --arg domain "$REST_DOMAIN" --arg oac "$OAC_ID" --arg bucket "$LC_FRONTEND_BUCKET" \
  '(.Origins.Items[] | select((.Id == "s3-frontend") or (.DomainName | contains($bucket)))) | .DomainName = $domain | .OriginAccessControlId = $oac')
[ -n "$S3_ORIGIN" ] && [ "$S3_ORIGIN" != "null" ] || fail "cannot locate the S3 origin to re-point in distribution $LC_CLOUDFRONT_DISTRIBUTION"
UPDATED_CONFIG=$(printf '%s' "$DIST_CONFIG" | jq \
  --argjson s3origin "$S3_ORIGIN" --arg bucket "$LC_FRONTEND_BUCKET" \
  '.Origins.Items |= map(if (.Id == "s3-frontend") or (.DomainName | contains($bucket)) then $s3origin else . end)')
printf '%s' "$UPDATED_CONFIG" > "$TMP/dist-config.json"
"${LC_AWS[@]}" cloudfront update-distribution \
  --id "$LC_CLOUDFRONT_DISTRIBUTION" --if-match "$DIST_CONFIG_ETAG" \
  --distribution-config "file://$TMP/dist-config.json" >/dev/null
UPDATED_CFG=$("${LC_AWS[@]}" cloudfront get-distribution --id "$LC_CLOUDFRONT_DISTRIBUTION" \
  --query 'Distribution.DistributionConfig' --output json)
printf '%s' "$UPDATED_CFG" | jq -e --arg oac "$OAC_ID" --arg bucket "$LC_FRONTEND_BUCKET" \
  '.Origins.Items[] | select((.Id == "s3-frontend") or (.DomainName | contains($bucket))) | select(.OriginAccessControlId == $oac)' >/dev/null \
  || fail "distribution update read-back failed (S3 origin has no matching OAC)"
echo "Distribution updated to REST origin + OAC $OAC_ID."

# 3. CloudFront deployment is asynchronous: wait (bounded) for the
#    distribution to reach Deployed before tightening the bucket policy, so
#    the tool never claims success while the edge still serves the old origin.
CF_WAIT_ATTEMPTS="${CF_WAIT_ATTEMPTS:-20}"
CF_WAIT_INTERVAL="${CF_WAIT_INTERVAL:-15}"
cf_status=""
attempt=0
while [ "$cf_status" != "Deployed" ]; do
  attempt=$((attempt + 1))
  cf_status=$("${LC_AWS[@]}" cloudfront get-distribution --id "$LC_CLOUDFRONT_DISTRIBUTION" \
    --query 'Distribution.Status' --output text 2>/dev/null || echo "")
  [ "$cf_status" = "Deployed" ] && break
  [ "$attempt" -ge "$CF_WAIT_ATTEMPTS" ] && {
    fail "CloudFront distribution not Deployed after ${CF_WAIT_ATTEMPTS} polls (last status: ${cf_status:-unknown}); the migration is idempotent, re-run --apply to resume"
  }
  sleep "$CF_WAIT_INTERVAL"
done
echo "CloudFront distribution deployed (status: $cf_status)."

# 4. Replace the public-read bucket policy with the OAC service-principal policy.
jq -n \
  --arg bucketArn "$BUCKET_ARN" --arg distArn "$DISTRIBUTION_ARN" \
  '{Version: "2012-10-17", Statement: [{
      Sid: "AllowCloudFrontServicePrincipalReadOnly", Effect: "Allow",
      Principal: {Service: "cloudfront.amazonaws.com"}, Action: "s3:GetObject",
      Resource: ($bucketArn + "/*"),
      Condition: {StringEquals: {"aws:SourceArn": $distArn}}}]}' > "$TMP/bucket-policy.json"
"${LC_AWS[@]}" s3api put-bucket-policy --bucket "$LC_FRONTEND_BUCKET" \
  --policy "file://$TMP/bucket-policy.json" >/dev/null
POLICY_READBACK=$("${LC_AWS[@]}" s3api get-bucket-policy --bucket "$LC_FRONTEND_BUCKET" \
  --query 'Policy' --output text)
printf '%s' "$POLICY_READBACK" | jq -e --arg arn "$DISTRIBUTION_ARN" \
  '.Statement[] | select(.Principal.Service == "cloudfront.amazonaws.com") | select(.Condition.StringEquals."aws:SourceArn" == $arn)' >/dev/null \
  || fail "bucket policy read-back failed (OAC service-principal statement missing)"
echo "Bucket policy restricted to the CloudFront OAC."

# 5. Enable the full public access block.
"${LC_AWS[@]}" s3api put-public-access-block --bucket "$LC_FRONTEND_BUCKET" \
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" >/dev/null
PAB_READBACK=$("${LC_AWS[@]}" s3api get-public-access-block --bucket "$LC_FRONTEND_BUCKET" \
  --query 'PublicAccessBlockConfiguration' --output json)
printf '%s' "$PAB_READBACK" | jq -e '.BlockPublicAcls == true and .IgnorePublicAcls == true and .BlockPublicPolicy == true and .RestrictPublicBuckets == true' >/dev/null \
  || fail "public access block read-back failed"
echo "Public access block enabled."

# 6. Remove the website configuration (REST origin + OAC replaces it).
"${LC_AWS[@]}" s3api delete-bucket-website --bucket "$LC_FRONTEND_BUCKET" >/dev/null
if "${LC_AWS[@]}" s3api get-bucket-website --bucket "$LC_FRONTEND_BUCKET" >/dev/null 2>&1; then
  fail "website configuration still present after deletion"
fi
echo "Bucket website configuration removed."

# 7. Full fail-closed read-back.
echo "Running the full frontend hardening read-back..."
bash "$SCRIPT_DIR/verify-frontend-oac.sh" --json > "$TMP/final.json" || fail "final frontend hardening verification failed"
jq -e '.valid == true' "$TMP/final.json" >/dev/null || fail "final frontend hardening verification is not valid"
echo "OK: S3 REST origin + OAC migration applied and verified."
