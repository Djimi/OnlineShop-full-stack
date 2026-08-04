#!/usr/bin/env bash

# Shared, sourceable identifier helpers for the Pass 3, subphase 3.5
# production inventory and production/staging separation checks
# (scripts/inventory-production.sh, scripts/verify-production-staging-separation.sh).
#
# Both scripts compare an environment's explicit non-secret identifiers
# (scripts/config/{production,staging}.env) against live observed state. These
# helpers emit the identifier JSON for a config file:
#
#   identifiers_from_config <config.env>   # expected values straight from the config
#   identifiers_observed <config.env>      # live values read back from AWS (read-only)
#
# The JSON schema matches release_contract.environments (separation/inventory):
#   {vpcId, cluster, dbInstance, dbSubnetGroup, dbSecurityGroup, ecsSecurityGroup,
#    albSecurityGroup, albName, targetGroupArn, gatewayService, namespace,
#    executionRoleArn, frontendBucket, cloudfrontDistribution, dbPublicAccessible,
#    services[], subnets[], secrets[], logGroups[], ecrRepositories[]}
#
# Only stdout carries JSON; every diagnostic goes to stderr so command
# substitution captures valid JSON. Secret VALUES never appear — these helpers
# resolve secret names to their ARN (existence proof) and report a missing
# secret as "<name>-MISSING" so drift fails closed.
#
# Fail-closed read semantics: every AWS read either returns the observed
# identifier, the literal "missing" (the resource genuinely does not exist), or
# the literal "error" (the API call itself failed — auth, throttling, network).
# A caller that sees "error" must fail closed; a genuinely absent resource is
# reported as "missing". The two are never conflated.

# Every AWS call must force --profile dpm-profile --region eu-north-1. The
# config files carry these exact values; this guard rejects any override so no
# config or environment drift can redirect AWS calls.
lc_require_canonical_aws() {
  [ "${LC_PROFILE:-dpm-profile}" = "dpm-profile" ] || {
    echo "ERROR: LC_PROFILE must be dpm-profile (got ${LC_PROFILE:-unset}); not overridable" >&2
    return 2
  }
  [ "${LC_REGION:-eu-north-1}" = "eu-north-1" ] || {
    echo "ERROR: LC_REGION must be eu-north-1 (got ${LC_REGION:-unset}); not overridable" >&2
    return 2
  }
}

