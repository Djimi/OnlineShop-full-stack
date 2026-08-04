#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# ecs-run-sql.sh
# Runs SQL against the PRIVATE RDS instance via a one-off Fargate task
# (the only sanctioned way to reach it — RDS has no public access).
#
# Security model:
#   - Passwords are NEVER passed in plaintext. PGPASSWORD is injected by ECS
#     from Secrets Manager (valueFrom). No password appears in the task
#     definition, CLI args, session logs, or this script.
#   - The task definition revision created here is deregistered AND deleted
#     after a successful run, so no residue accumulates.
#
# Usage:
#   scripts/ecs-run-sql.sh --database <db> --file <path.sql> --verify "<sql>"
#   scripts/ecs-run-sql.sh --database <db> --command "<sql>" --verify "<sql>"
#   scripts/ecs-run-sql.sh --database <db> --file <path.sql> --read-only
#   scripts/ecs-run-sql.sh --database <db> --command "<sql>" --read-only
#
# Options:
#   --database DB       Target database (e.g. postgres, auth_staging) [required]
#   --file PATH         SQL file to apply (psql -f, ON_ERROR_STOP=1)
#   --command SQL       Inline SQL (applied via psql -f after base64 decode)
#   --verify SQL        Extra SQL run after the main one; use it to PROVE the
#                       mutation worked (e.g. --verify "\dt"). Required unless
#                       --read-only is set. Every mutation MUST use --verify.
#   --read-only         Declare this run is read-only (SELECT, EXPLAIN, etc.).
#                       Skips the --verify requirement. Mutating without --verify
#                       is prohibited per project rules.
#   --user NAME         DB user [default: dbadmin]
#   --secret ID         Secrets Manager id holding {"username","password"} for
#                       --user [default: onlineshop/rds/master]
#   --extra-secret V=ID:KEY
#                       Inject extra env var V from secret ID json-field KEY
#                       (repeatable). Also passed to psql as -v V="$V", so SQL
#                       can reference it with psql interpolation: :'V'
#                       For rotations: reference the NEW password from its
#                       secret instead of embedding it in SQL.
#   --keep-td           Keep the task-def revision (debugging); prints cleanup hint.
#
# Examples:
#   # Apply schema + prove tables exist
#   scripts/ecs-run-sql.sh --database auth_staging \
#     --file Auth/init-db/01-schema.sql \
#     --verify "SELECT tablename FROM pg_tables WHERE schemaname='public';"
#
#   # Rotate a password without ever printing it (update SM secret FIRST):
#   scripts/ecs-run-sql.sh --database postgres \
#     --command "ALTER ROLE auth_app_staging WITH LOGIN PASSWORD :'NEW_PASS';" \
#     --extra-secret NEW_PASS=onlineshop/auth/db-staging:password \
#     --verify "SELECT 1 FROM pg_roles WHERE rolname='auth_app_staging';"
#
#   # Run a read-only query:
#   scripts/ecs-run-sql.sh --database auth_staging \
#     --command "SELECT COUNT(*) FROM users;" --read-only
#
# Exit codes: 0 = SQL succeeded (exit 0 from psql); 1 = failure.
###############################################################################

PROFILE="${AWS_PROFILE:-dpm-profile}"
REGION="${AWS_REGION:-eu-north-1}"

# Defaults target production. Staging lifecycle scripts override these values,
# keeping this SQL runner reusable without coupling either environment.
CLUSTER="${ONLINESHOP_SQL_CLUSTER:-onlineshop-cluster}"
SUBNETS="${ONLINESHOP_SQL_SUBNETS:-subnet-03b318e59490a891a,subnet-041e4cf18bfce06f8,subnet-0a009040ef6bce7cc}"
SG="${ONLINESHOP_SQL_SECURITY_GROUP:-sg-0b209104a6b15b157}"
RDS_HOST="${ONLINESHOP_SQL_RDS_HOST:-onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com}"
EXEC_ROLE="arn:aws:iam::799111666795:role/ecsTaskExecutionRole"
FAMILY="${ONLINESHOP_SQL_FAMILY:-onlineshop-sql-runner}"
LOG_GROUP="${ONLINESHOP_SQL_LOG_GROUP:-/ecs/onlineshop-sql-runner}"
CONTAINER_NAME="sql"
STREAM_PREFIX="sql"
MASTER_SECRET="onlineshop/rds/master"

# --- Parse args ---------------------------------------------------------------
DB="" SQL_FILE="" SQL_CMD="" VERIFY_SQL="" DB_USER="dbadmin" SECRET_ID="$MASTER_SECRET"
KEEP_TD=0
READ_ONLY=0
EXTRA_SECRETS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --database)     DB="$2"; shift 2;;
    --file)         SQL_FILE="$2"; shift 2;;
    --command)      SQL_CMD="$2"; shift 2;;
    --verify)       VERIFY_SQL="$2"; shift 2;;
    --user)         DB_USER="$2"; shift 2;;
    --secret)       SECRET_ID="$2"; shift 2;;
    --extra-secret) EXTRA_SECRETS+=("$2"); shift 2;;
    --keep-td)      KEEP_TD=1; shift;;
    --read-only)    READ_ONLY=1; shift;;
    -h|--help)      sed -n '1,60p' "$0"; exit 0;;
    *) echo "Unknown option: $1" >&2; exit 1;;
  esac
