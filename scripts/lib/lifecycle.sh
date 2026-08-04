#!/usr/bin/env bash

# Shared, sourceable AWS lifecycle helpers. Entry points must enable strict mode.

# Lifecycle functions that also return a value write operational logs to stderr.
# This keeps stdout safe for command substitution (for example, capturing an ALB
# ARN) while still showing progress in terminals and GitHub Actions logs.
lc_log() {
  printf '[%s] [%s] %s\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "${LC_ENVIRONMENT:-lifecycle}" "$*" >&2
}

lc_log_step() {
  local step="$1" typical="$2"
  shift 2
  lc_log "STEP $step (typical: $typical) — $*"
}

lc_log_complete() {
  local operation="$1" started_at="$2" elapsed
  elapsed=$((SECONDS - started_at))
  lc_log "COMPLETE — $operation finished in $((elapsed / 60))m $((elapsed % 60))s."
}

lc_die() {
  echo "ERROR: $*" >&2
  return 1
}

lc_is_present() {
  [ -n "${1:-}" ] && [ "$1" != "None" ]
}

lc_require_environment() {
  local expected="$1"
  [ "${LC_ENVIRONMENT:-}" = "$expected" ] ||
    lc_die "refusing $expected operation with LC_ENVIRONMENT=${LC_ENVIRONMENT:-unset}"
}

lc_init() {
  local required=(
    LC_ENVIRONMENT LC_ACCOUNT_ID LC_PROFILE LC_REGION LC_VPC_ID LC_CLUSTER
    LC_DB_INSTANCE LC_DB_SUBNET_GROUP LC_DB_SECURITY_GROUP LC_ALB_NAME
    LC_ALB_SECURITY_GROUP LC_ECS_SECURITY_GROUP LC_TARGET_GROUP_ARN LC_GATEWAY_SERVICE
    LC_GATEWAY_CONTAINER LC_GATEWAY_PORT
  )
  local name
  for name in "${required[@]}"; do
    [ -n "${!name:-}" ] || lc_die "missing lifecycle configuration: $name"
  done
  [ "${#LC_ALB_SUBNETS[@]}" -gt 1 ] || lc_die "at least two ALB subnets are required"
  [ "${#LC_SERVICES[@]}" -gt 0 ] || lc_die "at least one ECS service is required"
  command -v aws >/dev/null || lc_die "aws CLI is required"
  LC_AWS=(aws --profile "$LC_PROFILE" --region "$LC_REGION")
}

lc_verify_identity() {
  local actual
  actual=$("${LC_AWS[@]}" sts get-caller-identity --query Account --output text)
  [ "$actual" = "$LC_ACCOUNT_ID" ] ||
    lc_die "AWS account mismatch: expected $LC_ACCOUNT_ID, got $actual"
  lc_log "AWS identity verified for account $actual in $LC_REGION."
}

