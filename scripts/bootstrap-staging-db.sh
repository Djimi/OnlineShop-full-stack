#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
# shellcheck source=config/staging.env
source "$SCRIPT_DIR/config/staging.env"
# shellcheck source=lib/lifecycle.sh
source "$SCRIPT_DIR/lib/lifecycle.sh"

lc_init
lc_require_environment staging
lc_verify_identity
lc_validate_static_resources

# Each SQL runner invocation creates a private one-off Fargate task, waits for
# it, reads CloudWatch output, verifies the mutation, and deletes its temporary
# task-definition revision. Several short invocations are intentional: they
# preserve service ownership and make a failed schema/seed/grant obvious.
BOOTSTRAP_STARTED_AT=$SECONDS

DB_STATUS=$(lc_staging_db_status)
[ "$DB_STATUS" = "available" ] || lc_die "clean staging database must be available before bootstrap"
RDS_HOST=$("${LC_AWS[@]}" rds describe-db-instances \
  --db-instance-identifier "$LC_DB_INSTANCE" --query 'DBInstances[0].Endpoint.Address' --output text)
MASTER_SECRET_ARN=$(lc_staging_master_secret_arn)

export AWS_PROFILE="$LC_PROFILE"
export AWS_REGION="$LC_REGION"
export ONLINESHOP_SQL_CLUSTER="$LC_CLUSTER"
export ONLINESHOP_SQL_SUBNETS
ONLINESHOP_SQL_SUBNETS=$(IFS=,; echo "${LC_ALB_SUBNETS[*]}")
export ONLINESHOP_SQL_SECURITY_GROUP="$LC_ECS_SECURITY_GROUP"
export ONLINESHOP_SQL_RDS_HOST="$RDS_HOST"
export ONLINESHOP_SQL_FAMILY="onlineshop-staging-sql-runner"
export ONLINESHOP_SQL_LOG_GROUP="$LC_SQL_LOG_GROUP"

SQL_RUNNER="$SCRIPT_DIR/ecs-run-sql.sh"

lc_log_step "bootstrap 1/4" "30–90 seconds" "Create restricted roles and empty Auth/Items databases, then verify them."
"$SQL_RUNNER" --database postgres --secret "$MASTER_SECRET_ARN" \
  --file "$SCRIPT_DIR/sql/staging-bootstrap.sql" \
  --extra-secret "AUTH_PASSWORD=$LC_AUTH_SECRET:password" \
  --extra-secret "ITEMS_PASSWORD=$LC_ITEMS_SECRET:password" \
  --verify 'DO $verify$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '\''auth_app_staging'\'') OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '\''items_app_staging'\'') OR NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '\''auth_staging'\'') OR NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '\''items_staging'\'') THEN RAISE EXCEPTION '\''staging role/database verification failed'\''; END IF; END $verify$;'

lc_log_step "bootstrap 2/4" "1–2 minutes" "Apply and verify the Auth-owned schema, deterministic seed, and grants."
"$SQL_RUNNER" --database "$LC_AUTH_DATABASE" --secret "$MASTER_SECRET_ARN" \
  --file "$REPO_ROOT/Auth/init-db/01-schema.sql" \
  --verify 'DO $verify$ BEGIN IF to_regclass('\''public.users'\'') IS NULL OR to_regclass('\''public.sessions'\'') IS NULL THEN RAISE EXCEPTION '\''Auth schema verification failed'\''; END IF; END $verify$;'
"$SQL_RUNNER" --database "$LC_AUTH_DATABASE" --secret "$MASTER_SECRET_ARN" \
  --file "$REPO_ROOT/Auth/init-db/02-seed-data.sql" \
  --verify 'DO $verify$ BEGIN IF (SELECT count(*) FROM users WHERE username = '\''testuser'\'') <> 1 THEN RAISE EXCEPTION '\''Auth seed verification failed'\''; END IF; END $verify$;'
"$SQL_RUNNER" --database "$LC_AUTH_DATABASE" --secret "$MASTER_SECRET_ARN" \
  --file "$SCRIPT_DIR/sql/staging-auth-grants.sql" \
  --verify 'DO $verify$ BEGIN IF NOT has_table_privilege('\''auth_app_staging'\'', '\''users'\'', '\''SELECT,INSERT,UPDATE,DELETE'\'') OR NOT has_sequence_privilege('\''auth_app_staging'\'', '\''users_id_seq'\'', '\''USAGE,SELECT'\'') THEN RAISE EXCEPTION '\''Auth grants verification failed'\''; END IF; END $verify$;'

lc_log_step "bootstrap 3/4" "1–2 minutes" "Apply and verify the Items-owned schema, deterministic seed, and grants."
"$SQL_RUNNER" --database "$LC_ITEMS_DATABASE" --secret "$MASTER_SECRET_ARN" \
  --file "$REPO_ROOT/Items/init-db/01-schema.sql" \
  --verify 'DO $verify$ BEGIN IF to_regclass('\''public.items'\'') IS NULL THEN RAISE EXCEPTION '\''Items schema verification failed'\''; END IF; END $verify$;'
"$SQL_RUNNER" --database "$LC_ITEMS_DATABASE" --secret "$MASTER_SECRET_ARN" \
  --file "$REPO_ROOT/Items/init-db/02-data.sql" \
  --verify 'DO $verify$ BEGIN IF (SELECT count(*) FROM items) <> 5 THEN RAISE EXCEPTION '\''Items seed verification failed'\''; END IF; END $verify$;'
"$SQL_RUNNER" --database "$LC_ITEMS_DATABASE" --secret "$MASTER_SECRET_ARN" \
  --file "$SCRIPT_DIR/sql/staging-items-grants.sql" \
  --verify 'DO $verify$ BEGIN IF NOT has_table_privilege('\''items_app_staging'\'', '\''items'\'', '\''SELECT,INSERT,UPDATE,DELETE'\'') THEN RAISE EXCEPTION '\''Items grants verification failed'\''; END IF; END $verify$;'

lc_log_step "bootstrap 4/4" "30–90 seconds" "Connect as both restricted application users for final read-back."
"$SQL_RUNNER" --database "$LC_AUTH_DATABASE" --user "$LC_AUTH_ROLE" \
  --secret "$LC_AUTH_SECRET" \
  --command "SELECT count(*) AS seeded_auth_users FROM users WHERE username = 'testuser';" \
  --read-only
"$SQL_RUNNER" --database "$LC_ITEMS_DATABASE" --user "$LC_ITEMS_ROLE" \
  --secret "$LC_ITEMS_SECRET" \
  --command 'SELECT count(*) AS seeded_items FROM items;' --read-only

lc_log_complete "Clean staging database bootstrap" "$BOOTSTRAP_STARTED_AT"
