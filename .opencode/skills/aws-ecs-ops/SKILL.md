---
name: aws-ecs-ops
description: Project runbook for operating the OnlineShop AWS stack — ECS Fargate services, Service Connect, private RDS SQL access via scripts/ecs-run-sql.sh, CloudWatch log retrieval, staging/prod deployments. Use whenever running AWS CLI against this project's account 799111666795 (eu-north-1), running SQL against RDS, debugging crashed ECS tasks, creating/updating ECS services or task definitions, or rotating DB credentials. NOT for application code changes.
---

# AWS ECS Ops — OnlineShop Project Runbook

Project-specific procedures that complement the generic `aws-*` skills.
Where they conflict, THIS file wins (it encodes our conventions and scars).

## Mandatory Conventions

- Every AWS CLI command: `--profile dpm-profile --region eu-north-1`. No exceptions.
- Every `create`/`put`/`delete` → immediately `describe`/`get`/`list` to confirm.
- Full gotcha list: `docs/CI_CD_GOTCHAS.md` (ECS + private RDS sections).
  Full narrative: `plans/AUTOMATIC-BUILDS-AND-DEPLOY/AWS_COMMANDS_GUIDE.md` Part D.

## Running SQL Against RDS (private — no public route)

NEVER connect from localhost (hangs). NEVER make RDS public. Use the script:

```bash
# Apply SQL + verify in the SAME run (exit 0 alone is NOT proof):
scripts/ecs-run-sql.sh --database auth_staging \
  --file Auth/init-db/01-schema.sql \
  --verify "SELECT tablename FROM pg_tables WHERE schemaname='public';"

# As a service account (creds from its own secret):
scripts/ecs-run-sql.sh --database items_staging \
  --user items_app_staging --secret onlineshop/items/db-staging \
  --command "SELECT count(*) FROM items;"

# Rotate a password without printing it (update the SM secret FIRST, then):
scripts/ecs-run-sql.sh --database postgres \
  --command "ALTER ROLE auth_app_staging WITH LOGIN PASSWORD :'NEW_PASS';" \
  --extra-secret NEW_PASS=onlineshop/auth/db-staging:password
```

Secrets in Secrets Manager: `onlineshop/rds/master` (dbadmin),
`onlineshop/{auth,items}/db` (prod), `onlineshop/{auth,items}/db-staging` (staging).

## Debugging Crashed ECS Tasks

1. Stopped-task reason: `aws ecs list-tasks --cluster onlineshop-cluster --service-name <svc> --desired-status STOPPED --query 'taskArns[0]'` → `describe-tasks` (guard empty/`None` ARNs).
2. Logs — don't guess stream names, filter by time:
   `aws logs filter-log-events --log-group-name /ecs/<svc> --start-time $(date -d '15 min ago' +%s)000`
   (Stream format if needed: `<prefix>/<container>/<task-id>`. Expect ingestion lag; retry.)
3. `health UNKNOWN` <3 min after start = normal (`startPeriod: 180`). Past that → check stopped tasks, don't keep waiting.
4. Crash loop then `desired:1, running:0` and nothing new starting = ECS stopped retrying → fix root cause, then `update-service --force-new-deployment`.

## Creating/Updating ECS Services & Task Definitions

- Service Connect: `portName` must equal container `portMappings[].name` AND be unique per namespace (`onlineshop.local`). Prod owns `auth-port`/`items-port`/`gateway-port`; staging uses `*-staging-port`.
- Never combine `--launch-type` with `--capacity-provider-strategy`. We use `capacityProvider=FARGATE_SPOT,weight=1`.
- TD with `secrets` requires `executionRoleArn` (`ecsTaskExecutionRole`).
- `secrets[].valueFrom` with `:json-key::` needs the FULL secret ARN — resolve names first (`describe-secret --query ARN`).
- Build TD JSON with python `json.dump` → `--cli-input-json file://`. Never inline multi-line content in CLI params.
- Secrets NEVER in plaintext (`environment`, `command`). Helper TD revisions: deregister AND `delete-task-definitions` after use.

## Session Efficiency Rules

- No blocking poll loops in one bash call (>2 min). Use `aws ecs wait services-stable` or short loops + re-invoke.
- Batch independent reads in one call instead of one API call per turn.
- Same error on a second service = apply the fix you already found; don't re-diagnose.

## Infrastructure Map (eu-north-1, account 799111666795)

| Resource | Value |
|----------|-------|
| Cluster | `onlineshop-cluster` |
| ECS SG / subnets | `sg-0b209104a6b15b157` / `subnet-03b318e59490a891a, subnet-041e4cf18bfce06f8, subnet-0a009040ef6bce7cc` |
| RDS | `onlineshop-postgres-db.cf2gikqaqh9f.eu-north-1.rds.amazonaws.com` (PostgreSQL 18.4, PRIVATE) |
| Prod services | `onlineshop-auth` (9001), `onlineshop-items` (9000), `onlineshop-api-gateway` (10000) |
| Staging services | `onlineshop-{auth,items,api-gateway}-staging`, desired 0, scale up on demand |
| SC namespace | `onlineshop.local` (`auth`, `items`, `gateway` + `-staging` variants) |
| Playground pause/resume | `plans/AUTOMATIC-BUILDS-AND-DEPLOY/scripts/{pause,resume}-playground.sh` |