lc_validate_static_resources() {
  local actual_vpc subnet_vpc sg_vpc cluster_status db_vpc tg_vpc service

  actual_vpc=$("${LC_AWS[@]}" ec2 describe-vpcs --vpc-ids "$LC_VPC_ID" \
    --query 'Vpcs[0].VpcId' --output text)
  [ "$actual_vpc" = "$LC_VPC_ID" ] || lc_die "VPC mismatch: $actual_vpc"

  for subnet in "${LC_ALB_SUBNETS[@]}"; do
    subnet_vpc=$("${LC_AWS[@]}" ec2 describe-subnets --subnet-ids "$subnet" \
      --query 'Subnets[0].VpcId' --output text)
    [ "$subnet_vpc" = "$LC_VPC_ID" ] || lc_die "subnet $subnet is outside $LC_VPC_ID"
  done

  for sg in "$LC_ALB_SECURITY_GROUP" "$LC_ECS_SECURITY_GROUP" "$LC_DB_SECURITY_GROUP"; do
    sg_vpc=$("${LC_AWS[@]}" ec2 describe-security-groups --group-ids "$sg" \
      --query 'SecurityGroups[0].VpcId' --output text)
    [ "$sg_vpc" = "$LC_VPC_ID" ] || lc_die "security group $sg is outside $LC_VPC_ID"
  done

  cluster_status=$("${LC_AWS[@]}" ecs describe-clusters --clusters "$LC_CLUSTER" \
    --query 'clusters[0].status' --output text)
  [ "$cluster_status" = "ACTIVE" ] || lc_die "cluster $LC_CLUSTER is not ACTIVE"

  db_vpc=$("${LC_AWS[@]}" rds describe-db-subnet-groups \
    --db-subnet-group-name "$LC_DB_SUBNET_GROUP" \
    --query 'DBSubnetGroups[0].VpcId' --output text)
  [ "$db_vpc" = "$LC_VPC_ID" ] ||
    lc_die "DB subnet group $LC_DB_SUBNET_GROUP is outside $LC_VPC_ID"

  tg_vpc=$("${LC_AWS[@]}" elbv2 describe-target-groups \
    --target-group-arns "$LC_TARGET_GROUP_ARN" \
    --query 'TargetGroups[0].VpcId' --output text)
  [ "$tg_vpc" = "$LC_VPC_ID" ] || lc_die "target group is outside $LC_VPC_ID"

  for service in "${LC_SERVICES[@]}"; do
    local status
    status=$("${LC_AWS[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
      --services "$service" --query 'services[0].status' --output text)
    [ "$status" = "ACTIVE" ] || lc_die "service $service is not ACTIVE"
  done

  if [ "$LC_ENVIRONMENT" = "production" ]; then
    db_vpc=$("${LC_AWS[@]}" rds describe-db-instances \
      --db-instance-identifier "$LC_DB_INSTANCE" \
      --query 'DBInstances[0].DBSubnetGroup.VpcId' --output text)
    [ "$db_vpc" = "$LC_VPC_ID" ] || lc_die "production database is outside $LC_VPC_ID"
  fi
  lc_log "Static resource validation passed for VPC, subnets, security groups, ECS, RDS, and target group."
}

lc_find_alb() {
  "${LC_AWS[@]}" elbv2 describe-load-balancers --names "$LC_ALB_NAME" \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || true
}

lc_ensure_alb() {
  local alb_arn listener_arn
  alb_arn=$(lc_find_alb)
  if ! lc_is_present "$alb_arn"; then
    lc_log "Creating ALB $LC_ALB_NAME; AWS usually provisions it in 20–60 seconds."
    alb_arn=$("${LC_AWS[@]}" elbv2 create-load-balancer \
      --name "$LC_ALB_NAME" --subnets "${LC_ALB_SUBNETS[@]}" \
      --security-groups "$LC_ALB_SECURITY_GROUP" --scheme internet-facing \
      --type application --ip-address-type ipv4 \
      --tags "Key=Environment,Value=$LC_ENVIRONMENT" \
      --query 'LoadBalancers[0].LoadBalancerArn' --output text)
    "${LC_AWS[@]}" elbv2 describe-load-balancers --load-balancer-arns "$alb_arn" \
      --query 'LoadBalancers[0].{Arn:LoadBalancerArn,Vpc:VpcId,State:State.Code}' >&2
  else
    lc_log "Reusing existing ALB $LC_ALB_NAME."
  fi

  listener_arn=$("${LC_AWS[@]}" elbv2 describe-listeners \
    --load-balancer-arn "$alb_arn" --query 'Listeners[0].ListenerArn' \
    --output text 2>/dev/null || true)
  if ! lc_is_present "$listener_arn"; then
    lc_log "Creating HTTP listener for $LC_ALB_NAME."
    listener_arn=$("${LC_AWS[@]}" elbv2 create-listener \
      --load-balancer-arn "$alb_arn" --protocol HTTP --port 80 \
      --default-actions "Type=forward,TargetGroupArn=$LC_TARGET_GROUP_ARN" \
      --query 'Listeners[0].ListenerArn' --output text)
    "${LC_AWS[@]}" elbv2 describe-listeners --listener-arns "$listener_arn" \
      --query 'Listeners[0].{Arn:ListenerArn,Port:Port,Actions:DefaultActions}' >&2
  else
    lc_log "Existing ALB listener is already present."
  fi

  lc_ensure_target_group_attributes

  printf '%s\n' "$alb_arn"
}