done

[ -n "$DB" ] || { echo "ERROR: --database is required" >&2; exit 1; }
[[ "$DB" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || { echo "ERROR: unsafe database name" >&2; exit 1; }
[[ "$DB_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || { echo "ERROR: unsafe database user" >&2; exit 1; }
if [ -n "$SQL_FILE" ] && [ -n "$SQL_CMD" ]; then
  echo "ERROR: use --file OR --command, not both" >&2; exit 1
fi
if [ -z "$SQL_FILE" ] && [ -z "$SQL_CMD" ]; then
  echo "ERROR: one of --file/--command is required" >&2; exit 1
fi
if [ -n "$SQL_FILE" ] && [ ! -f "$SQL_FILE" ]; then
  echo "ERROR: file not found: $SQL_FILE" >&2; exit 1
fi

if [ "$READ_ONLY" = "0" ] && [ -z "$VERIFY_SQL" ]; then
  echo "ERROR: --verify is required for mutating SQL runs. Use --read-only for" >&2
  echo "       SELECT-only queries, or provide --verify to confirm the mutation." >&2
  echo "       AGENTS.md: every SQL mutation needs a read-back --verify." >&2
  exit 1
fi

AWS="aws --profile $PROFILE --region $REGION"

# --- Resolve secret ARN (name -> full ARN with suffix) ------------------------
SECRET_ARN=$($AWS secretsmanager describe-secret --secret-id "$SECRET_ID" \
  --query 'ARN' --output text)

# ECS valueFrom needs FULL ARNs when using the ":json-key::" suffix form —
# resolve any names in --extra-secret the same way.
RESOLVED_EXTRA=()
for pair in "${EXTRA_SECRETS[@]:-}"; do
  [ -n "$pair" ] || continue
  var="${pair%%=*}"
  [[ "$var" =~ ^[A-Z_][A-Z0-9_]*$ ]] || { echo "ERROR: unsafe extra-secret variable: $var" >&2; exit 1; }
  ref="${pair#*=}"
  sid="${ref%:*}"
  key="${ref##*:}"
  sid_arn=$($AWS secretsmanager describe-secret --secret-id "$sid" \
    --query 'ARN' --output text)
  RESOLVED_EXTRA+=("$var=$sid_arn:$key")
done

# --- Build the container command ----------------------------------------------
# SQL is base64-encoded: survives JSON/shell embedding with zero escaping issues.
SQL_B64=$( { [ -n "$SQL_FILE" ] && cat "$SQL_FILE" || printf '%s' "$SQL_CMD"; } | base64 -w0 )
# Extra-secret vars are also exposed to psql as -v NAME="$NAME" (expanded by the
# CONTAINER shell at runtime), so SQL can use psql interpolation :'NAME'.
PSQL_VARS=""
for pair in "${EXTRA_SECRETS[@]:-}"; do
  [ -n "$pair" ] || continue
  var="${pair%%=*}"
  PSQL_VARS="$PSQL_VARS -v $var=\"\$$var\""
done
PSQL="psql -h $RDS_HOST -U $DB_USER -d $DB -v ON_ERROR_STOP=1$PSQL_VARS"
CMD="echo '$SQL_B64' | base64 -d > /tmp/q.sql && $PSQL -f /tmp/q.sql && echo '=== SQL_OK ==='"
if [ -n "$VERIFY_SQL" ]; then
  V_B64=$(printf '%s' "$VERIFY_SQL" | base64 -w0)
  CMD="$CMD && echo '$V_B64' | base64 -d > /tmp/v.sql && echo '=== VERIFY ===' && $PSQL -f /tmp/v.sql"
fi

# --- Build container definitions JSON (file-based; no inline JSON quoting) ----
TD_JSON=$(mktemp /tmp/sql-runner-td.XXXXXX.json)
chmod 600 "$TD_JSON"
EXTRA_SECRETS_JSON=$(printf '%s\n' "${RESOLVED_EXTRA[@]:-}" | sed '/^$/d' || true)
python3 - "$TD_JSON" "$CMD" "$SECRET_ARN" "$EXTRA_SECRETS_JSON" "$FAMILY" "$EXEC_ROLE" "$LOG_GROUP" "$REGION" <<'PYEOF'
import json, sys

td_path, cmd, secret_arn, extra_raw, family, execution_role, log_group, region = sys.argv[1:]

secrets = [{"name": "PGPASSWORD", "valueFrom": f"{secret_arn}:password::"}]
for line in extra_raw.splitlines():
    line = line.strip()
    if not line:
        continue
    var, ref = line.split("=", 1)
    sid, key = ref.rsplit(":", 1)
    secrets.append({"name": var, "valueFrom": f"{sid}:{key}::"})

td = {
    "family": family,
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "cpu": "256",
    "memory": "512",
    "executionRoleArn": execution_role,
    "containerDefinitions": [{
        "name": "sql",
        "image": "postgres:18-alpine",
        "essential": True,
        "command": ["sh", "-c", cmd],
        "secrets": secrets,
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group,
                "awslogs-region": region,
                "awslogs-stream-prefix": "sql"
            }
        }
    }]
}
with open(td_path, "w") as f:
    json.dump(td, f)
print("TD JSON built")
PYEOF

# --- Register task definition -------------------------------------------------
if ! $AWS logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" \
  --query 'logGroups[?logGroupName==`'"$LOG_GROUP"'`].logGroupName' --output text | \
  grep -Fxq "$LOG_GROUP"; then
  $AWS logs create-log-group --log-group-name "$LOG_GROUP"
  $AWS logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" \
    --query 'logGroups[?logGroupName==`'"$LOG_GROUP"'`].logGroupName' --output text | \
    grep -Fxq "$LOG_GROUP" || { echo "ERROR: log group creation was not verified" >&2; exit 1; }
fi

TD_ARN=$($AWS ecs register-task-definition --cli-input-json "file://$TD_JSON" \
  --query 'taskDefinition.taskDefinitionArn' --output text)
rm -f "$TD_JSON"
$AWS ecs describe-task-definition --task-definition "$TD_ARN" \
  --query 'taskDefinition.taskDefinitionArn' --output text | grep -Fxq "$TD_ARN" || {
  echo "ERROR: task definition registration was not verified" >&2; exit 1;
}
echo "Registered: $TD_ARN"

cleanup_td() {
  local status
  $AWS ecs deregister-task-definition --task-definition "$TD_ARN" >/dev/null
  status=$($AWS ecs describe-task-definition --task-definition "$TD_ARN" \
    --query 'taskDefinition.status' --output text)
  [ "$status" = "INACTIVE" ] || { echo "ERROR: task definition deregistration was not verified" >&2; return 1; }
  $AWS ecs delete-task-definitions --task-definitions "$TD_ARN" >/dev/null
  status=$($AWS ecs describe-task-definition --task-definition "$TD_ARN" \
    --query 'taskDefinition.status' --output text 2>/dev/null || true)
  [ -z "$status" ] || [ "$status" = "DELETE_IN_PROGRESS" ] || {
    echo "ERROR: task definition deletion was not verified (status: $status)" >&2; return 1;
  }
}

cleanup_on_exit() {
  local exit_code=$?
  if [ "$KEEP_TD" = "0" ]; then
    cleanup_td || exit_code=1
  else
    echo "--keep-td set: revision kept for explicit debugging: $TD_ARN" >&2
  fi
  return "$exit_code"
}
trap cleanup_on_exit EXIT

# --- Run one-off task -----------------------------------------------------------
TASK_ARN=$($AWS ecs run-task \
  --cluster "$CLUSTER" --task-definition "$TD_ARN" --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}" \
  --query 'tasks[0].taskArn' --output text)
TASK_ID="${TASK_ARN##*/}"
echo "Task: $TASK_ID"

if ! $AWS ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN"; then
  STATUS=$($AWS ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
    --query 'tasks[0].lastStatus' --output text)
  echo "ERROR: task did not stop before the AWS waiter timed out (last: $STATUS)" >&2
  echo "Inspect: aws ecs describe-tasks --profile $PROFILE --region $REGION --cluster $CLUSTER --tasks $TASK_ARN" >&2
  exit 1
fi

EXIT_CODE=$($AWS ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[?name==`'"$CONTAINER_NAME"'`]|[0].exitCode' --output text)
echo "Container exit code: $EXIT_CODE"

# --- Fetch logs (stream = <prefix>/<container>/<task-id>; retry for CW lag) ---
STREAM="$STREAM_PREFIX/$CONTAINER_NAME/$TASK_ID"
for attempt in 1 2 3 4; do
  LOGS=$($AWS logs get-log-events --log-group-name "$LOG_GROUP" \
    --log-stream-name "$STREAM" --query 'events[*].message' --output text 2>/dev/null || true)
  [ -n "$LOGS" ] && break
  sleep 5
done
echo "--- task logs ---"
[ -n "$LOGS" ] && echo "$LOGS" || echo "(no logs captured)"
echo "-----------------"

if [ "$EXIT_CODE" != "0" ]; then
  echo "ERROR: SQL task failed (exit $EXIT_CODE). Logs above are preserved in $LOG_GROUP." >&2
  exit 1
fi

if [ "$KEEP_TD" = "0" ]; then
  cleanup_td
  trap - EXIT
  echo "TD revision deregistered + deleted (clean)."
else
  echo "--keep-td set: revision kept: $TD_ARN"
  trap - EXIT
fi
echo "DONE"
