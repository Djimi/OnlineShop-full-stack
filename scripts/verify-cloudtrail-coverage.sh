#!/usr/bin/env bash
set -euo pipefail

# Read-only CloudTrail management-event coverage audit (Pass 3, subphase 3.5).
# Proves CloudTrail logs the management-plane mutations the release pipeline
# makes (ECS, ECR, S3, CloudFront, IAM, Secrets Manager) so sanitized AWS
# request IDs can be correlated with the GitHub evidence plane.
#
# Requirements (fail closed on any drift):
#   - at least one trail exists and is currently logging;
#   - at least one trail logs management events (IncludeManagementEvents with
#     ReadWriteType All/WriteOnly);
#   - at least one trail is multi-region (global IAM/CloudFront management
#     events are delivered from us-east-1 and would be missed otherwise);
#   - the trail delivers to an S3 bucket or CloudWatch Logs group.
#
# Identity preflight is mandatory. Nothing is ever mutated.
#
# Usage:
#   bash scripts/verify-cloudtrail-coverage.sh [--json]

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

TRAILS=$("${LC_AWS[@]}" cloudtrail describe-trails --query 'trailList' --output json 2>/dev/null || echo '[]')
jq -n --argjson trails "$TRAILS" '{trails: $trails}' > "$TMP/input.json"

STATUSES=$(jq -n '{}')
SELECTORS=$(jq -n '{}')
while IFS= read -r name; do
  [ -n "$name" ] || continue
  status=$("${LC_AWS[@]}" cloudtrail get-trail-status --name "$name" \
    --query '{IsLogging: IsLogging, LatestDeliveryTime: LatestDeliveryTime, LatestDeliveryError: LatestDeliveryError}' \
    --output json 2>/dev/null || echo '{}')
  STATUSES=$(jq --arg n "$name" --argjson s "$status" '. + {($n): $s}' <<<"$STATUSES")
  selectors=$("${LC_AWS[@]}" cloudtrail get-event-selectors --trail-name "$name" \
    --query 'EventSelectors[].{IncludeManagementEvents: IncludeManagementEvents, ReadWriteType: ReadWriteType}' \
    --output json 2>/dev/null || echo '[]')
  SELECTORS=$(jq --arg n "$name" --argjson s "$selectors" '. + {($n): $s}' <<<"$SELECTORS")
done < <(jq -r '.trails[]?.Name' "$TMP/input.json")

printf '%s' "$TRAILS" > "$TMP/trails.json"
printf '%s' "$STATUSES" > "$TMP/statuses.json"
printf '%s' "$SELECTORS" > "$TMP/selectors.json"

CHECK=$(PYTHONPATH="$RELEASE_ROOT/src" python3 -m release_contract.cloudtrail verify \
  --trails "$TMP/trails.json" --statuses "$TMP/statuses.json" --selectors "$TMP/selectors.json") || true
printf '%s' "$CHECK" | jq -e 'type == "object"' >/dev/null 2>&1 || {
  echo "ERROR: CloudTrail coverage decision layer produced no valid result (see stderr)" >&2
  exit 1
}
VALID=$(printf '%s' "$CHECK" | jq -r '.valid')

if [ "$JSON_ONLY" = "1" ]; then
  jq -n \
    --argjson check "$CHECK" \
    --argjson trails "$TRAILS" --argjson statuses "$STATUSES" --argjson selectors "$SELECTORS" \
    '{valid: $check.valid, issues: $check.issues, coveredServices: $check.coveredServices,
      trails: $trails, statuses: $statuses, selectors: $selectors}'
  exit "$([ "$VALID" = "true" ] && echo 0 || echo 1)"
fi

echo "=== CloudTrail management-event coverage (read-only) ==="
printf '%s' "$CHECK" | jq -r '
  if .valid then "OK: a multi-region trail logs management events (covers the ECS/ECR/S3/CloudFront/IAM/Secrets Manager control-plane mutations) and delivers (" + (.coveredServices | join(", ")) + ")."
  else "COVERAGE GAP:",
    (.issues[] | "  [\(.code)] \(.field): \(.message)")
  end'
if [ "$VALID" != "true" ]; then
  exit 1
fi
