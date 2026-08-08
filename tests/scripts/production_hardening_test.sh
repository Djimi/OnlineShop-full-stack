#!/usr/bin/env bash
set -euo pipefail

# Production environment hardening (Pass 3, subphase 3.5) verification gate.
#
# Runs the offline parts of the 3.5 gate:
#   [ 1/11] Python syntax + unit tests (ecs_config, sanitize, frontend_hosting,
#           cloudtrail, environments)
#   [ 2/11] Task-definition hardening fixtures (valid pass; every invalid
#           fixture fails for its intended rule)
#   [ 3/11] Service-config hardening fixtures (circuit breaker + rolling)
#   [ 4/11] sanitize-task-definition.sh: digest-pin transform + image-only diff,
#           secrets stay in secrets[].valueFrom with full ARNs
#   [ 5/11] validate-task-definition.sh + sanitize-task-definition.sh CLI paths
#   [ 6/11] inventory-production.sh with stateful AWS stubs: OK state passes,
#           drift fails closed, read-only (no mutation calls), identity preflight
#   [ 7/11] verify-production-staging-separation.sh: static configs isolated;
#           live stubbed observed state isolated; shared VPC fails closed
#   [ 8/11] verify-frontend-oac.sh + migrate-frontend-oac.sh: hardened state
#           passes, website/public drift fails, dry-run mutates nothing, apply
#           mutates + reads back, read-back drift fails closed
#   [ 9/11] verify-cloudtrail-coverage.sh with stubs: coverage passes; gaps fail
#   [10/11] Lifecycle environment guards: production helpers cannot reach the
#           clean-staging database create/bootstrap/delete paths
#   [11/11] Static scan (profile/region on every aws call, mutation read-backs,
#           no secrets) + lint (ruff + shellcheck + git diff --check)
#
# Live hardening (real inventory read-back, real OAC migration, real CloudTrail
# read-back, real service/config verification, security-group/IAM tightening)
# is deferred to the consolidated verification pass and is NOT claimed here.

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE="$REPO_ROOT/plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
FIXTURES="$RELEASE/fixtures/production"
SCRIPTS="$REPO_ROOT/scripts"
RELEASE_BIN="$RELEASE/bin"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_success() {
  "$@" >/dev/null 2>&1 || fail "expected success: $*"
}

assert_failure() {
  if "$@" >/dev/null 2>&1; then
    fail "expected failure: $*"
  fi
}

assert_contains() {
  local value="$1" expected="$2"
  [[ "$value" == *"$expected"* ]] || fail "expected output to contain: $expected"
}

expect_issue_code() {
  # $1 = stdout from a validator, $2 = expected issue code.
  local value="$1" expected="$2"
  printf '%s' "$value" | jq -e --arg code "$expected" '.issues[] | select(.code == $code)' >/dev/null \
    || fail "expected issue code $expected not present in: $value"
}

