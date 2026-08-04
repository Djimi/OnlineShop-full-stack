#!/usr/bin/env bash
# Restores the fully isolated, snapshot-backed staging environment.
set -euo pipefail

PROFILE="dpm-profile"
REGION="eu-north-1"
CLUSTER="onlineshop-staging-cluster"
DB_INSTANCE="onlineshop-staging-postgres"
DB_SNAPSHOT="onlineshop-staging-latest"
DB_SUBNET_GROUP="onlineshop-staging-db-subnets"
DB_SG="sg-08c5d1008d1ce54ae"
ALB_NAME="onlineshop-staging-v2-alb"
TG_ARN="arn:aws:elasticloadbalancing:eu-north-1:799111666795:targetgroup/onlineshop-staging-tg-v2/8a9b0471c381e60b"
ALB_SG="sg-0e4c072113dd8d1e9"
SUBNETS=(subnet-04f5da5a8cf1b1350 subnet-06b823d8d6b24333b)
SERVICES=(onlineshop-auth-staging onlineshop-items-staging onlineshop-api-gateway-staging)
AWS=(aws --profile "$PROFILE" --region "$REGION")

CAPACITY_PROVIDER="FARGATE_SPOT"
if [ "${1:-}" = "--on-demand" ]; then
  CAPACITY_PROVIDER="FARGATE"
elif [ -n "${1:-}" ]; then
  echo "Usage: $0 [--on-demand]" >&2
  exit 1
fi

"${AWS[@]}" sts get-caller-identity >/dev/null

db_status=$("${AWS[@]}" rds describe-db-instances --db-instance-identifier "$DB_INSTANCE" \
  --query 'DBInstances[0].DBInstanceStatus' --output text 2>/dev/null || true)
if [ -z "$db_status" ] || [ "$db_status" = "None" ]; then
  "${AWS[@]}" rds restore-db-instance-from-db-snapshot \
    --db-instance-identifier "$DB_INSTANCE" \
    --db-snapshot-identifier "$DB_SNAPSHOT" \
    --db-instance-class db.t4g.micro \
    --db-subnet-group-name "$DB_SUBNET_GROUP" \
    --vpc-security-group-ids "$DB_SG" \
    --no-publicly-accessible --no-multi-az \
    --no-auto-minor-version-upgrade \
    --deletion-protection \
    --tags Key=Environment,Value=staging Key=Name,Value=onlineshop-staging-postgres >/dev/null
  "${AWS[@]}" rds describe-db-instances --db-instance-identifier "$DB_INSTANCE" \
    --query 'DBInstances[0].{Status:DBInstanceStatus,Vpc:DBSubnetGroup.VpcId,Public:PubliclyAccessible}'
fi
"${AWS[@]}" rds wait db-instance-available --db-instance-identifier "$DB_INSTANCE"

alb_arn=$("${AWS[@]}" elbv2 describe-load-balancers --names "$ALB_NAME" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || true)
if [ -z "$alb_arn" ] || [ "$alb_arn" = "None" ]; then
  alb_arn=$("${AWS[@]}" elbv2 create-load-balancer --name "$ALB_NAME" \
    --subnets "${SUBNETS[@]}" --security-groups "$ALB_SG" --scheme internet-facing \
    --type application --ip-address-type ipv4 --tags Key=Environment,Value=staging \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text)
  "${AWS[@]}" elbv2 describe-load-balancers --load-balancer-arns "$alb_arn" \
    --query 'LoadBalancers[0].{Arn:LoadBalancerArn,Vpc:VpcId,State:State.Code}'
  listener=$("${AWS[@]}" elbv2 create-listener --load-balancer-arn "$alb_arn" \
    --protocol HTTP --port 80 --default-actions Type=forward,TargetGroupArn="$TG_ARN" \
    --query 'Listeners[0].ListenerArn' --output text)
  "${AWS[@]}" elbv2 describe-listeners --listener-arns "$listener" \
    --query 'Listeners[0].{Arn:ListenerArn,Port:Port,Actions:DefaultActions}'
fi

for service in "${SERVICES[@]}"; do
  "${AWS[@]}" ecs update-service --cluster "$CLUSTER" --service "$service" \
    --desired-count 1 --capacity-provider-strategy "capacityProvider=$CAPACITY_PROVIDER,weight=1" \
    --force-new-deployment >/dev/null
  desired=$("${AWS[@]}" ecs describe-services --cluster "$CLUSTER" --services "$service" \
    --query 'services[0].desiredCount' --output text)
  [ "$desired" = "1" ] || { echo "ERROR: $service did not scale to one" >&2; exit 1; }
done
"${AWS[@]}" ecs wait services-stable --cluster "$CLUSTER" --services "${SERVICES[@]}"

dns=$("${AWS[@]}" elbv2 describe-load-balancers --load-balancer-arns "$alb_arn" \
  --query 'LoadBalancers[0].DNSName' --output text)
ready=false
for attempt in $(seq 1 12); do
  status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --header 'Authorization: Bearer staging-readiness-invalid-token' \
    "http://$dns/items" || true)
  if [ "$status" = "401" ]; then ready=true; break; fi
  sleep 5
done
[ "$ready" = true ] || { echo "ERROR: staging API did not become ready within 60 seconds" >&2; exit 1; }
echo "Staging is running on $CAPACITY_PROVIDER: http://$dns"
