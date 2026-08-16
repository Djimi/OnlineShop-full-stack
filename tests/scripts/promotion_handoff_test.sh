#!/usr/bin/env bash
set -euo pipefail

# Stateful offline contract test for the exact promotion handoff:
# candidate -> production snapshot -> real deploy-production.sh -> deployment
# manifest -> official manifest -> real verification -> finalization decision.
# The AWS/GitHub CLIs below are stateful local stubs; no remote resource is
# contacted or mutated.

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE="$REPO_ROOT/plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
VALID="$RELEASE/fixtures/valid"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_contains() {
  local value="$1" expected="$2"
  [[ "$value" == *"$expected"* ]] || fail "expected output to contain: $expected"
}

cp "$VALID/candidate-v1.2.1.json" "$TMP/candidate.json"

echo "[ 1/8] candidate contract"
bash "$RELEASE/bin/validate-manifest.sh" "$TMP/candidate.json" >/dev/null \
  || fail "candidate fixture must be schema-valid"
jq -e '.release.status == "candidate" and ([.components.auth, .components.items, .components.apiGateway] | all(has("taskDefinitionArn") | not))' \
  "$TMP/candidate.json" >/dev/null \
  || fail "candidate must not carry task-definition ARNs"

echo "[ 2/8] create stateful AWS/GitHub stubs"
mkdir -p "$TMP/bin"
python3 - "$TMP/state.json" <<'PY'
import json
import hashlib
import sys

sha = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
index_html = "<html>live-index</html>"
index_sha = hashlib.sha256(index_html.encode()).hexdigest()
registry = "799111666795.dkr.ecr.eu-north-1.amazonaws.com"
digests = {
    "onlineshop-auth": "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0",
    "onlineshop-items": "sha256:1a78d43d2aaaca3cafe41539f5fe211f4ef45152a1fa6af5b85006e93b019452",
    "onlineshop-api-gateway": "sha256:31f9bfdf4d15f460167ba2b7ce9ada23278be1a9f115f06c01e5242901e8948e",
}
source_arns = {
    "onlineshop-auth": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:5",
    "onlineshop-items": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items:4",
    "onlineshop-api-gateway": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-api-gateway:13",
}
new_arns = {
    "onlineshop-auth": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:6",
    "onlineshop-items": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-items:5",
    "onlineshop-api-gateway": "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-api-gateway:14",
}
containers = {
    "onlineshop-auth": ("auth", 9001, "auth-port", "onlineshop-auth-task"),
    "onlineshop-items": ("items", 9000, "items-port", "onlineshop-items-task"),
    "onlineshop-api-gateway": ("api-gateway", 10000, "gateway-port", "onlineshop-api-gateway-task"),
}


def task_definition(service, arn, image):
    container, port, port_name, _ = containers[service]
    return {
        "family": service,
        "taskDefinitionArn": arn,
        "containerDefinitions": [{
            "name": container,
            "image": image,
            "versionConsistency": "enabled",
            "healthCheck": {"command": ["CMD-SHELL", "curl -f http://localhost/actuator/health || exit 1"]},
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {"awslogs-group": f"/ecs/{service}", "awslogs-region": "eu-north-1"},
            },
            "portMappings": [{"name": port_name, "containerPort": port}],
            "stopTimeout": 30,
            "environment": [],
            "secrets": [],
            "essential": True,
        }],
        "cpu": "512",
        "memory": "1024",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "executionRoleArn": "arn:aws:iam::799111666795:role/ecsTaskExecutionRole",
        "taskRoleArn": f"arn:aws:iam::799111666795:role/{service}-task",
    }