lc_ensure_target_group_attributes() {
  local delay
  delay=$("${LC_AWS[@]}" elbv2 describe-target-group-attributes \
    --target-group-arn "$LC_TARGET_GROUP_ARN" \
    --query 'Attributes[?Key==`deregistration_delay.timeout_seconds`].Value|[0]' \
    --output text)
  if [ "$delay" != "30" ]; then
    lc_log "Changing target deregistration delay from ${delay}s to 30s."
    "${LC_AWS[@]}" elbv2 modify-target-group-attributes \
      --target-group-arn "$LC_TARGET_GROUP_ARN" \
      --attributes Key=deregistration_delay.timeout_seconds,Value=30 >/dev/null
    delay=$("${LC_AWS[@]}" elbv2 describe-target-group-attributes \
      --target-group-arn "$LC_TARGET_GROUP_ARN" \
      --query 'Attributes[?Key==`deregistration_delay.timeout_seconds`].Value|[0]' \
      --output text)
    [ "$delay" = "30" ] || lc_die "target group deregistration delay update was not applied"
  else
    lc_log "Target group deregistration delay is already 30s."
  fi
}

lc_wire_gateway() {
  local configured
  configured=$("${LC_AWS[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
    --services "$LC_GATEWAY_SERVICE" \
    --query 'services[0].loadBalancers[0].targetGroupArn' --output text)
  if [ "$configured" != "$LC_TARGET_GROUP_ARN" ]; then
    lc_log "Attaching gateway service $LC_GATEWAY_SERVICE to the lifecycle target group."
    "${LC_AWS[@]}" ecs update-service --cluster "$LC_CLUSTER" \
      --service "$LC_GATEWAY_SERVICE" \
      --load-balancers "targetGroupArn=$LC_TARGET_GROUP_ARN,containerName=$LC_GATEWAY_CONTAINER,containerPort=$LC_GATEWAY_PORT" \
      --force-new-deployment >/dev/null
    configured=$("${LC_AWS[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
      --services "$LC_GATEWAY_SERVICE" \
      --query 'services[0].loadBalancers[0].targetGroupArn' --output text)
    [ "$configured" = "$LC_TARGET_GROUP_ARN" ] || lc_die "gateway target group update was not applied"
  else
    lc_log "Gateway service is already attached to the expected target group."
  fi
}

lc_scale_services() {
  local desired="$1" capacity_provider="${2:-}" service actual current_capacity
  for service in "${LC_SERVICES[@]}"; do
    actual=$("${LC_AWS[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
      --services "$service" --query 'services[0].desiredCount' --output text)
    current_capacity=$("${LC_AWS[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
      --services "$service" \
      --query 'services[0].capacityProviderStrategy[0].capacityProvider' --output text)
    if [ "$actual" = "$desired" ] && { [ -z "$capacity_provider" ] || [ "$current_capacity" = "$capacity_provider" ]; }; then
      lc_log "$service already has desired=$desired${capacity_provider:+ on $capacity_provider}; no update needed."
      continue
    fi
    local args=(--cluster "$LC_CLUSTER" --service "$service" --desired-count "$desired")
    if [ -n "$capacity_provider" ]; then
      args+=(--capacity-provider-strategy "capacityProvider=$capacity_provider,weight=1,base=1")
      args+=(--force-new-deployment)
    fi
    lc_log "Updating $service from desired=$actual to desired=$desired${capacity_provider:+ on $capacity_provider}."
    "${LC_AWS[@]}" ecs update-service "${args[@]}" >/dev/null
    actual=$("${LC_AWS[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
      --services "$service" --query 'services[0].desiredCount' --output text)
    [ "$actual" = "$desired" ] || lc_die "$service desired count is $actual, expected $desired"
    lc_log "Verified $service desired count is $desired."
  done
}

