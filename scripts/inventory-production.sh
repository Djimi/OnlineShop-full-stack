#!/usr/bin/env bash
set -euo pipefail

# Read-only production inventory and config-consistency check
# (Pass 3, subphase 3.5). Describes every known production resource and
# compares it to the explicit non-secret identifiers in scripts/config/
# production.env. It NEVER creates, modifies, or deletes anything.
#
# Covered resources: VPC, subnets, security groups, ECS cluster + services
# (with Service Connect namespace), ALB/TG, RDS (existence, availability, and
# non-public accessibility), Secrets Manager references (names only — never
# values), log groups, execution role, ECR repositories, and the frontend S3
# bucket + CloudFront distribution.
#
# Identity preflight is mandatory: the script refuses to run unless
# `aws sts get-caller-identity` returns the configured production account.
#
# Usage:
#   bash scripts/inventory-production.sh [--json]
#
# Exit 0 when every expected identifier matches observed state; non-zero
# (fail closed) on any drift — including an AWS read failure (reported as
# "error", never disguised as a missing resource). `--json` prints the full
# machine-readable report (with both expected and observed values).

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RELEASE_ROOT="$SCRIPT_DIR/../plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
# shellcheck source=config/production.env
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$SCRIPT_DIR/config/production.env"
# shellcheck source=lib/lifecycle.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$SCRIPT_DIR/lib/lifecycle.sh"
# shellcheck source=lib/identifiers.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$SCRIPT_DIR/lib/identifiers.sh"

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

EXPECTED=$(identifiers_from_config "$SCRIPT_DIR/config/production.env")
OBSERVED=$(identifiers_observed "$SCRIPT_DIR/config/production.env")

CHECK=$(PYTHONPATH="$RELEASE_ROOT/src" python3 -m release_contract.environments inventory \
  --expected <(printf '%s' "$EXPECTED") --observed <(printf '%s' "$OBSERVED") || true)
printf '%s' "$CHECK" | jq -e 'type == "object"' >/dev/null 2>&1 || {
  echo "ERROR: inventory decision layer produced no valid result (see stderr)" >&2
  exit 1
}
VALID=$(printf '%s' "$CHECK" | jq -r '.valid')

# Additional production-only facts not part of the identifier schema (ECR
# repositories and the execution role are now part of the identifier
# comparison above).
CF_STATUS=$(id_value "${LC_AWS[@]}" cloudfront get-distribution --id "$LC_CLOUDFRONT_DISTRIBUTION" \
  --query 'Distribution.Status' --output text)
FB_EXISTS=missing
if "${LC_AWS[@]}" s3api head-bucket --bucket "$LC_FRONTEND_BUCKET" >/dev/null 2>&1; then
  FB_EXISTS=found
fi

SERVICE_NAMESPACES=$(jq -n '{}')
for service in "${LC_SERVICES[@]}"; do
  ns=$(id_value "${LC_AWS[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
    --services "$service" --query 'services[0].serviceConnectConfiguration.namespace' --output text)
  SERVICE_NAMESPACES=$(jq --arg s "$service" --arg n "$ns" '. + {($s): $n}' <<<"$SERVICE_NAMESPACES")
done

if [ "$JSON_ONLY" = "1" ]; then
  jq -n \
    --argjson expected "$EXPECTED" --argjson observed "$OBSERVED" \
    --argjson check "$CHECK" \
    --arg cloudfrontStatus "$CF_STATUS" \
    --arg frontendBucket "$FB_EXISTS" --argjson serviceNamespaces "$SERVICE_NAMESPACES" \
    '{valid: $check.valid, issues: $check.issues, expected: $expected, observed: $observed,
      extras: {cloudfrontStatus: $cloudfrontStatus,
               frontendBucket: $frontendBucket, serviceNamespaces: $serviceNamespaces}}'
  exit "$([ "$VALID" = "true" ] && echo 0 || echo 1)"
fi

echo "=== Production inventory (read-only) ==="
printf '%s' "$CHECK" | jq -r '
  if .valid then "OK: every expected production identifier matches observed state."
  else "DRIFT detected:",
    (.issues[] | "  [\(.code)] \(.field): \(.message)")
  end'
echo "--- Observed summary ---"
printf '%s' "$OBSERVED" | jq '{vpcId, cluster, services, dbInstance, targetGroupArn, albName, namespace}'
echo "--- Service Connect namespaces ---"
printf '%s' "$SERVICE_NAMESPACES" | jq -r 'to_entries[] | "  \(.key): \(.value)"'
echo "--- Frontend delivery ---"
echo "  CloudFront distribution $LC_CLOUDFRONT_DISTRIBUTION status: $CF_STATUS"
echo "  Frontend bucket $LC_FRONTEND_BUCKET: $FB_EXISTS"
if [ "$VALID" != "true" ]; then
  exit 1
fi
