#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=config/production.env
source "$SCRIPT_DIR/config/production.env"
# shellcheck source=lib/lifecycle.sh
source "$SCRIPT_DIR/lib/lifecycle.sh"

CAPACITY_PROVIDER="FARGATE_SPOT"
case "${1:-}" in
  "") ;;
  --on-demand) CAPACITY_PROVIDER="FARGATE" ;;
  --help) echo "Usage: $0 [--on-demand]"; exit 0 ;;
  *) echo "Usage: $0 [--on-demand]" >&2; exit 1 ;;
esac

# Production keeps its RDS instance and target group while paused. Resume only
# recreates the hourly ALB resources, reconnects the gateway, and starts ECS.
RUN_STARTED_AT=$SECONDS
lc_log_step "1/6" "10–20 seconds" "Validate AWS identity and all production resource boundaries."
lc_init
lc_require_environment production
lc_verify_identity
lc_validate_static_resources

lc_log_step "2/6" "20–60 seconds" "Create or reuse the public ALB/listener and enforce the 30s drain delay."
ALB_ARN=$(lc_ensure_alb)

# The gateway may lose its target-group attachment when the disposable ALB is
# recreated. Reapply and verify that wiring before launching application tasks.
lc_log_step "3/6" "under 15 seconds" "Verify the API gateway target-group attachment."
lc_wire_gateway

lc_log_step "4/6" "10–20 seconds" "Set all ECS services to desired=1 on $CAPACITY_PROVIDER."
lc_scale_services 1 "$CAPACITY_PROVIDER"

# This is normally the longest production step: ECS pulls images, starts the
# JVMs, runs container health checks, and completes the rolling deployments.
lc_log_step "5/6" "2–6 minutes" "Wait for Auth, Items, and API Gateway to become stable."
lc_wait_services_stable
ALB_DNS=$(lc_alb_dns "$ALB_ARN")

# The ALB is disposable and its DNS changes on every resume. Re-point the
# CloudFront alb-api origin so the live frontend's /auth* and /items* paths
# keep reaching the new ALB (no-op when it already matches; skipped for staging).
lc_repoint_cloudfront_alb_origin "$ALB_DNS"

lc_log_step "6/6" "5–60 seconds" "Probe the public route until the gateway returns the expected authentication response."
lc_wait_http_unauthorized "$ALB_DNS" production

lc_log_complete "Production resume" "$RUN_STARTED_AT"
lc_log "Production is running: http://$ALB_DNS"
lc_log "To pause: bash scripts/pause-playground.sh"