lc_wait_services_stable() {
  lc_log "Waiting for ECS deployments to stabilize; Spring Boot tasks typically need 2–6 minutes."
  "${LC_AWS[@]}" ecs wait services-stable --cluster "$LC_CLUSTER" \
    --services "${LC_SERVICES[@]}"
  "${LC_AWS[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
    --services "${LC_SERVICES[@]}" \
    --query 'services[].{Name:serviceName,Desired:desiredCount,Running:runningCount,Pending:pendingCount,Rollout:deployments[0].rolloutState}'
  lc_log "All ECS services are stable."
}

lc_wait_services_stopped() {
  local active attempt counts service
  lc_log "Waiting for ECS tasks and target registrations to drain; typically 15–60 seconds."
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    active=$("${LC_AWS[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
      --services "${LC_SERVICES[@]}" \
      --query 'services[].[runningCount,pendingCount]' --output text | \
      awk '{ total += $1 + $2 } END { print total + 0 }')
    [ "$active" = "0" ] && break
    [ "$attempt" = "12" ] && lc_die "ECS tasks did not stop within 60 seconds"
    sleep 5
  done
  for service in "${LC_SERVICES[@]}"; do
    counts=$("${LC_AWS[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
      --services "$service" --query 'services[0].[desiredCount,runningCount,pendingCount]' \
      --output text)
    [ "$counts" = $'0\t0\t0' ] || lc_die "$service still has tasks: $counts"
  done
  lc_log "All ECS services are verified at desired=0, running=0, pending=0."
}

lc_delete_alb() {
  local alb_arn listener
  alb_arn=$(lc_find_alb)
  if ! lc_is_present "$alb_arn"; then
    lc_log "ALB $LC_ALB_NAME is already absent; no deletion needed."
    return 0
  fi
  lc_log "Deleting listeners and ALB $LC_ALB_NAME; AWS usually accepts this in 10–30 seconds."
  while read -r listener; do
    lc_is_present "$listener" || continue
    lc_log "Deleting listener $listener."
    "${LC_AWS[@]}" elbv2 delete-listener --listener-arn "$listener"
    if "${LC_AWS[@]}" elbv2 describe-listeners --listener-arns "$listener" >/dev/null 2>&1; then
      lc_die "listener still exists after deletion: $listener"
    fi
  done < <("${LC_AWS[@]}" elbv2 describe-listeners --load-balancer-arn "$alb_arn" \
    --query 'Listeners[].ListenerArn' --output text | tr '\t' '\n')
  "${LC_AWS[@]}" elbv2 delete-load-balancer --load-balancer-arn "$alb_arn"
  if "${LC_AWS[@]}" elbv2 describe-load-balancers --load-balancer-arns "$alb_arn" >/dev/null 2>&1; then
    lc_die "load balancer still exists after deletion: $alb_arn"
  fi
  lc_log "Verified ALB $LC_ALB_NAME is absent."
}

lc_alb_dns() {
  local alb_arn="$1"
  "${LC_AWS[@]}" elbv2 describe-load-balancers --load-balancer-arns "$alb_arn" \
    --query 'LoadBalancers[0].DNSName' --output text
}

lc_wait_http_unauthorized() {
  local dns="$1" label="$2" status attempt
  lc_log "Checking API readiness at $dns; a 401 for an invalid token proves the request reached the application."
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
      --header "Authorization: Bearer ${label}-readiness-invalid-token" \
      "http://$dns/items" || true)
    if [ "$status" = "401" ]; then
      lc_log "API readiness passed on attempt $attempt with HTTP 401."
      return 0
    fi
    if [ "$attempt" = "1" ] || [ $((attempt % 3)) = "0" ]; then
      lc_log "API not ready yet (attempt $attempt/12, HTTP ${status:-none}); retrying in 5s."
    fi
    sleep 5
  done
  lc_die "$label API did not become ready within 60 seconds (last HTTP $status)"
}

lc_staging_db_status() {
  lc_require_environment staging
  "${LC_AWS[@]}" rds describe-db-instances --db-instance-identifier "$LC_DB_INSTANCE" \
    --query 'DBInstances[0].DBInstanceStatus' --output text 2>/dev/null || true
}