# ---------------------------------------------------------------------------
echo "[ 1/11] Python syntax + unit tests"
python3 -m py_compile "$RELEASE"/src/release_contract/*.py "$RELEASE"/tests/*.py || fail "Python syntax check failed"
(
  cd "$RELEASE" && PYTHONPATH="$RELEASE/src" python3 -m unittest discover -s tests
) || fail "Python validation tests failed"

# ---------------------------------------------------------------------------
echo "[ 2/11] Task-definition hardening fixtures"
for fx in valid-auth valid-items valid-gateway; do
  assert_success bash "$RELEASE_BIN/validate-task-definition.sh" --input "$FIXTURES/taskdef/$fx.json"
done
declare -A TD_INVALID_CODES=(
  [invalid-cpu-memory]=INVALID_CPU_MEMORY
  [invalid-floating-image]=FLOATING_IMAGE
  [invalid-network-mode]=NETWORK_MODE
  [invalid-not-fargate]=NOT_FARGATE
  [invalid-no-health]=MISSING_HEALTH_CHECK
  [invalid-no-logs]=MISSING_LOGS
  [invalid-no-stop-timeout]=INVALID_STOP_TIMEOUT
  [invalid-no-version-consistency]=VERSION_CONSISTENCY_DISABLED
  [invalid-secret-in-env]=SECRET_PLAINTEXT_IN_ENV
  [invalid-short-secret-arn]=SECRET_SHORT_ARN
  [invalid-unamed-port]=UNNAMED_PORT
)
for fx in "${!TD_INVALID_CODES[@]}"; do
  OUT=$(bash "$RELEASE_BIN/validate-task-definition.sh" --input "$FIXTURES/taskdef/$fx.json" 2>&1) \
    && fail "$fx fixture must fail validation"
  expect_issue_code "$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.ecs_config validate-td \
    --input "$FIXTURES/taskdef/$fx.json")" "${TD_INVALID_CODES[$fx]}"
done

# ---------------------------------------------------------------------------
echo "[ 3/11] Service-config hardening fixtures"
assert_success bash "$RELEASE_BIN/validate-task-definition.sh" --input "$FIXTURES/taskdef/valid-auth.json"
OUT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.ecs_config validate-service \
  --input "$FIXTURES/service/valid-auth-service.json" \
  --task-definition "$FIXTURES/taskdef/valid-auth.json")
assert_contains "$OUT" '"valid":true'
for pair in "invalid-circuit-breaker-off:CIRCUIT_BREAKER_DISABLED" "invalid-rolling:MIN_HEALTHY_PERCENT" \
  "invalid-no-capacity-provider:MISSING_CAPACITY_PROVIDER" "invalid-sc-port:SC_PORT_NOT_IN_TD"; do
  fx="${pair%%:*}"
  code="${pair##*:}"
  OUT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.ecs_config validate-service \
    --input "$FIXTURES/service/$fx.json" \
    --task-definition "$FIXTURES/taskdef/valid-auth.json") && fail "$fx fixture must fail service validation"
  expect_issue_code "$OUT" "$code"
done

# ---------------------------------------------------------------------------
echo "[ 4/11] sanitize-task-definition.sh: digest-pin + image-only diff"
assert_success bash "$RELEASE_BIN/sanitize-task-definition.sh" \
  --input "$FIXTURES/sanitize/input-auth.json" \
  --output "$TMP/sanitized-auth.json" \
  --set-image "auth=799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth@sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"
assert_success bash "$RELEASE_BIN/validate-task-definition.sh" --input "$TMP/sanitized-auth.json"
# Only the image changed.
DIFF=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.sanitize assert \
  --original "$FIXTURES/sanitize/input-auth.json" --sanitized "$TMP/sanitized-auth.json")
assert_contains "$DIFF" '"valid":true'
CHANGED=$(printf '%s' "$DIFF" | jq '.changedFields | length')
[ "$CHANGED" = "1" ] || fail "sanitize diff must change exactly one field (image), changed $CHANGED"
# Secrets stay in valueFrom with full ARNs and never appear as plaintext.
jq -e '.containerDefinitions[0].secrets | all(.valueFrom | startswith("arn:aws:secretsmanager:"))' "$TMP/sanitized-auth.json" >/dev/null \
  || fail "sanitized definition must keep full-ARN secrets in valueFrom"
python3 - "$TMP/sanitized-auth.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    td = json.load(handle)
container = td["containerDefinitions"][0]
env_text = json.dumps(container.get("environment", []))
command_text = json.dumps(container.get("command", []))
for secret in container.get("secrets", []):
    assert secret["valueFrom"] not in env_text, "secret ARN leaked to environment"
    assert secret["valueFrom"] not in command_text, "secret ARN leaked to command"
PY

# ---------------------------------------------------------------------------
echo "[ 5/11] validate-task-definition.sh + sanitize CLI error paths"
assert_failure bash "$RELEASE_BIN/validate-task-definition.sh" --input /nonexistent
assert_failure bash "$RELEASE_BIN/sanitize-task-definition.sh" --input "$FIXTURES/sanitize/input-auth.json" \
  --output "$TMP/x.json" --set-image "missing=repo@sha256:0000000000000000000000000000000000000000000000000000000000000000"
assert_failure bash "$RELEASE_BIN/sanitize-task-definition.sh" --input "$FIXTURES/sanitize/input-auth.json" \
  --output "$TMP/y.json" --set-image "auth=repo:not-a-digest"

# ---------------------------------------------------------------------------
# Stub AWS + supporting CLIs. The stub is stateful ($TMP/state.json), records
# every call ($TMP/calls.txt), and honors STUB_IGNORE_MUTATIONS=1 to simulate
# read-back drift. STUB_STATE/STUB_CALLS/PATH are exported so all script
# invocations transparently use the stub.
# ---------------------------------------------------------------------------
write_stub_clis() {
  mkdir -p "$TMP/bin"
  cat > "$TMP/bin/aws" <<'PY'
#!/usr/bin/env python3
import json
import os
import re
import sys

state_path = os.environ["STUB_STATE"]
calls_path = os.environ["STUB_CALLS"]
state = json.load(open(state_path, encoding="utf-8"))
args = sys.argv[1:]


def record(line):
    with open(calls_path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def arg(name):
    for index, item in enumerate(args):
        if item == name and index + 1 < len(args):
            return args[index + 1]
    return None


def query():
    return arg("--query") or ""


def output_format():
    return arg("--output") or "text"


def text(value):
    print(value)


def emit(value):
    if output_format() == "json":
        print(json.dumps(value, sort_keys=True))
    else:
        print(value)


def fail():
    # Not-found errors print a NotFound message (classified as "missing" by the
    # shell helpers); STUB_API_ERROR=1 simulates a real API failure such as
    # AccessDenied (classified as "error", never as "missing").
    if os.environ.get("STUB_API_ERROR") == "1":
        print("An error occurred (AccessDenied) when calling the operation: "
              "User is not authorized", file=sys.stderr)
    else:
        print("An error occurred (NotFound) when calling the operation: "
              "the resource does not exist", file=sys.stderr)
    sys.exit(255)


def persist():
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def mutate(fn):
    if os.environ.get("STUB_IGNORE_MUTATIONS") == "1":
        return None
    return fn()


def read_file(flag):
    value = arg(flag) or ""
    if value.startswith("file://"):
        with open(value[len("file://"):], encoding="utf-8") as handle:
            return handle.read()
    return value


def between_backticks(text):
    match = re.search(r"`([^`]+)`", text or "")
    return match.group(1) if match else None


def positionals(argv):
    # AWS CLI allows options before the service (aws --profile p --region r
    # service sub ...). Collect the positional tokens, skipping every
    # --flag <value> pair where the value does not itself start with '--'.
    result = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("--"):
            if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                index += 2
            else:
                index += 1
        else:
            result.append(token)
            index += 1
    return result


pos = positionals(args)
service = pos[0] if len(pos) > 0 else ""
sub = pos[1] if len(pos) > 1 else ""
q = query()

# STUB_API_ERROR=1 simulates a live AWS API outage (e.g. AccessDenied) on every
# read except the identity preflight, which must still succeed.
if os.environ.get("STUB_API_ERROR") == "1" and not (service == "sts" and sub == "get-caller-identity"):
    fail()

if service == "sts" and sub == "get-caller-identity":
    record("sts get-caller-identity")
    text(state.get("identity", "799111666795"))

elif service == "ec2" and sub == "describe-vpcs":
    record(f"ec2 describe-vpcs {arg('--vpc-ids')}")
    vpc = arg("--vpc-ids")
    if vpc in state.get("vpcs", []):
        text(vpc)
    else:
        fail()

elif service == "ec2" and sub == "describe-subnets":
    ids = (arg("--subnet-ids") or "").split()
    record(f"ec2 describe-subnets {len(ids)}")
    if "Subnets[].VpcId" in q:
        emit([state.get("subnetVpc", {})[i] for i in ids if i in state.get("subnetVpc", {})])
    elif "Subnets[0].VpcId" in q:
        subnet = arg("--subnet-ids")
        if subnet in state.get("subnetVpc", {}):
            text(state["subnetVpc"][subnet])
        else:
            fail()
    else:
        subnet = arg("--subnet-ids")
        if subnet in state.get("subnetVpc", {}):
            text(subnet)
        else:
            fail()

elif service == "ec2" and sub == "describe-security-groups":
    ids = (arg("--group-ids") or "").split()
    record(f"ec2 describe-security-groups {len(ids)}")
    if "SecurityGroups[].VpcId" in q:
        emit([state.get("sgVpc", {})[i] for i in ids if i in state.get("sgVpc", {})])
    elif "SecurityGroups[0].GroupId" in q:
        sg = arg("--group-ids")
        if sg in state.get("sgVpc", {}):
            text(sg)
        else:
            fail()
    else:
        sg = arg("--group-ids")
        if sg in state.get("sgVpc", {}):
            text(state["sgVpc"][sg])
        else:
            fail()

elif service == "ecs" and sub == "describe-clusters":
    record(f"ecs describe-clusters {arg('--clusters')}")
    cluster = arg("--clusters")
    if cluster in state.get("clusters", {}):
        text(state["clusters"][cluster])
    else:
        fail()

elif service == "ecs" and sub == "list-services":
    record(f"ecs list-services {arg('--cluster')}")
    emit(state.get("serviceArns", {}).get(arg("--cluster"), []))

elif service == "ecs" and sub == "describe-services":
    cluster = arg("--cluster")
    svc = arg("--services")
    record(f"ecs describe-services {cluster} {svc}")
    entry = state.get("services", {}).get(cluster, {}).get(svc)
    if entry is None:
        fail()
    if "serviceConnectConfiguration.namespace" in q:
        text(entry.get("namespace", "missing"))
    elif "services[0].status" in q:
        text(entry.get("status", "missing"))
    else:
        emit(entry)

elif service == "rds" and sub == "describe-db-instances":
    record(f"rds describe-db-instances {arg('--db-instance-identifier')}")
    db = arg("--db-instance-identifier")
    if db not in state.get("dbInstances", {}):
        fail()
    if "PubliclyAccessible" in q:
        text("false" if state.get("dbInstancesPrivate", {}).get(db) else "true")
    else:
        text(state["dbInstances"][db])

elif service == "rds" and sub == "describe-db-subnet-groups":
    record(f"rds describe-db-subnet-groups {arg('--db-subnet-group-name')}")
    group = arg("--db-subnet-group-name")
    if group in state.get("dbSubnetGroups", {}):
        if "DBSubnetGroupName" in q:
            text(group)
        else:
            text(state["dbSubnetGroups"][group])
    else:
        fail()

elif service == "elbv2" and sub == "describe-load-balancers":
    record(f"elbv2 describe-load-balancers {arg('--names')}")
    alb = arg("--names")
    if alb in state.get("loadBalancers", {}):
        if "LoadBalancerName" in q:
            text(alb)
        else:
            text(state["loadBalancers"][alb])
    else:
        fail()

elif service == "elbv2" and sub == "describe-target-groups":
    record(f"elbv2 describe-target-groups")
    arn = arg("--target-group-arns")
    if arn in state.get("targetGroups", {}):
        text(state["targetGroups"][arn])
    else:
        fail()

elif service == "servicediscovery" and sub == "list-namespaces":
    name = between_backticks(q)
    record(f"servicediscovery list-namespaces {name}")
    if name in state.get("namespaces", []):
        text(name)
    else:
        text("")

elif service == "secretsmanager" and sub == "describe-secret":
    secret = arg("--secret-id")
    record(f"secretsmanager describe-secret {secret}")
    if secret in state.get("secrets", {}):
        text(state["secrets"][secret])
    else:
        fail()

elif service == "logs" and sub == "describe-log-groups":
    group = between_backticks(q) or arg("--log-group-name-prefix")
    record(f"logs describe-log-groups {group}")
    if group in state.get("logGroups", []):
        text("1")
    else:
        text("0")

elif service == "iam" and sub == "get-role":
    role = arg("--role-name")
    record(f"iam get-role {role}")
    if role in state.get("roles", {}):
        text(state["roles"][role])
    else:
        fail()

elif service == "ecr" and sub == "describe-repositories":
    repo = arg("--repository-names")
    record(f"ecr describe-repositories {repo}")
    if repo in state.get("ecrRepositories", []):
        if "repositoryName" in q:
            text(repo)
        else:
            emit({"repositories": [{"repositoryName": repo}]})
    else:
        fail()

elif service == "s3api" and sub == "head-bucket":
    bucket = arg("--bucket")
    record(f"s3api head-bucket {bucket}")
    if state.get("frontend", {}).get(bucket, {}).get("exists"):
        sys.exit(0)
    fail()

elif service == "s3api" and sub == "get-bucket-policy":
    bucket = arg("--bucket")
    record(f"s3api get-bucket-policy {bucket}")
    policy = state.get("frontend", {}).get(bucket, {}).get("bucketPolicy")
    if policy is None:
        fail()
    if "Policy" in q and output_format() == "text":
        text(json.dumps(policy))
    else:
        emit({"Policy": json.dumps(policy)})

elif service == "s3api" and sub == "put-bucket-policy":
    bucket = arg("--bucket")
    record(f"s3api put-bucket-policy {bucket}")
    raw = read_file("--policy")
    mutate(lambda: state["frontend"][bucket].__setitem__("bucketPolicy", json.loads(raw)))
    emit({"bucket": bucket})

elif service == "s3api" and sub == "get-public-access-block":
    bucket = arg("--bucket")
    record(f"s3api get-public-access-block {bucket}")
    pab = state.get("frontend", {}).get(bucket, {}).get("publicAccessBlock")
    if pab is None:
        fail()
    if "PublicAccessBlockConfiguration" in q:
        emit(pab)
    else:
        emit({"PublicAccessBlockConfiguration": pab})

elif service == "s3api" and sub == "put-public-access-block":
    bucket = arg("--bucket")
    record(f"s3api put-public-access-block {bucket}")
    raw = arg("--public-access-block-configuration") or ""
    parsed = {}
    for pair in raw.split(","):
        if "=" in pair:
            key, value = pair.split("=", 1)
            parsed[key.strip()] = value.strip().lower() == "true"
    mutate(lambda: state["frontend"][bucket].__setitem__("publicAccessBlock", parsed))
    emit({"bucket": bucket})

elif service == "s3api" and sub == "get-bucket-website":
    bucket = arg("--bucket")
    record(f"s3api get-bucket-website {bucket}")
    website = state.get("frontend", {}).get(bucket, {}).get("website")
    if website is None:
        fail()
    emit(website)

elif service == "s3api" and sub == "delete-bucket-website":
    bucket = arg("--bucket")
    record(f"s3api delete-bucket-website {bucket}")
    mutate(lambda: state["frontend"][bucket].__setitem__("website", None))
    emit({"bucket": bucket})

elif service == "cloudfront" and sub == "get-distribution":
    dist_id = arg("--id")
    record(f"cloudfront get-distribution {dist_id}")
    entry = state.get("cloudfront", {}).get(dist_id)
    if entry is None:
        fail()
    config = entry["config"]
    if "Distribution.DistributionConfig.Origins.Items[0].OriginAccessControlId" in q:
        origins = config.get("Origins", {}).get("Items", [])
        text(origins[0].get("OriginAccessControlId", "") if origins else "")
    elif "Distribution.DistributionConfig" in q:
        emit(config)
    elif "Distribution.Id" in q:
        text(dist_id)
    elif "Distribution.Status" in q:
        # CloudFront deployments are asynchronous: return InProgress for the
        # first two status polls so the migration's bounded wait loop actually
        # polls before Deployed.
        if state.get("deployWaits", 0) < 2:
            state["deployWaits"] = state.get("deployWaits", 0) + 1
            text("InProgress")
        else:
            text(entry.get("status", "Deployed"))
    else:
        emit({"Distribution": {"Id": dist_id, "Status": entry.get("status", "Deployed"), "DistributionConfig": config}})

elif service == "cloudfront" and sub == "get-distribution-config":
    dist_id = arg("--id")
    record(f"cloudfront get-distribution-config {dist_id}")
    entry = state.get("cloudfront", {}).get(dist_id)
    if entry is None:
        fail()
    if "ETag" in q:
        text(entry.get("etag", "E0000000"))
    else:
        emit(entry["config"])

elif service == "cloudfront" and sub == "list-origin-access-controls":
    name = between_backticks(q)
    record(f"cloudfront list-origin-access-controls {name}")
    if name and state.get("oac", {}).get("name") == name:
        text(state["oac"]["id"])
    else:
        text("")

elif service == "cloudfront" and sub == "create-origin-access-control":
    record("cloudfront create-origin-access-control")
    mutate(lambda: state.update({"oac": {"id": "OAC-1A2B3C4D", "name": "onlineshop-frontend-oac"}}))
    text("OAC-1A2B3C4D")

elif service == "cloudfront" and sub == "get-origin-access-control":
    oac_id = arg("--id")
    record(f"cloudfront get-origin-access-control {oac_id}")
    if state.get("oac", {}).get("id") == oac_id:
        emit({"OriginAccessControl": {"Id": oac_id}})
    else:
        fail()

elif service == "cloudfront" and sub == "update-distribution":
    dist_id = arg("--id")
    record(f"cloudfront update-distribution {dist_id}")
    raw = read_file("--distribution-config")
    mutate(lambda: state["cloudfront"][dist_id].__setitem__("config", json.loads(raw)))
    emit({"Distribution": {"Id": dist_id}})

elif service == "cloudtrail" and sub == "describe-trails":
    record("cloudtrail describe-trails")
    if "trailList" in q:
        emit(state.get("cloudtrail", {}).get("trails", []))
    else:
        emit({"trailList": state.get("cloudtrail", {}).get("trails", [])})

elif service == "cloudtrail" and sub == "get-trail-status":
    name = arg("--name")
    record(f"cloudtrail get-trail-status {name}")
    emit(state.get("cloudtrail", {}).get("statuses", {}).get(name, {}))

elif service == "cloudtrail" and sub == "get-event-selectors":
    name = arg("--trail-name")
    record(f"cloudtrail get-event-selectors {name}")
    if "EventSelectors" in q:
        emit(state.get("cloudtrail", {}).get("selectors", {}).get(name, []))
    else:
        emit({"EventSelectors": state.get("cloudtrail", {}).get("selectors", {}).get(name, [])})

else:
    record(f"unhandled {service} {sub}")
    fail()

persist()
PY
  chmod +x "$TMP/bin/aws"
}

# Build the stateful stub state file. `mode` is ok | prod-drift.
stub_state() {
  local mode="${1:-ok}"
  python3 - "$mode" "$TMP" <<'PY'
import json
import os
import sys

mode, tmp = sys.argv[1], sys.argv[2]
dist_ok = json.load(open(os.path.join(tmp, "dist-ok.json"), encoding="utf-8"))
policy_ok = json.load(open(os.path.join(tmp, "policy-ok.json"), encoding="utf-8"))
pab_ok = json.load(open(os.path.join(tmp, "pab-ok.json"), encoding="utf-8"))["PublicAccessBlockConfiguration"]

# The helper script writes the fixtures into $TMP via env-free paths below.
state = {
    "identity": "799111666795",
    "vpcs": ["vpc-06eeb0bc47ecdbd61", "vpc-0e9b2c6911cf3d4e0"],
    "subnetVpc": {
        "subnet-03b318e59490a891a": "vpc-06eeb0bc47ecdbd61",
        "subnet-041e4cf18bfce06f8": "vpc-06eeb0bc47ecdbd61",
        "subnet-0a009040ef6bce7cc": "vpc-06eeb0bc47ecdbd61",
        "subnet-04f5da5a8cf1b1350": "vpc-0e9b2c6911cf3d4e0",
        "subnet-06b823d8d6b24333b": "vpc-0e9b2c6911cf3d4e0",
    },
    "sgVpc": {
        "sg-04ba95188d8374d96": "vpc-06eeb0bc47ecdbd61",
        "sg-0b209104a6b15b157": "vpc-06eeb0bc47ecdbd61",
        "sg-0b5427a6a3bf31c29": "vpc-06eeb0bc47ecdbd61",
        "sg-08c5d1008d1ce54ae": "vpc-0e9b2c6911cf3d4e0",
        "sg-0e4c072113dd8d1e9": "vpc-0e9b2c6911cf3d4e0",
        "sg-0edd7fa1813d03018": "vpc-0e9b2c6911cf3d4e0",
    },
    "clusters": {"onlineshop-cluster": "ACTIVE", "onlineshop-staging-cluster": "ACTIVE"},
    "services": {
        "onlineshop-cluster": {
            "onlineshop-auth": {"status": "ACTIVE", "namespace": "onlineshop.local"},
            "onlineshop-items": {"status": "ACTIVE", "namespace": "onlineshop.local"},
            "onlineshop-api-gateway": {"status": "ACTIVE", "namespace": "onlineshop.local"},
        },
        "onlineshop-staging-cluster": {
            "onlineshop-auth-staging": {"status": "ACTIVE", "namespace": "staging.onlineshop.local"},
            "onlineshop-items-staging": {"status": "ACTIVE", "namespace": "staging.onlineshop.local"},
            "onlineshop-api-gateway-staging": {"status": "ACTIVE", "namespace": "staging.onlineshop.local"},
        },
    },
    "serviceArns": {
        "onlineshop-cluster": [
            "arn:aws:ecs:eu-north-1:799111666795:service/onlineshop-cluster/onlineshop-auth",
            "arn:aws:ecs:eu-north-1:799111666795:service/onlineshop-cluster/onlineshop-items",
            "arn:aws:ecs:eu-north-1:799111666795:service/onlineshop-cluster/onlineshop-api-gateway",
        ]
    },
    "dbInstances": {"onlineshop-postgres-db": "available", "onlineshop-staging-postgres": "available"},
    "dbInstancesPrivate": {"onlineshop-postgres-db": True, "onlineshop-staging-postgres": True},
    "dbSubnetGroups": {
        "default-vpc-06eeb0bc47ecdbd61": "vpc-06eeb0bc47ecdbd61",
        "onlineshop-staging-db-subnets": "vpc-0e9b2c6911cf3d4e0",
    },
    "targetGroups": {
        "arn:aws:elasticloadbalancing:eu-north-1:799111666795:targetgroup/onlineshop-gateway-tg/29ba79a624079a04": "arn:aws:elasticloadbalancing:eu-north-1:799111666795:targetgroup/onlineshop-gateway-tg/29ba79a624079a04",
        "arn:aws:elasticloadbalancing:eu-north-1:799111666795:targetgroup/onlineshop-staging-tg-v2/8a9b0471c381e60b": "arn:aws:elasticloadbalancing:eu-north-1:799111666795:targetgroup/onlineshop-staging-tg-v2/8a9b0471c381e60b",
    },
    "loadBalancers": {
        "onlineshop-alb": "arn:aws:elasticloadbalancing:eu-north-1:799111666795:loadbalancer/app/onlineshop-alb/1111111111111111",
        "onlineshop-staging-v2-alb": "arn:aws:elasticloadbalancing:eu-north-1:799111666795:loadbalancer/app/onlineshop-staging-v2-alb/2222222222222222",
    },
    "namespaces": ["onlineshop.local", "staging.onlineshop.local"],
    "secrets": {
        "onlineshop/auth/db": "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/auth/db-umtxh1",
        "onlineshop/items/db": "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/items/db-9xzp3",
        "onlineshop/rds/master": "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/rds/master-abcde",
        "onlineshop/auth/db-staging": "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/auth/db-staging-aaaaa",
        "onlineshop/items/db-staging": "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/items/db-staging-bbbbb",
    },
    "logGroups": [
        "/ecs/onlineshop-auth", "/ecs/onlineshop-items", "/ecs/onlineshop-api-gateway",
        "/ecs/onlineshop-auth-staging", "/ecs/onlineshop-items-staging", "/ecs/onlineshop-api-gateway-staging",
    ],
    "roles": {"ecsTaskExecutionRole": "arn:aws:iam::799111666795:role/ecsTaskExecutionRole"},
    "ecrRepositories": ["onlineshop-auth", "onlineshop-items", "onlineshop-api-gateway"],
    "frontend": {
        "onlineshop-frontend-799111666795": {
            "exists": True,
            "distributionId": "EPS8MI3FV3B7X",
            "distributionConfig": dist_ok,
            "bucketPolicy": policy_ok,
            "publicAccessBlock": pab_ok,
            "website": None,
            "oacId": "OAC-1A2B3C4D",
        }
    },
    "cloudfront": {
        "EPS8MI3FV3B7X": {
            "config": dist_ok,
            "etag": "ETAG-1234",
            "status": "Deployed",
        }
    },
    "deployWaits": 0,
    "oac": {"id": "OAC-1A2B3C4D", "name": "onlineshop-frontend-oac"},
    "cloudtrail": {
        "trails": [{"Name": "onlineshop-cloudtrail", "S3BucketName": "onlineshop-cloudtrail", "IsMultiRegionTrail": True}],
        "statuses": {"onlineshop-cloudtrail": {"IsLogging": True, "LatestDeliveryTime": "2026-08-04T12:00:00Z"}},
        "selectors": {"onlineshop-cloudtrail": [{"IncludeManagementEvents": True, "ReadWriteType": "All"}]},
    },
}

if mode == "prod-drift":
    state["vpcs"].remove("vpc-06eeb0bc47ecdbd61")

with open(os.path.join(tmp, "state.json"), "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
  : > "$TMP/calls.txt"
}

write_stub_clis

# Copy the OAC/CloudTrail fixtures into $TMP for the stub-state builder.
cp "$FIXTURES/oac/dist-ok.json" "$FIXTURES/oac/policy-ok.json" "$FIXTURES/oac/pab-ok.json" "$TMP/"

export PATH="$TMP/bin:$PATH"
export STUB_STATE="$TMP/state.json"
export STUB_CALLS="$TMP/calls.txt"

# ---------------------------------------------------------------------------
echo "[ 6/11] inventory-production.sh with stateful AWS stubs"
stub_state ok
assert_success bash "$SCRIPTS/inventory-production.sh" --json
# Read-only: no mutating calls may be issued.
if grep -Eq ' (put|create|update|delete|register|run|apply)-' "$TMP/calls.txt"; then
  fail "inventory-production.sh must be read-only; got a mutating call: $(grep -E ' (put|create|update|delete|register|run|apply)-' "$TMP/calls.txt" | head -1)"
fi
grep -q "sts get-caller-identity" "$TMP/calls.txt" || fail "inventory-production.sh must preflight identity"
# Drift (missing VPC) fails closed.
stub_state prod-drift
assert_failure bash "$SCRIPTS/inventory-production.sh"
# An AWS read failure (auth/throttle/network) fails closed as a READ ERROR,
# never disguised as drift or a missing resource.
stub_state ok
: > "$TMP/calls.txt"
OUT=$(env STUB_API_ERROR=1 bash "$SCRIPTS/inventory-production.sh" --json 2>&1) \
  && fail "inventory-production.sh must fail closed on an AWS read error"
assert_contains "$OUT" "OBSERVED_READ_ERROR"
# A missing frontend bucket fails the inventory (frontend delivery is part of
# the identifier contract, not just an informational extra).
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["frontend"]["onlineshop-frontend-799111666795"]["exists"] = False
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$SCRIPTS/inventory-production.sh" --json 2>&1) \
  && fail "inventory-production.sh must fail when the frontend bucket is missing"
assert_contains "$OUT" "INVENTORY_DRIFT"
# Identity mismatch fails closed before any inventory work.
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["identity"] = "000000000000"
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
assert_failure bash "$SCRIPTS/inventory-production.sh"

# ---------------------------------------------------------------------------
echo "[ 7/11] verify-production-staging-separation.sh"
stub_state ok
assert_success bash "$SCRIPTS/verify-production-staging-separation.sh"
OUT=$(bash "$SCRIPTS/verify-production-staging-separation.sh" --json)
assert_contains "$OUT" '"staticValid": true'
assert_contains "$OUT" '"liveValid": true'
# Shared VPC between prod and staging observed topology fails closed.
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["sgVpc"]["sg-08c5d1008d1ce54ae"] = "vpc-06eeb0bc47ecdbd61"  # staging DB SG now in prod VPC
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$SCRIPTS/verify-production-staging-separation.sh" --json 2>&1) && fail "shared topology must fail closed"
assert_contains "$OUT" "SHARED_VPC"
# Static config separation still passes for the real (disjoint) configs.
assert_success env PYTHONPATH="$RELEASE/src" python3 -m release_contract.environments separation \
  --prod "$FIXTURES/environments/prod-config.json" --staging "$FIXTURES/environments/staging-config.json"
assert_failure env PYTHONPATH="$RELEASE/src" python3 -m release_contract.environments separation \
  --prod "$FIXTURES/environments/prod-config.json" --staging "$FIXTURES/environments/staging-shared-vpc.json"

# ---------------------------------------------------------------------------
echo "[ 8/11] verify-frontend-oac.sh + migrate-frontend-oac.sh"
stub_state ok
assert_success bash "$SCRIPTS/verify-frontend-oac.sh"
OUT=$(bash "$SCRIPTS/verify-frontend-oac.sh" --json)
assert_contains "$OUT" '"valid": true'
# Website origin drift fails closed.
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["cloudfront"]["EPS8MI3FV3B7X"]["config"]["Origins"]["Items"][0]["DomainName"] = "onlineshop-frontend-799111666795.s3-website.eu-north-1.amazonaws.com"
state["cloudfront"]["EPS8MI3FV3B7X"]["config"]["Origins"]["Items"][0]["OriginAccessControlId"] = ""
state["frontend"]["onlineshop-frontend-799111666795"]["website"] = {"IndexDocument": {"Suffix": "index.html"}}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$SCRIPTS/verify-frontend-oac.sh" --json 2>&1) && fail "website origin must fail OAC verification"
assert_contains "$OUT" "WEBSITE_ORIGIN"
assert_contains "$OUT" "OAC_MISSING"
# Public-read policy drift fails closed.
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["frontend"]["onlineshop-frontend-799111666795"]["bucketPolicy"] = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::onlineshop-frontend-799111666795/*"}],
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
assert_failure bash "$SCRIPTS/verify-frontend-oac.sh"
# --dry-run mutates nothing.
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["cloudfront"]["EPS8MI3FV3B7X"]["config"]["Origins"]["Items"][0]["DomainName"] = "onlineshop-frontend-799111666795.s3-website.eu-north-1.amazonaws.com"
state["cloudfront"]["EPS8MI3FV3B7X"]["config"]["Origins"]["Items"][0]["OriginAccessControlId"] = ""
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
: > "$TMP/calls.txt"
OUT=$(bash "$SCRIPTS/migrate-frontend-oac.sh" --dry-run)
assert_contains "$OUT" "plan"
if grep -q 'cloudfront update-distribution\|s3api put-\|s3api delete-' "$TMP/calls.txt"; then
  fail "dry-run must not mutate"
fi
# --apply mutates and reads back every step (migration completes).
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["cloudfront"]["EPS8MI3FV3B7X"]["config"]["Origins"]["Items"][0]["DomainName"] = "onlineshop-frontend-799111666795.s3-website.eu-north-1.amazonaws.com"
state["cloudfront"]["EPS8MI3FV3B7X"]["config"]["Origins"]["Items"][0]["OriginAccessControlId"] = ""
state["oac"] = {"id": "", "name": ""}
state["frontend"]["onlineshop-frontend-799111666795"]["bucketPolicy"] = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::onlineshop-frontend-799111666795/*"}],
}
state["frontend"]["onlineshop-frontend-799111666795"]["publicAccessBlock"] = {"BlockPublicAcls": False, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}
state["frontend"]["onlineshop-frontend-799111666795"]["website"] = {"IndexDocument": {"Suffix": "index.html"}}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
assert_success bash "$SCRIPTS/migrate-frontend-oac.sh" --apply
# The apply must have polled the asynchronous CloudFront deployment (status
# InProgress -> Deployed) instead of assuming the update is live immediately.
DEPLOY_WAITS=$(jq -r '.deployWaits // 0' "$TMP/state.json")
[ "$DEPLOY_WAITS" -ge 2 ] || fail "apply must poll the asynchronous CloudFront deployment (deployWaits=$DEPLOY_WAITS)"
# Every mutation was followed by a read-back.
grep -q "cloudfront create-origin-access-control" "$TMP/calls.txt" || fail "apply must create the OAC"
grep -q "cloudfront get-origin-access-control" "$TMP/calls.txt" || fail "apply must read back the OAC"
grep -q "cloudfront update-distribution" "$TMP/calls.txt" || fail "apply must update the distribution"
grep -q "s3api put-bucket-policy" "$TMP/calls.txt" || fail "apply must put the bucket policy"
grep -q "s3api put-public-access-block" "$TMP/calls.txt" || fail "apply must enable the public access block"
grep -q "s3api delete-bucket-website" "$TMP/calls.txt" || fail "apply must remove the website configuration"
# Final state is hardened.
assert_success bash "$SCRIPTS/verify-frontend-oac.sh"
# Read-back drift (mutations ignored) fails closed when starting pre-migration.
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["cloudfront"]["EPS8MI3FV3B7X"]["config"]["Origins"]["Items"][0]["DomainName"] = "onlineshop-frontend-799111666795.s3-website.eu-north-1.amazonaws.com"
state["cloudfront"]["EPS8MI3FV3B7X"]["config"]["Origins"]["Items"][0]["OriginAccessControlId"] = ""
state["oac"] = {"id": "", "name": ""}
state["frontend"]["onlineshop-frontend-799111666795"]["bucketPolicy"] = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::onlineshop-frontend-799111666795/*"}],
}
state["frontend"]["onlineshop-frontend-799111666795"]["publicAccessBlock"] = {"BlockPublicAcls": False, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}
state["frontend"]["onlineshop-frontend-799111666795"]["website"] = {"IndexDocument": {"Suffix": "index.html"}}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
assert_failure env STUB_IGNORE_MUTATIONS=1 bash "$SCRIPTS/migrate-frontend-oac.sh" --apply

# No-lockout preconditions: --apply must refuse to start (before ANY mutation)
# when the current bucket policy would lock out CloudFront after the switch.
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["cloudfront"]["EPS8MI3FV3B7X"]["config"]["Origins"]["Items"][0]["DomainName"] = "onlineshop-frontend-799111666795.s3-website.eu-north-1.amazonaws.com"
state["cloudfront"]["EPS8MI3FV3B7X"]["config"]["Origins"]["Items"][0]["OriginAccessControlId"] = ""
state["frontend"]["onlineshop-frontend-799111666795"]["bucketPolicy"] = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"AWS": "arn:aws:iam::000000000000:role/some-other"},
        "Action": "s3:GetObject",
        "Resource": "arn:aws:s3:::onlineshop-frontend-799111666795/*",
    }],
}
state["oac"] = {"id": "", "name": ""}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
: > "$TMP/calls.txt"
OUT=$(bash "$SCRIPTS/migrate-frontend-oac.sh" --apply 2>&1) && fail "apply must refuse to start when the no-lockout preconditions fail"
assert_contains "$OUT" "PRECONDITION_LOCKOUT"
if grep -q 'cloudfront create-origin-access-control\|cloudfront update-distribution\|s3api put-' "$TMP/calls.txt"; then
  fail "apply must not mutate when the no-lockout preconditions fail"
fi

# ---------------------------------------------------------------------------
echo "[ 9/11] verify-cloudtrail-coverage.sh with stubs"
stub_state ok
assert_success bash "$SCRIPTS/verify-cloudtrail-coverage.sh"
OUT=$(bash "$SCRIPTS/verify-cloudtrail-coverage.sh" --json)
assert_contains "$OUT" '"coveredServices":'
for svc in ecs ecr s3 cloudfront iam secretsmanager; do
  assert_contains "$OUT" "\"$svc\""
done
# A non-multi-region, non-logging trail fails closed.
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["cloudtrail"]["trails"][0]["IsMultiRegionTrail"] = False
state["cloudtrail"]["statuses"]["onlineshop-cloudtrail"]["IsLogging"] = False
state["cloudtrail"]["selectors"]["onlineshop-cloudtrail"][0]["IncludeManagementEvents"] = False
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$SCRIPTS/verify-cloudtrail-coverage.sh" --json 2>&1) && fail "cloudtrail coverage gaps must fail closed"
assert_contains "$OUT" "NOT_MULTI_REGION"
assert_contains "$OUT" "NOT_LOGGING"
assert_contains "$OUT" "MANAGEMENT_EVENTS_DISABLED"
# No trail at all fails closed.
stub_state ok
python3 - "$TMP/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["cloudtrail"]["trails"] = []
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
assert_failure bash "$SCRIPTS/verify-cloudtrail-coverage.sh"

# ---------------------------------------------------------------------------
echo "[10/11] Lifecycle environment guards"
# Production helpers must never reach the clean-staging database paths.
if rg -n 'bootstrap-staging-db\.sh|lc_create_clean_staging_db|lc_delete_staging_db' \
  "$SCRIPTS/resume-playground.sh" "$SCRIPTS/pause-playground.sh"; then
  fail "production lifecycle helpers must not call clean-staging database paths"
fi
# The staging DB helpers themselves must refuse to run under production.
# shellcheck source=../../scripts/config/production.env
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$REPO_ROOT/scripts/config/production.env"
# shellcheck source=../../scripts/lib/lifecycle.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$REPO_ROOT/scripts/lib/lifecycle.sh"
lc_init
# shellcheck disable=SC2034  # LC_ENVIRONMENT is read by the lifecycle helpers
LC_ENVIRONMENT=production
# Guard failure is not enough: the staging-only helpers must fail BEFORE any
# AWS call, even in a conditional-call context. The stateful stub records every
# call, so we assert both the non-zero exit AND an empty mutation log.
assert_no_staging_db_calls() {
  if grep -Eq 'rds (create-db-instance|delete-db-instance|modify-db-instance)|rds wait ' \
    "$TMP/calls.txt"; then
    fail "a staging-only DB helper issued an AWS call after its environment guard failed: $(grep -E 'rds (create|delete|modify|wait)' "$TMP/calls.txt" | head -1)"
  fi
}
: > "$TMP/calls.txt"
assert_failure lc_require_environment staging
assert_failure lc_create_clean_staging_db
assert_failure lc_delete_staging_db
assert_failure lc_staging_db_status
assert_failure lc_staging_master_secret_arn
assert_no_staging_db_calls
# The same must hold in a conditional-call context (`if helper; then ...`),
# which was the pre-existing unsafe assumption.
: > "$TMP/calls.txt"
if lc_create_clean_staging_db; then
  fail "lc_create_clean_staging_db must fail under production (conditional context)"
fi
if lc_delete_staging_db; then
  fail "lc_delete_staging_db must fail under production (conditional context)"
fi
if lc_staging_master_secret_arn; then
  fail "lc_staging_master_secret_arn must fail under production (conditional context)"
fi
assert_no_staging_db_calls
# shellcheck disable=SC2034  # LC_ENVIRONMENT is read by the lifecycle helpers
LC_ENVIRONMENT=staging
assert_success lc_require_environment staging
# The clean-staging DB helpers must still refuse to run under production.
# shellcheck disable=SC2034  # LC_ENVIRONMENT is read by the lifecycle helpers
LC_ENVIRONMENT=production
: > "$TMP/calls.txt"
assert_failure lc_require_environment staging
assert_failure lc_create_clean_staging_db
assert_failure lc_delete_staging_db
assert_failure lc_staging_db_status
assert_failure lc_staging_master_secret_arn
assert_no_staging_db_calls

# ---------------------------------------------------------------------------
echo "[11/11] Static scan: profile/region, mutation read-back, no secrets + lint"
scripts_to_scan=(
  "$SCRIPTS/inventory-production.sh"
  "$SCRIPTS/verify-production-staging-separation.sh"
  "$SCRIPTS/verify-frontend-oac.sh"
  "$SCRIPTS/migrate-frontend-oac.sh"
  "$SCRIPTS/verify-cloudtrail-coverage.sh"
  "$SCRIPTS/lib/identifiers.sh"
  "$RELEASE_BIN/validate-task-definition.sh"
  "$RELEASE_BIN/sanitize-task-definition.sh"
)
for script in "${scripts_to_scan[@]}"; do
  # Identity preflight is mandatory for live entry points (scripts/ top-level);
  # sourced libraries and the file-based release/bin validators need none.
  case "$script" in
    "$REPO_ROOT/scripts"/*.sh)
      if [[ "$script" != *"/lib/"* ]]; then
        grep -q "lc_verify_identity" "$script" || fail "$(basename "$script") must preflight identity (lc_verify_identity)"
      fi
      ;;
  esac
  # Every `aws ` invocation must use the configured AWS_ARGS/profile/region.
  # shellcheck disable=SC2094  # read-only scan; $script is only ever read, never written
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    # Require the char before `aws` to be a delimiter (not [a-zA-Z_]) so an
    # identifier like lc_require_canonical_aws does not false-positive.
    if [[ "$line" =~ (^|[^a-zA-Z_])(aws|"aws")[[:space:]] ]]; then
      # shellcheck disable=SC2016  # literal pattern
      if ! [[ "$line" == *'${LC_AWS[@]}'* || "$line" == *'${aws[@]}'* || "$line" == *'$AWS_ARGS'* || "$line" == *'AWS='* || "$line" == *'aws --profile'* ]]; then
        fail "$(basename "$script") aws call missing profile/region args: $line"
      fi
    fi
  done < "$script"
done
# Secret values must never appear in the scripts.
if rg -n 'PGPASSWORD|password.*[=:]|s3cr3t|plaintext-secret' \
  "${scripts_to_scan[@]}" "$REPO_ROOT/scripts/config/production.env" "$REPO_ROOT/scripts/config/staging.env"; then
  fail "a secret-looking value appears in the 3.5 scripts/config"
fi
# The profile/region are MANDATORY and not overridable: the config files must
# carry exactly the literal values and every script must route every aws call
# through them (enforced at runtime by lc_init / lc_require_canonical_aws).
for cfg in "$REPO_ROOT/scripts/config/production.env" "$REPO_ROOT/scripts/config/staging.env"; do
  grep -q '^LC_PROFILE="dpm-profile"$' "$cfg" || fail "$(basename "$cfg") must force LC_PROFILE=\"dpm-profile\""
  grep -q '^LC_REGION="eu-north-1"$' "$cfg" || fail "$(basename "$cfg") must force LC_REGION=\"eu-north-1\""
done
# The migration tool's decision layer is consulted before any mutation and the
# apply run ends with the full read-back.
grep -q "frontend_hosting" "$SCRIPTS/migrate-frontend-oac.sh" || fail "migrate-frontend-oac.sh must run the no-lockout preconditions decision layer"
grep -q "verify-frontend-oac.sh" "$SCRIPTS/migrate-frontend-oac.sh" || fail "migrate-frontend-oac.sh must end with the full read-back"

echo "--- lint ---"
if command -v ruff >/dev/null 2>&1; then
  (cd "$RELEASE" && ruff check src tests) || fail "ruff lint failed"
else
  echo "ruff not found; skipping (report this)"
fi
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck \
    "$RELEASE_BIN/validate-task-definition.sh" \
    "$RELEASE_BIN/sanitize-task-definition.sh" \
    "$SCRIPTS/lib/identifiers.sh" \
    "$SCRIPTS/inventory-production.sh" \
    "$SCRIPTS/verify-production-staging-separation.sh" \
    "$SCRIPTS/verify-frontend-oac.sh" \
    "$SCRIPTS/migrate-frontend-oac.sh" \
    "$SCRIPTS/verify-cloudtrail-coverage.sh" \
    "${BASH_SOURCE[0]}" || fail "shellcheck failed"
else
  echo "shellcheck not found; skipping (report this)"
fi
if ! git -C "$REPO_ROOT" diff --check >/dev/null 2>&1; then
  fail "git diff --check reports whitespace errors"
fi

echo "Production hardening tests passed."
