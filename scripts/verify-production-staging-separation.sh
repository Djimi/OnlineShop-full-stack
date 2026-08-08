#!/usr/bin/env bash
set -euo pipefail

# Read-only production/staging separation check (Pass 3, subphase 3.5).
# Proves the two environments share no VPC, ECS cluster, RDS instance,
# security groups, Cloud Map namespace, Secrets Manager entries, services,
# or target group — first against the explicit non-secret configs
# (scripts/config/{production,staging}.env) and then against live observed
# state (describe/get/list only; nothing is ever mutated).
#
# Identity preflight is mandatory. The staging Cloud Map namespace and the
# per-service Service Connect namespaces are read live so namespace isolation
# is proven against reality, not only the config.
#
# Usage:
#   bash scripts/verify-production-staging-separation.sh [--json]
#
# Exit 0 when production and staging are isolated; non-zero (fail closed) when
# any shared environment-scoped resource is detected.

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

PROD_CONFIG="$SCRIPT_DIR/config/production.env"
STAGING_CONFIG="$SCRIPT_DIR/config/staging.env"

PROD_EXPECTED=$(identifiers_from_config "$PROD_CONFIG")
STAGING_EXPECTED=$(identifiers_from_config "$STAGING_CONFIG")
PROD_OBSERVED=$(identifiers_observed "$PROD_CONFIG")
STAGING_OBSERVED=$(identifiers_observed "$STAGING_CONFIG")
PROD_TOPOLOGY=$(topology_observed "$PROD_CONFIG")
STAGING_TOPOLOGY=$(topology_observed "$STAGING_CONFIG")

run_separation() {
  PYTHONPATH="$RELEASE_ROOT/src" python3 -m release_contract.environments separation \
    --prod "$1" --staging "$2" || true
}

STATIC_CHECK=$(run_separation <(printf '%s' "$PROD_EXPECTED") <(printf '%s' "$STAGING_EXPECTED"))
LIVE_CHECK=$(run_separation <(printf '%s' "$PROD_OBSERVED") <(printf '%s' "$STAGING_OBSERVED"))
TOPOLOGY_CHECK=$(PYTHONPATH="$RELEASE_ROOT/src" python3 -m release_contract.environments topology \
  --prod <(printf '%s' "$PROD_TOPOLOGY") --staging <(printf '%s' "$STAGING_TOPOLOGY") || true)

for raw in "$STATIC_CHECK" "$LIVE_CHECK" "$TOPOLOGY_CHECK"; do
  printf '%s' "$raw" | jq -e 'type == "object"' >/dev/null 2>&1 || {
    echo "ERROR: separation/topology decision layer produced no valid result (see stderr)" >&2
    exit 1
  }
done

STATIC_VALID=$(printf '%s' "$STATIC_CHECK" | jq -r '.valid')
LIVE_VALID=$(printf '%s' "$LIVE_CHECK" | jq -r '.valid')
TOPOLOGY_VALID=$(printf '%s' "$TOPOLOGY_CHECK" | jq -r '.valid')

if [ "$JSON_ONLY" = "1" ]; then
  jq -n \
    --argjson staticCheck "$STATIC_CHECK" --argjson liveCheck "$LIVE_CHECK" \
    --argjson topologyCheck "$TOPOLOGY_CHECK" \
    '{staticValid: ($staticCheck.valid == true), staticIssues: $staticCheck.issues,
      liveValid: ($liveCheck.valid == true), liveIssues: $liveCheck.issues,
      topologyValid: ($topologyCheck.valid == true), topologyIssues: $topologyCheck.issues,
      productionObserved: '"$PROD_OBSERVED"', stagingObserved: '"$STAGING_OBSERVED"',
      productionTopology: '"$PROD_TOPOLOGY"', stagingTopology: '"$STAGING_TOPOLOGY"'}'
  exit "$([ "$STATIC_VALID" = "true" ] && [ "$LIVE_VALID" = "true" ] && [ "$TOPOLOGY_VALID" = "true" ] && echo 0 || echo 1)"
fi

echo "=== Production/staging separation (read-only) ==="
printf '%s' "$STATIC_CHECK" | jq -r '
  if .valid then "STATIC config check: OK — no shared environment-scoped identifiers in the two configs."
  else "STATIC config check: FAIL",
    (.issues[] | "  [\(.code)] \(.field): \(.message)")
  end'
printf '%s' "$LIVE_CHECK" | jq -r '
  if .valid then "LIVE identity check: OK — no shared live resource identifier between the environments."
  else "LIVE identity check: FAIL",
    (.issues[] | "  [\(.code)] \(.field): \(.message)")
  end'
printf '%s' "$TOPOLOGY_CHECK" | jq -r '
  if .valid then "LIVE topology check: OK — no shared VPC or Cloud Map namespace."
  else "LIVE topology check: FAIL",
    (.issues[] | "  [\(.code)] \(.field): \(.message)")
  end'

if [ "$STATIC_VALID" != "true" ] || [ "$LIVE_VALID" != "true" ] || [ "$TOPOLOGY_VALID" != "true" ]; then
  exit 1
fi