identifiers_from_config() {
  local config="$1"
  [ -f "$config" ] || { echo "ERROR: missing config: $config" >&2; return 2; }
  lc_require_canonical_aws || return 2
  (
    # shellcheck source=/dev/null
    source "$config"
    [ -n "${LC_VPC_ID:-}" ] || { echo "ERROR: $config is not a lifecycle config" >&2; return 2; }
    jq -n \
      --arg vpcId "${LC_VPC_ID:-}" \
      --arg cluster "${LC_CLUSTER:-}" \
      --arg dbInstance "${LC_DB_INSTANCE:-}" \
      --arg dbSubnetGroup "${LC_DB_SUBNET_GROUP:-}" \
      --arg dbSecurityGroup "${LC_DB_SECURITY_GROUP:-}" \
      --arg ecsSecurityGroup "${LC_ECS_SECURITY_GROUP:-}" \
      --arg albSecurityGroup "${LC_ALB_SECURITY_GROUP:-}" \
      --arg albName "${LC_ALB_NAME:-}" \
      --arg targetGroupArn "${LC_TARGET_GROUP_ARN:-}" \
      --arg gatewayService "${LC_GATEWAY_SERVICE:-}" \
      --arg namespace "${LC_NAMESPACE:-}" \
      --arg executionRoleArn "${LC_EXECUTION_ROLE_ARN:-}" \
      --arg frontendBucket "${LC_FRONTEND_BUCKET:-}" \
      --arg cloudfrontDistribution "${LC_CLOUDFRONT_DISTRIBUTION:-}" \
      --argjson services "$(printf '%s\n' "${LC_SERVICES[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      --argjson subnets "$(printf '%s\n' "${LC_ALB_SUBNETS[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      --argjson secrets "$(printf '%s\n' "${LC_SECRETS[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      --argjson logGroups "$(printf '%s\n' "${LC_LOG_GROUPS[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      --argjson ecrRepositories "$(printf '%s\n' "${LC_ECR_REPOSITORIES[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      '{vpcId: $vpcId, cluster: $cluster, dbInstance: $dbInstance, dbSubnetGroup: $dbSubnetGroup,
        dbSecurityGroup: $dbSecurityGroup, ecsSecurityGroup: $ecsSecurityGroup,
        albSecurityGroup: $albSecurityGroup, albName: $albName, targetGroupArn: $targetGroupArn,
        gatewayService: $gatewayService, namespace: $namespace, executionRoleArn: $executionRoleArn,
        frontendBucket: $frontendBucket, cloudfrontDistribution: $cloudfrontDistribution,
        services: $services, subnets: $subnets, secrets: $secrets, logGroups: $logGroups,
        ecrRepositories: $ecrRepositories}'
  )
}

# Emit a value if present; "missing" when the resource is genuinely absent;
# "error" when the AWS API call itself failed (so the caller fails closed and
# the operator sees the real error on stderr rather than a fake drift).
id_value() {
  local result err rc
  err=$(mktemp) || return 1
  set +e
  result=$("$@" 2>"$err")
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    rm -f "$err"
    if [ -n "$result" ] && [ "$result" != "None" ]; then
      printf '%s' "$result"
    else
      printf '%s' "missing"
    fi
    return 0
  fi
  if grep -qiE 'not ?found|does not exist|no such|could not be found|not be found|resource not found|\.NotFound|NoSuch' "$err"; then
    printf '%s' "missing"
  else
    sed -n '1,3p' "$err" >&2
    printf '%s' "error"
  fi
  rm -f "$err"
  return 0
}

# Observed network topology for an environment: which VPC the environment's
# security groups, subnets, and DB subnet group live in, and which Cloud Map
# namespace each of its services uses. This is the live isolation proof that
# complements the identifier-identity comparison: it catches a staging
# security group/subnet accidentally placed in the production VPC or a staging
# service registered in the production namespace.
topology_observed() {
  local config="$1"
  [ -f "$config" ] || { echo "ERROR: missing config: $config" >&2; return 2; }
  lc_require_canonical_aws || return 2
  (
    # shellcheck source=/dev/null
    source "$config"
    [ -n "${LC_VPC_ID:-}" ] || { echo "ERROR: $config is not a lifecycle config" >&2; return 2; }
    local aws=(aws --profile "${LC_PROFILE:-dpm-profile}" --region "${LC_REGION:-eu-north-1}")
    value() { id_value "$@" || printf '%s' "error"; }

    local vpcId sg_vpcs=() subnet_vpcs=() db_subnet_group_vpc
    vpcId=$(value "${aws[@]}" ec2 describe-vpcs --vpc-ids "$LC_VPC_ID" --query 'Vpcs[0].VpcId' --output text)
    local sg
    for sg in "$LC_DB_SECURITY_GROUP" "$LC_ECS_SECURITY_GROUP" "$LC_ALB_SECURITY_GROUP"; do
      sg_vpcs+=("$(value "${aws[@]}" ec2 describe-security-groups --group-ids "$sg" --query 'SecurityGroups[0].VpcId' --output text)")
    done
    local subnet
    for subnet in "${LC_ALB_SUBNETS[@]:-}"; do
      subnet_vpcs+=("$(value "${aws[@]}" ec2 describe-subnets --subnet-ids "$subnet" --query 'Subnets[0].VpcId' --output text)")
    done
    db_subnet_group_vpc=$(value "${aws[@]}" rds describe-db-subnet-groups \
      --db-subnet-group-name "$LC_DB_SUBNET_GROUP" --query 'DBSubnetGroups[0].VpcId' --output text)

    local namespaces_json svc ns
    namespaces_json=$(jq -n '{}')
    for svc in "${LC_SERVICES[@]:-}"; do
      ns=$(value "${aws[@]}" ecs describe-services --cluster "$LC_CLUSTER" \
        --services "$svc" --query 'services[0].serviceConnectConfiguration.namespace' --output text)
      namespaces_json=$(jq --arg s "$svc" --arg n "$ns" '. + {($s): $n}' <<<"$namespaces_json")
    done

    jq -n \
      --arg vpcId "$vpcId" \
      --argjson sgVpcs "$(printf '%s\n' "${sg_vpcs[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      --argjson subnetVpcs "$(printf '%s\n' "${subnet_vpcs[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      --arg dbSubnetGroupVpc "$db_subnet_group_vpc" \
      --argjson serviceNamespaces "$namespaces_json" \
      '{vpcId: $vpcId, sgVpcs: $sgVpcs, subnetVpcs: $subnetVpcs,
        dbSubnetGroupVpc: $dbSubnetGroupVpc, serviceNamespaces: $serviceNamespaces}'
  )
}

identifiers_observed() {
  local config="$1"
  [ -f "$config" ] || { echo "ERROR: missing config: $config" >&2; return 2; }
  lc_require_canonical_aws || return 2
  (
    # shellcheck source=/dev/null
    source "$config"
    [ -n "${LC_VPC_ID:-}" ] || { echo "ERROR: $config is not a lifecycle config" >&2; return 2; }
    local aws=(aws --profile "${LC_PROFILE:-dpm-profile}" --region "${LC_REGION:-eu-north-1}")

    # Each observed identifier is the identifier itself when the resource
    # exists, "missing" when it is genuinely absent, or "error" when the AWS
    # read itself failed — so an API failure fails closed and is never
    # disguised as drift or silence.
    local vpcId cluster dbInstance dbSubnetGroup dbSecurityGroup ecsSecurityGroup albSecurityGroup
    local albName targetGroupArn gatewayService namespace executionRoleArn dbPublicAccessible
    vpcId=$(id_value "${aws[@]}" ec2 describe-vpcs --vpc-ids "$LC_VPC_ID" --query 'Vpcs[0].VpcId' --output text)

    cluster="missing"
    case "$(id_value "${aws[@]}" ecs describe-clusters --clusters "$LC_CLUSTER" \
      --query 'clusters[0].status' --output text)" in
      ACTIVE) cluster="$LC_CLUSTER" ;;
      error) cluster="error" ;;
    esac

    dbInstance="missing"
    case "$(id_value "${aws[@]}" rds describe-db-instances --db-instance-identifier "$LC_DB_INSTANCE" \
      --query 'DBInstances[0].DBInstanceStatus' --output text)" in
      available) dbInstance="$LC_DB_INSTANCE" ;;
      error) dbInstance="error" ;;
    esac

    dbSubnetGroup="missing"
    case "$(id_value "${aws[@]}" rds describe-db-subnet-groups --db-subnet-group-name "$LC_DB_SUBNET_GROUP" \
      --query 'DBSubnetGroups[0].DBSubnetGroupName' --output text)" in
      "$LC_DB_SUBNET_GROUP") dbSubnetGroup="$LC_DB_SUBNET_GROUP" ;;
      error) dbSubnetGroup="error" ;;
    esac
    dbSecurityGroup="missing"
    case "$(id_value "${aws[@]}" ec2 describe-security-groups --group-ids "$LC_DB_SECURITY_GROUP" \
      --query 'SecurityGroups[0].GroupId' --output text)" in
      "$LC_DB_SECURITY_GROUP") dbSecurityGroup="$LC_DB_SECURITY_GROUP" ;;
      error) dbSecurityGroup="error" ;;
    esac
    ecsSecurityGroup="missing"
    case "$(id_value "${aws[@]}" ec2 describe-security-groups --group-ids "$LC_ECS_SECURITY_GROUP" \
      --query 'SecurityGroups[0].GroupId' --output text)" in
      "$LC_ECS_SECURITY_GROUP") ecsSecurityGroup="$LC_ECS_SECURITY_GROUP" ;;
      error) ecsSecurityGroup="error" ;;
    esac
    albSecurityGroup="missing"
    case "$(id_value "${aws[@]}" ec2 describe-security-groups --group-ids "$LC_ALB_SECURITY_GROUP" \
      --query 'SecurityGroups[0].GroupId' --output text)" in
      "$LC_ALB_SECURITY_GROUP") albSecurityGroup="$LC_ALB_SECURITY_GROUP" ;;
      error) albSecurityGroup="error" ;;
    esac

    albName="missing"
    case "$(id_value "${aws[@]}" elbv2 describe-load-balancers --names "$LC_ALB_NAME" \
      --query 'LoadBalancers[0].LoadBalancerName' --output text)" in
      "$LC_ALB_NAME") albName="$LC_ALB_NAME" ;;
      error) albName="error" ;;
    esac
    targetGroupArn=$(id_value "${aws[@]}" elbv2 describe-target-groups --target-group-arns "$LC_TARGET_GROUP_ARN" \
      --query 'TargetGroups[0].TargetGroupArn' --output text)

    gatewayService="missing"
    case "$(id_value "${aws[@]}" ecs describe-services --cluster "$LC_CLUSTER" --services "$LC_GATEWAY_SERVICE" \
      --query 'services[0].status' --output text)" in
      ACTIVE) gatewayService="$LC_GATEWAY_SERVICE" ;;
      error) gatewayService="error" ;;
    esac
    namespace=$(id_value "${aws[@]}" servicediscovery list-namespaces \
      --query "Namespaces[?Name==\`$LC_NAMESPACE\`].Name | [0]" --output text)

    executionRoleArn="missing"
    if [ -n "${LC_EXECUTION_ROLE_ARN:-}" ]; then
      local role_name
      role_name=$(printf '%s' "$LC_EXECUTION_ROLE_ARN" | sed 's#.*:role/##')
      case "$(id_value "${aws[@]}" iam get-role --role-name "$role_name" \
        --query 'Role.Arn' --output text)" in
        "$LC_EXECUTION_ROLE_ARN") executionRoleArn="$LC_EXECUTION_ROLE_ARN" ;;
        error) executionRoleArn="error" ;;
      esac
    fi

    dbPublicAccessible="missing"
    if [ -n "${LC_DB_INSTANCE:-}" ]; then
      case "$(id_value "${aws[@]}" rds describe-db-instances --db-instance-identifier "$LC_DB_INSTANCE" \
        --query 'DBInstances[0].PubliclyAccessible' --output text)" in
        true|True) dbPublicAccessible="true" ;;
        false|False) dbPublicAccessible="false" ;;
        error) dbPublicAccessible="error" ;;
      esac
    fi

    local services=() subnets=() secrets=() logGroups=() ecrRepositories=()
    local svc subnet secret secret_arn group repo
    for svc in "${LC_SERVICES[@]:-}"; do
      case "$(id_value "${aws[@]}" ecs describe-services --cluster "$LC_CLUSTER" --services "$svc" \
        --query 'services[0].status' --output text)" in
        ACTIVE) services+=("$svc") ;;
        error) services+=("$svc-ERROR") ;;
        *) services+=("$svc-MISSING") ;;
      esac
    done
    for subnet in "${LC_ALB_SUBNETS[@]:-}"; do
      case "$(id_value "${aws[@]}" ec2 describe-subnets --subnet-ids "$subnet" \
        --query 'Subnets[0].SubnetId' --output text)" in
        "$subnet") subnets+=("$subnet") ;;
        error) subnets+=("$subnet-ERROR") ;;
        *) subnets+=("$subnet-MISSING") ;;
      esac
    done
    for secret in "${LC_SECRETS[@]:-}"; do
      secret_arn=$(id_value "${aws[@]}" secretsmanager describe-secret --secret-id "$secret" \
        --query 'ARN' --output text)
      case "$secret_arn" in
        missing) secrets+=("$secret-MISSING") ;;
        error) secrets+=("$secret-ERROR") ;;
        *) secrets+=("$secret") ;;
      esac
    done
    for group in "${LC_LOG_GROUPS[@]:-}"; do
      case "$(id_value "${aws[@]}" logs describe-log-groups --log-group-name-prefix "$group" \
        --query "logGroups[?logGroupName==\`$group\`] | length(@)" --output text)" in
        1) logGroups+=("$group") ;;
        error) logGroups+=("$group-ERROR") ;;
        *) logGroups+=("$group-MISSING") ;;
      esac
    done
    for repo in "${LC_ECR_REPOSITORIES[@]:-}"; do
      case "$(id_value "${aws[@]}" ecr describe-repositories --repository-names "$repo" \
        --query 'repositories[0].repositoryName' --output text)" in
        "$repo") ecrRepositories+=("$repo") ;;
        error) ecrRepositories+=("$repo-ERROR") ;;
        *) ecrRepositories+=("$repo-MISSING") ;;
      esac
    done

    # Frontend delivery resources: the bucket must exist and the CloudFront
    # distribution must exist (their exact status is reported separately).
    local frontendBucket cloudfrontDistribution
    frontendBucket="missing"
    if [ -n "${LC_FRONTEND_BUCKET:-}" ]; then
      local rc err_file
      err_file=$(mktemp) || exit 1
      set +e
      "${aws[@]}" s3api head-bucket --bucket "$LC_FRONTEND_BUCKET" >/dev/null 2>"$err_file"
      rc=$?
      set -e
      if [ "$rc" -eq 0 ]; then
        frontendBucket="$LC_FRONTEND_BUCKET"
      elif grep -qiE 'not ?found|does not exist|no such|NoSuchBucket|\.NotFound|NoSuch' "$err_file"; then
        frontendBucket="missing"
      else
        sed -n '1,3p' "$err_file" >&2
        frontendBucket="error"
      fi
      rm -f "$err_file"
    fi
    cloudfrontDistribution="missing"
    if [ -n "${LC_CLOUDFRONT_DISTRIBUTION:-}" ]; then
      case "$(id_value "${aws[@]}" cloudfront get-distribution --id "$LC_CLOUDFRONT_DISTRIBUTION" \
        --query 'Distribution.Id' --output text)" in
        "$LC_CLOUDFRONT_DISTRIBUTION") cloudfrontDistribution="$LC_CLOUDFRONT_DISTRIBUTION" ;;
        error) cloudfrontDistribution="error" ;;
      esac
    fi

    jq -n \
      --arg vpcId "$vpcId" --arg cluster "$cluster" \
      --arg dbInstance "$dbInstance" --arg dbSubnetGroup "$dbSubnetGroup" \
      --arg dbSecurityGroup "$dbSecurityGroup" --arg ecsSecurityGroup "$ecsSecurityGroup" \
      --arg albSecurityGroup "$albSecurityGroup" --arg albName "$albName" \
      --arg targetGroupArn "$targetGroupArn" --arg gatewayService "$gatewayService" \
      --arg namespace "$namespace" --arg executionRoleArn "$executionRoleArn" \
      --arg dbPublicAccessible "$dbPublicAccessible" \
      --arg frontendBucket "$frontendBucket" --arg cloudfrontDistribution "$cloudfrontDistribution" \
      --argjson services "$(printf '%s\n' "${services[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      --argjson subnets "$(printf '%s\n' "${subnets[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      --argjson secrets "$(printf '%s\n' "${secrets[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      --argjson logGroups "$(printf '%s\n' "${logGroups[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      --argjson ecrRepositories "$(printf '%s\n' "${ecrRepositories[@]:-}" | jq -R -s 'split("\n") | map(select(length>0))')" \
      '{vpcId: $vpcId, cluster: $cluster, dbInstance: $dbInstance, dbSubnetGroup: $dbSubnetGroup,
        dbSecurityGroup: $dbSecurityGroup, ecsSecurityGroup: $ecsSecurityGroup,
        albSecurityGroup: $albSecurityGroup, albName: $albName, targetGroupArn: $targetGroupArn,
        gatewayService: $gatewayService, namespace: $namespace, executionRoleArn: $executionRoleArn,
        dbPublicAccessible: $dbPublicAccessible,
        frontendBucket: $frontendBucket, cloudfrontDistribution: $cloudfrontDistribution,
        services: $services, subnets: $subnets, secrets: $secrets, logGroups: $logGroups,
        ecrRepositories: $ecrRepositories}'
  )
}
