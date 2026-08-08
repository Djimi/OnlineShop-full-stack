#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=config/staging.env
source "$SCRIPT_DIR/config/staging.env"
# shellcheck source=lib/lifecycle.sh
source "$SCRIPT_DIR/lib/lifecycle.sh"

CAPACITY_PROVIDER="FARGATE_SPOT"
START_SERVICES=true
while [ "$#" -gt 0 ]; do
  case "$1" in
    --on-demand) CAPACITY_PROVIDER="FARGATE" ;;
    --defer-services) START_SERVICES=false ;;
    --help)
      echo "Usage: $0 [--on-demand] [--defer-services]"
      echo "  --defer-services leaves ECS at desired=0 for a subsequent candidate deployment."
      exit 0
      ;;
    *) echo "Usage: $0 [--on-demand] [--defer-services]" >&2; exit 1 ;;
  esac
  shift
done

# Staging is intentionally rebuilt from an empty database on every resume. The
# error trap below captures evidence first, then returns the environment to its
# cost-saving paused state if any provisioning, bootstrap, or startup step fails.
RUN_STARTED_AT=$SECONDS
lc_log_step "1/8" "10–20 seconds" "Validate AWS identity and isolated staging resource boundaries."
lc_init
lc_require_environment staging
lc_verify_identity
lc_validate_static_resources

START_COMPLETE=false
failure_cleanup() {
  local exit_code=$?
  trap - ERR
  [ "$START_COMPLETE" = true ] && return "$exit_code"
  lc_log "FAILED — staging resume stopped after $((SECONDS - RUN_STARTED_AT))s; starting diagnostic teardown."
  lc_log_step "cleanup 1/4" "10–30 seconds" "Capture ECS, stopped-task, target-health, and RDS diagnostics."
  lc_capture_diagnostics >&2 || true
  lc_log_step "cleanup 2/4" "15–60 seconds" "Scale services to zero and wait for tasks to stop."
  lc_scale_services 0 || true
  lc_wait_services_stopped || true
  lc_log_step "cleanup 3/4" "10–30 seconds" "Delete any staging ALB/listener resources."
  lc_delete_alb || true
  lc_log_step "cleanup 4/4" "5–10 minutes" "Delete the ephemeral staging database without retaining failed state."
  lc_delete_staging_db || true
  lc_log "Failure cleanup finished; original exit code=$exit_code."
  return "$exit_code"
}
trap failure_cleanup ERR

# RDS creation is the longest infrastructure operation and uses an AWS-managed
# master secret. Application services remain stopped until bootstrap succeeds.
lc_log_step "2/8" "5–10 minutes" "Create a new encrypted private PostgreSQL instance and wait for availability."
lc_create_clean_staging_db >/dev/null

# Bootstrap runs SQL from one-off private Fargate tasks. Every mutation has a
# read-back, restricted application users are tested, and helper task revisions
# are deleted after use. No database password is printed or stored in a task TD.
lc_log_step "3/8" "2–5 minutes" "Create application databases/roles, apply schemas and seeds, and verify access."
"$SCRIPT_DIR/bootstrap-staging-db.sh"

lc_log_step "4/8" "20–60 seconds" "Create the staging ALB/listener and enforce the 30s drain delay."
ALB_ARN=$(lc_ensure_alb)
lc_log_step "5/8" "under 15 seconds" "Verify the staging API gateway target-group attachment."
lc_wire_gateway
if [ "$START_SERVICES" = true ]; then
  lc_log_step "6/8" "10–20 seconds" "Set all staging ECS services to desired=1 on $CAPACITY_PROVIDER."
  lc_scale_services 1 "$CAPACITY_PROVIDER"
  lc_log_step "7/8" "3–8 minutes" "Wait for all JVM services and Service Connect deployments to stabilize."
  lc_wait_services_stable
  ALB_DNS=$(lc_alb_dns "$ALB_ARN")
  lc_log_step "8/8" "5–60 seconds" "Probe the public route for the expected authentication response."
  lc_wait_http_unauthorized "$ALB_DNS" staging
else
  # CI must select the candidate task definitions before ECS starts pulling an
  # image. Starting the paused services here would briefly use whatever image
  # tag was left by the previous run and can fail before ci-deploy-staging.sh
  # has a chance to replace it.
  lc_log_step "6/6" "under 15 seconds" "Leave staging ECS services stopped for the candidate deployment."
  lc_log "Staging infrastructure and the clean database are ready; application services remain at desired=0."
fi

START_COMPLETE=true
trap - ERR
if [ "$START_SERVICES" = true ]; then
  lc_log_complete "Clean staging resume" "$RUN_STARTED_AT"
  lc_log "Staging is running against a clean database: http://$ALB_DNS"
else
  lc_log_complete "Clean staging infrastructure resume" "$RUN_STARTED_AT"
  lc_log "Staging is ready for ci-deploy-staging.sh; ECS application services are intentionally stopped."
fi