lc_create_clean_staging_db() {
  lc_require_environment staging
  local status endpoint public vpc encrypted
  status=$(lc_staging_db_status)
  if lc_is_present "$status"; then
    lc_die "staging database $LC_DB_INSTANCE already exists ($status); run pause-staging.sh before a clean start"
  fi

  lc_log "Submitting clean encrypted RDS creation for $LC_DB_INSTANCE; availability typically takes 5–10 minutes."
  "${LC_AWS[@]}" rds create-db-instance \
    --db-instance-identifier "$LC_DB_INSTANCE" \
    --db-instance-class "$LC_DB_INSTANCE_CLASS" \
    --engine "$LC_DB_ENGINE" --engine-version "$LC_DB_ENGINE_VERSION" \
    --allocated-storage "$LC_DB_ALLOCATED_STORAGE" --storage-type "$LC_DB_STORAGE_TYPE" \
    --storage-encrypted --master-username dbadmin --manage-master-user-password \
    --db-subnet-group-name "$LC_DB_SUBNET_GROUP" \
    --vpc-security-group-ids "$LC_DB_SECURITY_GROUP" \
    --backup-retention-period 0 --no-publicly-accessible --no-multi-az \
    --no-auto-minor-version-upgrade --no-deletion-protection \
    --tags Key=Environment,Value=staging Key=Lifecycle,Value=ephemeral \
      Key=Name,Value=onlineshop-staging-postgres >/dev/null
  status=$(lc_staging_db_status)
  lc_is_present "$status" || lc_die "RDS create did not produce $LC_DB_INSTANCE"
  lc_log "RDS creation accepted with status=$status; waiting for available."

  "${LC_AWS[@]}" rds wait db-instance-available \
    --db-instance-identifier "$LC_DB_INSTANCE"
  read -r status endpoint public vpc encrypted < <("${LC_AWS[@]}" rds describe-db-instances \
    --db-instance-identifier "$LC_DB_INSTANCE" \
    --query 'DBInstances[0].[DBInstanceStatus,Endpoint.Address,PubliclyAccessible,DBSubnetGroup.VpcId,StorageEncrypted]' \
    --output text)
  [ "$status" = "available" ] || lc_die "staging database is not available: $status"
  [ "$public" = "False" ] || [ "$public" = "false" ] || lc_die "staging database became public"
  [ "$vpc" = "$LC_VPC_ID" ] || lc_die "staging database was created in $vpc"
  [ "$encrypted" = "True" ] || [ "$encrypted" = "true" ] || lc_die "staging database is not encrypted"
  lc_log "RDS is available and verified private, encrypted, and in the staging VPC."
  printf '%s\n' "$endpoint"
}

lc_staging_master_secret_arn() {
  lc_require_environment staging
  local secret_arn
  secret_arn=$("${LC_AWS[@]}" rds describe-db-instances \
    --db-instance-identifier "$LC_DB_INSTANCE" \
    --query 'DBInstances[0].MasterUserSecret.SecretArn' --output text)
  lc_is_present "$secret_arn" || lc_die "RDS-managed master secret is missing"
  printf '%s\n' "$secret_arn"
}