task_definitions = {}
services = []
tasks = []
task_arns = {}
for index, service in enumerate(source_arns, start=1):
    container, _port, _port_name, task_id = containers[service]
    image = f"{registry}/{service}:sha-{sha}"
    task_definitions[source_arns[service]] = task_definition(service, source_arns[service], image)
    service_entry = {
        "serviceName": service,
        "taskDefinition": source_arns[service],
        "desiredCount": 1,
        "capacityProviderStrategy": [{"capacityProvider": "FARGATE_SPOT", "weight": 1, "base": 1}],
        "deployments": [{"id": f"ecs-svc/{index}", "rolloutState": "COMPLETED"}],
        "loadBalancers": ([{"targetGroupArn": "arn:aws:elasticloadbalancing:eu-north-1:799111666795:targetgroup/onlineshop-gateway-tg/29ba79a624079a04", "containerName": "api-gateway", "containerPort": 10000}] if service == "onlineshop-api-gateway" else []),
    }
    services.append(service_entry)
    task_arn = f"arn:aws:ecs:eu-north-1:799111666795:task/{task_id}"
    task_arns[service] = [task_arn]
    tasks.append({
        "taskArn": task_arn,
        "taskDefinitionArn": source_arns[service],
        "lastStatus": "RUNNING",
        "containers": [{"name": container, "imageDigest": digests[service]}],
    })

