#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=config/production.env
source "$SCRIPT_DIR/config/production.env"
# shellcheck source=lib/lifecycle.sh
source "$SCRIPT_DIR/lib/lifecycle.sh"

[ "$#" = "0" ] || { echo "Usage: $0" >&2; exit 1; }

# Pausing production preserves the database, ECR images, target group, and ECS
# definitions. Only running tasks and hourly ALB/listener resources are removed.
RUN_STARTED_AT=$SECONDS
lc_log_step "1/4" "10–20 seconds" "Validate AWS identity and production resource boundaries."
lc_init
lc_require_environment production
lc_verify_identity
lc_validate_static_resources

lc_log_step "2/4" "10–20 seconds" "Set all production ECS services to desired=0."
lc_scale_services 0

# Wait for both running and pending counts to reach zero before deleting the
# entry point, so the final state is deterministic and explicitly verified.
lc_log_step "3/4" "15–60 seconds" "Wait for all ECS tasks and target registrations to drain."
lc_wait_services_stopped

lc_log_step "4/4" "10–30 seconds" "Delete the ALB/listener and verify that the ALB is absent."
lc_delete_alb

lc_log_complete "Production pause" "$RUN_STARTED_AT"
lc_log "Production pause state: ECS=0, ALB deleted; target group and RDS retained."
lc_log "To resume: bash scripts/resume-playground.sh"
