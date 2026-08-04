#!/usr/bin/env bash
set -euo pipefail

PROFILE="dpm-profile"
REGION="eu-north-1"
CLUSTER="onlineshop-staging-cluster"
DB_INSTANCE="onlineshop-staging-postgres"
DB_SNAPSHOT="onlineshop-staging-latest"
ALB_NAME="onlineshop-staging-v2-alb"
SERVICES=(onlineshop-auth-staging onlineshop-items-staging onlineshop-api-gateway-staging)
AWS=(aws --profile "$PROFILE" --region "$REGION")

"${AWS[@]}" sts get-caller-identity >/dev/null

echo "Scaling isolated staging services to zero..."
for service in "${SERVICES[@]}"; do
  "${AWS[@]}" ecs update-service --cluster "$CLUSTER" --service "$service" \
    --desired-count 0 >/dev/null
  desired=$("${AWS[@]}" ecs describe-services --cluster "$CLUSTER" \
    --services "$service" --query 'services[0].desiredCount' --output text)
  [ "$desired" = "0" ] || { echo "ERROR: $service did not scale to zero" >&2; exit 1; }
done
for attempt in {1..20}; do
  active=$("${AWS[@]}" ecs describe-services --cluster "$CLUSTER" \
    --services "${SERVICES[@]}" \
    --query 'services[].[runningCount,pendingCount]' --output text | \
    awk '{ total += $1 + $2 } END { print total + 0 }')
  [ "$active" = "0" ] && break
  [ "$attempt" = "20" ] && { echo "ERROR: staging tasks did not stop" >&2; exit 1; }
  sleep 5
done
for service in "${SERVICES[@]}"; do
  counts=$("${AWS[@]}" ecs describe-services --cluster "$CLUSTER" --services "$service" \
    --query 'services[0].[desiredCount,runningCount,pendingCount]' --output text)
  [ "$counts" = $'0\t0\t0' ] || { echo "ERROR: $service still has tasks: $counts" >&2; exit 1; }
done

alb_arn=$("${AWS[@]}" elbv2 describe-load-balancers --names "$ALB_NAME" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || true)
if [ -n "$alb_arn" ] && [ "$alb_arn" != "None" ]; then
  listeners=$("${AWS[@]}" elbv2 describe-listeners --load-balancer-arn "$alb_arn" \
    --query 'Listeners[].ListenerArn' --output text)
  for listener in $listeners; do
    "${AWS[@]}" elbv2 delete-listener --listener-arn "$listener"
    if "${AWS[@]}" elbv2 describe-listeners --listener-arns "$listener" >/dev/null 2>&1; then
      echo "ERROR: listener still exists: $listener" >&2; exit 1
    fi
  done
  "${AWS[@]}" elbv2 delete-load-balancer --load-balancer-arn "$alb_arn"
  if "${AWS[@]}" elbv2 describe-load-balancers --load-balancer-arns "$alb_arn" >/dev/null 2>&1; then
    echo "ERROR: staging ALB still exists" >&2; exit 1
  fi
fi

db_status=$("${AWS[@]}" rds describe-db-instances --db-instance-identifier "$DB_INSTANCE" \
  --query 'DBInstances[0].DBInstanceStatus' --output text 2>/dev/null || true)
if [ -n "$db_status" ] && [ "$db_status" != "None" ]; then
  if [ "$db_status" != "deleting" ]; then
    old_snapshot_status=$("${AWS[@]}" rds describe-db-snapshots --db-snapshot-identifier "$DB_SNAPSHOT" \
      --query 'DBSnapshots[0].Status' --output text 2>/dev/null || true)
    if [ -n "$old_snapshot_status" ] && [ "$old_snapshot_status" != "None" ]; then
      [ "$old_snapshot_status" = "available" ] || [ "$old_snapshot_status" = "failed" ] || {
        echo "ERROR: previous snapshot is busy: $old_snapshot_status" >&2; exit 1;
      }
      "${AWS[@]}" rds delete-db-snapshot --db-snapshot-identifier "$DB_SNAPSHOT" >/dev/null
      if "${AWS[@]}" rds describe-db-snapshots --db-snapshot-identifier "$DB_SNAPSHOT" >/dev/null 2>&1; then
        echo "ERROR: previous staging snapshot still exists" >&2; exit 1
      fi
    fi

    "${AWS[@]}" rds modify-db-instance --db-instance-identifier "$DB_INSTANCE" \
      --no-deletion-protection --apply-immediately >/dev/null
    protection=$("${AWS[@]}" rds describe-db-instances --db-instance-identifier "$DB_INSTANCE" \
      --query 'DBInstances[0].DeletionProtection' --output text)
    [ "$protection" = "False" ] || [ "$protection" = "false" ] || {
      echo "ERROR: deletion protection is still enabled" >&2; exit 1;
    }

    "${AWS[@]}" rds delete-db-instance --db-instance-identifier "$DB_INSTANCE" \
      --final-db-snapshot-identifier "$DB_SNAPSHOT" >/dev/null
    status=$("${AWS[@]}" rds describe-db-instances --db-instance-identifier "$DB_INSTANCE" \
      --query 'DBInstances[0].DBInstanceStatus' --output text)
    [ "$status" = "deleting" ] || { echo "ERROR: staging DB deletion did not start" >&2; exit 1; }
  fi
  "${AWS[@]}" rds wait db-instance-deleted --db-instance-identifier "$DB_INSTANCE"
  "${AWS[@]}" rds wait db-snapshot-completed --db-snapshot-identifier "$DB_SNAPSHOT"
  "${AWS[@]}" rds describe-db-snapshots --db-snapshot-identifier "$DB_SNAPSHOT" \
    --query 'DBSnapshots[0].{Status:Status,Encrypted:Encrypted,Size:AllocatedStorage}'
fi

echo "Staging is paused: ECS=0, ALB deleted, RDS retained only as an encrypted snapshot."