state = {
    "identity": "799111666795",
    "ecs": {
        "services": services,
        "taskArns": task_arns,
        "tasks": tasks,
        "taskDefinitions": task_definitions,
        "newArns": new_arns,
    },
    "frontend": {
        "release.json": {"version": "1.2.1", "sourceSha": sha, "frontendSha256": "b9debb6b25ee6e6e534f7738d27f53f4153dbf361f097336741ae9fb54939ee4"},
        "_releases/v1.2.1/release.json": {"version": "1.2.1", "sourceSha": sha, "frontendSha256": "b9debb6b25ee6e6e534f7738d27f53f4153dbf361f097336741ae9fb54939ee4"},
        "_releases/v1.2.1/index.html": index_html,
        "index.html": index_html,
        "indexSha256": index_sha,
        "checksums": {
            "_releases/v1.2.1/index.html": index_sha,
            "index.html": index_sha,
        },
    },
    "alb": {"targetHealth": [{"target": {"id": "10.0.0.1"}, "targetHealth": {"state": "healthy"}}]},
}
json.dump(state, open(sys.argv[1], "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
: > "$TMP/calls.txt"

cat > "$TMP/bin/aws" <<'PY'
#!/usr/bin/env python3
import base64
import json
import os
import sys

state_path = os.environ["STUB_STATE"]
calls_path = os.environ["STUB_CALLS"]
state = json.load(open(state_path, encoding="utf-8"))
args = sys.argv[1:]
service = args[0] if args else ""
sub = args[1] if len(args) > 1 else ""


def record(value):
    with open(calls_path, "a", encoding="utf-8") as handle:
        handle.write(value + "\n")


def persist():
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def value(flag):
    try:
        return args[args.index(flag) + 1]
    except (ValueError, IndexError):
        return ""


def query():
    return value("--query")


def emit(data):
    print(json.dumps(data, sort_keys=True))


def fail_not_found():
    print("An error occurred (NotFound): not found", file=sys.stderr)
    sys.exit(255)


def requested_services():
    try:
        start = args.index("--services") + 1
    except ValueError:
        return []
    result = []
    for item in args[start:]:
        if item.startswith("--"):
            break
        result.append(item)
    return result


if service == "ecs" and sub == "describe-task-definition":
    arn = value("--task-definition")
    record(f"ecs describe-task-definition {arn}")
    definition = state["ecs"]["taskDefinitions"].get(arn)
    if definition is None:
        fail_not_found()
    if "taskDefinition.{arn:" in query():
        emit({"arn": definition["taskDefinitionArn"], "image": definition["containerDefinitions"][0]["image"]})
    else:
        emit(definition)
elif service == "ecs" and sub == "register-task-definition":
    input_path = value("--cli-input-json")
    if not input_path.startswith("file://"):
        fail_not_found()
    definition = json.load(open(input_path[len("file://"):], encoding="utf-8"))
    family = definition.get("family", "")
    arn = state["ecs"]["newArns"].get(family)
    if not arn:
        fail_not_found()
    definition["taskDefinitionArn"] = arn
    state["ecs"]["taskDefinitions"][arn] = definition
    record(f"ecs register-task-definition {arn}")
    persist()
    print(arn)
elif service == "ecs" and sub == "update-service":
    service_name = value("--service")
    arn = value("--task-definition")
    record(f"ecs update-service {service_name} {arn}")
    service_entry = next(item for item in state["ecs"]["services"] if item["serviceName"] == service_name)
    deployment_id = f"ecs-svc/new-{service_name}"
    service_entry["taskDefinition"] = arn
    service_entry["deployments"] = [{"id": deployment_id, "rolloutState": "COMPLETED"}]
    definition = state["ecs"]["taskDefinitions"][arn]
    digest = definition["containerDefinitions"][0]["image"].split("@", 1)[1]
    for task in state["ecs"]["tasks"]:
        if task["taskArn"] in state["ecs"]["taskArns"].get(service_name, []):
            task["taskDefinitionArn"] = arn
            task["containers"][0]["imageDigest"] = digest
    persist()
    emit({"deployments": deployment_id, "taskDefinition": arn})
elif service == "ecs" and sub == "wait":
    record("ecs wait services-stable")
    print("")
elif service == "ecs" and sub == "describe-services":
    record("ecs describe-services")
    selected = requested_services()
    matched = [item for item in state["ecs"]["services"] if not selected or item["serviceName"] in selected]
    q = query()
    if "services[0].{" in q:
        item = matched[0] if matched else {}
        emit({"deployments": item.get("deployments", [{}])[0].get("id", ""), "rollout": item.get("deployments", [{}])[0].get("rolloutState", ""), "taskDefinition": item.get("taskDefinition", "")})
    elif "services[0]" in q:
        emit(matched[0] if matched else {})
    elif "services[].{serviceName" in q:
        emit([{"serviceName": item["serviceName"], "taskDefinition": item["taskDefinition"]} for item in matched])
    else:
        emit(matched)
elif service == "ecs" and sub == "list-tasks":
    service_name = value("--service-name")
    record(f"ecs list-tasks {service_name}")
    if service_name:
        emit(state["ecs"]["taskArns"].get(service_name, []))
    else:
        emit([arn for arns in state["ecs"]["taskArns"].values() for arn in arns])
elif service == "ecs" and sub == "describe-tasks":
    record("ecs describe-tasks")
    selected = []
    try:
        start = args.index("--tasks") + 1
        for item in args[start:]:
            if item.startswith("--"):
                break
            selected.append(item)
    except ValueError:
        pass
    tasks = [item for item in state["ecs"]["tasks"] if not selected or item["taskArn"] in selected]
    q = query()
    if "containers[0].imageDigest" in q:
        print(tasks[0]["containers"][0]["imageDigest"] if tasks else "")
    elif "tasks[0]" in q:
        emit(tasks[0] if tasks else {})
    else:
        emit(tasks)
elif service == "ecs" and sub == "sts":
    fail_not_found()
elif service == "ecr" and sub == "describe-images":
    record("ecr describe-images")
    print("")
elif service == "s3api" and sub == "get-object":
    key = value("--key")
    output = args[-1]
    record(f"s3api get-object {key}")
    content = state["frontend"].get(key)
    if content is None:
        fail_not_found()
    with open(output, "w", encoding="utf-8") as handle:
        if isinstance(content, str):
            handle.write(content)
        else:
            json.dump(content, handle)
elif service == "s3api" and sub == "head-object":
    key = value("--key")
    checksum_mode = value("--checksum-mode")
    record(f"s3api head-object {key} checksum-mode={checksum_mode or '<missing>'}")
    if checksum_mode != "ENABLED":
        fail_not_found()
    frontend = state["frontend"]
    if "headChecksumSha256" in frontend:
        checksum = frontend["headChecksumSha256"]
    else:
        checksum_hex = frontend.get("checksums", {}).get(key)
        if checksum_hex is None:
            checksum = None
        else:
            try:
                checksum = base64.b64encode(bytes.fromhex(checksum_hex)).decode("ascii")
            except ValueError:
                checksum = checksum_hex
    print(json.dumps({
        "checksum": checksum,
        "checksumType": frontend.get("headChecksumType", "FULL_OBJECT"),
    }))
elif service == "elbv2" and sub == "describe-target-health":
    record("elbv2 describe-target-health")
    emit(state["alb"]["targetHealth"])
else:
    record(f"unhandled {service} {sub}")
    fail_not_found()
PY

cat > "$TMP/bin/gh" <<'PY'
#!/usr/bin/env python3
import json
import sys

url = sys.argv[2] if len(sys.argv) > 2 else ""
if "/git/refs/tags/" in url:
    # The finalization decision must see an absent Git tag and plan a publish.
    print("not found", file=sys.stderr)
    sys.exit(1)
elif "/git/ref/tags/v1.2.1" in url:
    # snapshot-production.sh resolves the exact tag named by the live marker.
    print(json.dumps({
        "ref": "refs/tags/v1.2.1",
        "object": {"sha": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4", "type": "commit"},
    }))
elif "/tags?per_page=100" in url:
    # Keep an unrelated newer tag on a different page. The snapshot must use
    # the live marker's v1.2.1 identity rather than selecting v2.0.0.
    print(json.dumps([
        [
            {"name": "v9.0.0", "commit": {"sha": "9999999999999999999999999999999999999999"}},
            {"name": "v2.0.0", "commit": {"sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}},
        ],
        [
            {"name": "v1.2.1", "commit": {"sha": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"}},
            {"name": "not-a-release", "commit": {"sha": "cccccccccccccccccccccccccccccccccccccccc"}},
        ],
    ]))
    sys.exit(0)
else:
    print("{}")
PY
chmod +x "$TMP/bin/aws" "$TMP/bin/gh"
export PATH="$TMP/bin:$PATH"
export STUB_STATE="$TMP/state.json"
export STUB_CALLS="$TMP/calls.txt"
export GITHUB_REPOSITORY="Djimi/OnlineShop-full-stack"
export GITHUB_TOKEN=t

echo "[ 3/8] real snapshot-production.sh emits the deployment input"
bash "$RELEASE/bin/snapshot-production.sh" \
  --manifest "$TMP/candidate.json" \
  --profile dpm-profile --region eu-north-1 \
  > "$TMP/snapshot.json" 2> "$TMP/snapshot.log" \
  || { cat "$TMP/snapshot.log" >&2; fail "real snapshot-production.sh must succeed through the stateful stub"; }
PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion snapshot \
  --snapshot "$TMP/snapshot.json" --manifest "$TMP/candidate.json" \
  | jq -e '.valid == true' >/dev/null \
  || fail "the real snapshot output must validate against the candidate"
jq -e '.services["onlineshop-auth"].taskDefinitionArn == "arn:aws:ecs:eu-north-1:799111666795:task-definition/onlineshop-auth:5" and .frontend.indexSha256 != ""' \
  "$TMP/snapshot.json" >/dev/null \
  || fail "snapshot output must capture the current ARN and frontend checksum"
jq -e '.officialRelease.version == .frontend.marker.version and .officialRelease.gitTag == ("v" + .frontend.marker.version) and .officialRelease.sourceSha == .frontend.marker.sourceSha' \
  "$TMP/snapshot.json" >/dev/null \
  || fail "snapshot must bind the official identity to the live marker, not a newer tag"
grep -q 'ecs describe-services' "$TMP/calls.txt" || fail "snapshot must read ECS services"
grep -q 's3api head-object index.html checksum-mode=ENABLED' "$TMP/calls.txt" \
  || fail "snapshot must request S3 checksum mode"
if grep -Eq ' (put|create|update|delete|register|run)-' "$TMP/calls.txt"; then
  fail "snapshot-production.sh must be read-only"
fi

echo "[ 4/8] real deploy-production.sh emits new task-definition ARNs"
bash "$RELEASE/bin/deploy-production.sh" \
  --manifest "$TMP/candidate.json" \
  --snapshot "$TMP/snapshot.json" \
  --ecr-registry 799111666795.dkr.ecr.eu-north-1.amazonaws.com \
  --profile dpm-profile --region eu-north-1 \
  > "$TMP/deployment.json" 2> "$TMP/deploy.log" \
  || { cat "$TMP/deploy.log" >&2; fail "real deploy-production.sh must succeed through the stateful stub"; }

declare -A SERVICES=(
  [auth]=onlineshop-auth
  [items]=onlineshop-items
  [apiGateway]=onlineshop-api-gateway
)
for component in "${!SERVICES[@]}"; do
  service="${SERVICES[$component]}"
  source_arn=$(jq -r --arg service "$service" '.services[$service].taskDefinitionArn' "$TMP/snapshot.json")
  final_arn=$(jq -r --arg component "$component" '.components[$component].taskDefinitionArn' "$TMP/deployment.json")
  [ -n "$final_arn" ] && [ "$final_arn" != "null" ] || fail "$component final ARN is missing"
  [ "$final_arn" != "$source_arn" ] || fail "$component reused the snapshot source ARN"
done
grep -q 'ecs register-task-definition' "$TMP/calls.txt" || fail "deploy did not register task definitions"
grep -q 'ecs update-service' "$TMP/calls.txt" || fail "deploy did not update ECS services"
grep -q 'ecs wait services-stable' "$TMP/calls.txt" || fail "deploy did not wait for each service"

echo "[ 5/8] deployment manifest is rendered into an official manifest"
jq --arg now 2026-08-04T14:30:00Z \
  '.release.status = "official" | .release.promotionWorkflow = {runId: 123456790, actor: "djimi", approvedBy: "djimi", approvedAt: $now, deployedAt: $now} ' \
  "$TMP/deployment.json" > "$TMP/official.json"
bash "$RELEASE/bin/validate-manifest.sh" "$TMP/official.json" >/dev/null \
  || fail "official manifest rendered from deployment output must be schema-valid"
jq -e '.release.status == "official" and (.components.auth.taskDefinitionArn | type == "string") and (.components.items.taskDefinitionArn | type == "string") and (.components.apiGateway.taskDefinitionArn | type == "string")' \
  "$TMP/official.json" >/dev/null \
  || fail "official manifest is missing final task-definition ARNs"

echo "[ 6/8] real verify-production.sh accepts the deployed state"
bash "$RELEASE/bin/verify-production.sh" \
  --manifest "$TMP/official.json" \
  --profile dpm-profile --region eu-north-1 \
  > "$TMP/verify.log" 2>&1 \
  || { cat "$TMP/verify.log" >&2; fail "real verify-production.sh must pass after deployment"; }
grep -q 'ecs describe-tasks' "$TMP/calls.txt" || fail "verification did not read running task digests"
grep -q 's3api get-object release.json' "$TMP/calls.txt" || fail "verification did not read the frontend marker"

echo "[ 7/8] real finalize-release.sh reaches the verified decision"
mkdir -p "$TMP/evidence"
FINALIZE=$(PROMOTION_PRODUCTION_VERIFIED=true bash "$RELEASE/bin/finalize-release.sh" \
  --manifest "$TMP/official.json" \
  --evidence-dir "$TMP/evidence" \
  --dry-run \
  --profile dpm-profile --region eu-north-1 2>&1) \
  || { printf '%s\n' "$FINALIZE" >&2; fail "real finalization decision must pass after verification"; }
assert_contains "$FINALIZE" 'finalize action=resume'
assert_contains "$FINALIZE" 'no mutation performed'

echo "[ 8/8] candidate bytes remain immutable across the handoff"
cmp -s "$VALID/candidate-v1.2.1.json" "$TMP/candidate.json" \
  || fail "candidate bytes changed during deployment/rendering"

echo "Promotion handoff stateful test passed."
