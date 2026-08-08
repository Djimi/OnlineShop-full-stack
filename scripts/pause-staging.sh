#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=config/staging.env
source "$SCRIPT_DIR/config/staging.env"
# shellcheck source=lib/lifecycle.sh
source "$SCRIPT_DIR/lib/lifecycle.sh"

SNAPSHOT_NAME=""
case "${1:-}" in
  "") ;;
  --retain-snapshot)
    SNAPSHOT_NAME="${2:-}"
    [ -n "$SNAPSHOT_NAME" ] || { echo "--retain-snapshot requires a name" >&2; exit 1; }
    [ "$#" = "2" ] || { echo "Usage: $0 [--retain-snapshot <name>]" >&2; exit 1; }
    ;;
  --help) echo "Usage: $0 [--retain-snapshot onlineshop-staging-debug-<reason>]"; exit 0 ;;
  *) echo "Usage: $0 [--retain-snapshot onlineshop-staging-debug-<reason>]" >&2; exit 1 ;;
esac

# Staging teardown is destructive by design: services, the ALB, and the clean
# ephemeral RDS instance are removed. A snapshot exists only when the caller
# explicitly supplies a diagnostic/DR name through --retain-snapshot.
RUN_STARTED_AT=$SECONDS
lc_log_step "1/5" "10–20 seconds" "Validate AWS identity and isolated staging resource boundaries."
lc_init
lc_require_environment staging
lc_verify_identity
lc_validate_static_resources

lc_log_step "2/5" "10–20 seconds" "Set all staging ECS services to desired=0."
lc_scale_services 0
lc_log_step "3/5" "15–60 seconds" "Wait for all staging tasks and target registrations to drain."
lc_wait_services_stopped
lc_log_step "4/5" "10–30 seconds" "Delete the staging ALB/listener and verify absence."
lc_delete_alb

if [ -n "$SNAPSHOT_NAME" ]; then
  DB_ESTIMATE="10–20 minutes"
  DB_DESCRIPTION="Delete staging RDS and verify retained diagnostic/DR snapshot $SNAPSHOT_NAME."
else
  DB_ESTIMATE="5–10 minutes"
  DB_DESCRIPTION="Delete staging RDS without a final snapshot and verify absence."
fi
lc_log_step "5/5" "$DB_ESTIMATE" "$DB_DESCRIPTION"
lc_delete_staging_db "$SNAPSHOT_NAME"

lc_log_complete "Staging pause" "$RUN_STARTED_AT"
if [ -n "$SNAPSHOT_NAME" ]; then
  lc_log "Staging pause state: ECS=0, ALB/RDS deleted; snapshot retained: $SNAPSHOT_NAME"
else
  lc_log "Staging pause state: ECS=0, ALB deleted, RDS deleted without a snapshot."
fi