lc_delete_staging_db() {
  local snapshot_name="${1:-}" status protection
  lc_require_environment staging
  status=$(lc_staging_db_status)
  if ! lc_is_present "$status"; then
    lc_log "Staging RDS $LC_DB_INSTANCE is already absent; no deletion needed."
    return 0
  fi
  [ "$status" != "deleting" ] || {
    lc_log "Staging RDS is already deleting; waiting for removal (typically 5–10 minutes)."
    "${LC_AWS[@]}" rds wait db-instance-deleted --db-instance-identifier "$LC_DB_INSTANCE"
    lc_log "Verified staging RDS is absent."
    return 0
  }

  protection=$("${LC_AWS[@]}" rds describe-db-instances \
    --db-instance-identifier "$LC_DB_INSTANCE" \
    --query 'DBInstances[0].DeletionProtection' --output text)
  if [ "$protection" = "True" ] || [ "$protection" = "true" ]; then
    lc_log "Disabling unexpected deletion protection before teardown."
    "${LC_AWS[@]}" rds modify-db-instance --db-instance-identifier "$LC_DB_INSTANCE" \
      --no-deletion-protection --apply-immediately >/dev/null
    protection=$("${LC_AWS[@]}" rds describe-db-instances \
      --db-instance-identifier "$LC_DB_INSTANCE" \
      --query 'DBInstances[0].DeletionProtection' --output text)
    [ "$protection" = "False" ] || [ "$protection" = "false" ] ||
      lc_die "staging database deletion protection is still enabled"
  fi

  if [ -n "$snapshot_name" ]; then
    lc_validate_staging_snapshot_name "$snapshot_name"
    lc_log "Deleting staging RDS while retaining $snapshot_name; database and snapshot completion typically take 10–20 minutes."
    "${LC_AWS[@]}" rds delete-db-instance --db-instance-identifier "$LC_DB_INSTANCE" \
      --final-db-snapshot-identifier "$snapshot_name" --delete-automated-backups >/dev/null
  else
    lc_log "Deleting staging RDS without a final snapshot; removal typically takes 5–10 minutes."
    "${LC_AWS[@]}" rds delete-db-instance --db-instance-identifier "$LC_DB_INSTANCE" \
      --skip-final-snapshot --delete-automated-backups >/dev/null
  fi
  status=$(lc_staging_db_status)
  [ "$status" = "deleting" ] || lc_die "staging database deletion did not start: $status"
  "${LC_AWS[@]}" rds wait db-instance-deleted --db-instance-identifier "$LC_DB_INSTANCE"
  status=$(lc_staging_db_status)
  lc_is_present "$status" && lc_die "staging database still exists after waiter: $status"
  lc_log "Verified staging RDS $LC_DB_INSTANCE is absent."

  if [ -n "$snapshot_name" ]; then
    lc_log "Waiting for retained snapshot $snapshot_name to become available."
    "${LC_AWS[@]}" rds wait db-snapshot-completed --db-snapshot-identifier "$snapshot_name"
    "${LC_AWS[@]}" rds describe-db-snapshots --db-snapshot-identifier "$snapshot_name" \
      --query 'DBSnapshots[0].{Status:Status,Encrypted:Encrypted,Size:AllocatedStorage}'
    lc_log "Verified retained snapshot $snapshot_name is complete."
  fi
}

lc_validate_staging_snapshot_name() {
  case "${1:-}" in
    onlineshop-staging-debug-*|onlineshop-staging-dr-*) return 0 ;;
    *) lc_die "retained snapshot name must start with onlineshop-staging-debug- or onlineshop-staging-dr-" ;;
  esac
}

lc_capture_diagnostics() {
  local task_arns alb_arn
  echo "=== $LC_ENVIRONMENT diagnostics ==="
  "${LC_AWS[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
    --services "${LC_SERVICES[@]}" \
    --query 'services[].{Name:serviceName,Desired:desiredCount,Running:runningCount,Pending:pendingCount,Events:events[:5],Deployments:deployments}' || true
  task_arns=$("${LC_AWS[@]}" ecs list-tasks --cluster "$LC_CLUSTER" \
    --desired-status STOPPED --query 'taskArns[-10:]' --output text 2>/dev/null || true)
  if lc_is_present "$task_arns"; then
    # shellcheck disable=SC2086
    "${LC_AWS[@]}" ecs describe-tasks --cluster "$LC_CLUSTER" --tasks $task_arns \
      --query 'tasks[].{Task:taskArn,StopCode:stopCode,StoppedReason:stoppedReason,Containers:containers[].{Name:name,Exit:exitCode,Reason:reason}}' || true
  fi
  alb_arn=$(lc_find_alb)
  if lc_is_present "$alb_arn"; then
    "${LC_AWS[@]}" elbv2 describe-target-health --target-group-arn "$LC_TARGET_GROUP_ARN" || true
  fi
  if [ "$LC_ENVIRONMENT" = "staging" ]; then
    "${LC_AWS[@]}" rds describe-db-instances --db-instance-identifier "$LC_DB_INSTANCE" \
      --query 'DBInstances[0].{Status:DBInstanceStatus,Vpc:DBSubnetGroup.VpcId,Public:PubliclyAccessible,Created:InstanceCreateTime}' || true
  fi
}
