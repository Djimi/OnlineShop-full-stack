# CI/CD Gotchas — Quick Reference

> Read before working on any CI/CD or AWS infra task. Condensed from actual debugging runs (see [plans/AUTOMATIC-BUILDS-AND-DEPLOY/AWS_COMMANDS_GUIDE.md](../plans/AUTOMATIC-BUILDS-AND-DEPLOY/AWS_COMMANDS_GUIDE.md) for the full narrative).

---

## Pre-flight Checks

1. **Identify yourself:** `aws sts get-caller-identity` — always first in any terminal.
2. **Confirm region:** `aws configure set region eu-north-1` or pass `--region eu-north-1` to every command.
3. **Check existing state:** `aws ecr describe-repositories --region eu-north-1`, `aws iam list-roles --query "..."` before creating anything.

---

## AWS Context

| Property | Value |
|----------|-------|
| Account ID | `799111666795` |
| Region | `eu-north-1` |
| OIDC Provider | `arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com` |
| IAM Role | `arn:aws:iam::799111666795:role/github-actions-onlineshop` |
| ECR Registry | `799111666795.dkr.ecr.eu-north-1.amazonaws.com` |
| ECR Naming | `onlineshop-<service>` (NO SLASHES — e.g. `onlineshop-auth`, not `onlineshop-auth/api-gateway`) |

---

## GitHub Actions Workflows

| Gotcha | Why It Happens | Rule |
|--------|---------------|------|
| `workflow_dispatch` invisible on feature branch | GitHub only indexes from the default branch (`main`) | During development, temporarily add `push` trigger. Remove before merging. |
| `github.event.inputs` is `null` on push events | `.inputs` only exists for `workflow_dispatch` events | Guard with `github.event_name == 'workflow_dispatch'` before accessing `.inputs`. Use `github.event_name == 'push'` as a catch-all during development. |
| `Cache export is not supported for the docker driver` | `cache-from: type=gha` requires BuildKit, but the runner's default Docker driver doesn't support it | Always add `docker/setup-buildx-action@v3` before any `docker/build-push-action` that uses `cache-from`/`cache-to`. |
| Java version mismatch: `release version X not supported` | `java-version` in `setup-java` doesn't match `<java.version>` in `pom.xml` or the `FROM` image in `Dockerfile` | Cross-check all three sources of truth before setting the version in the workflow. |
| `Could not find or load main class ...MavenWrapperMain` | `maven-wrapper.jar` was tracked in git and got corrupted by CRLF normalisation | `maven-wrapper.jar` is in `.gitignore` and auto-downloaded. Never track it. |
| Jobs all "skipped" on push | Job `if:` condition only checked `github.event.inputs.service` which is `null` on push | Always include `github.event_name == 'push'` as an OR condition in job guards during development. |

---

## AWS CLI on Windows

PowerShell's `@'...'@` here-strings write UTF-8 with a Byte Order Mark (BOM). AWS IAM (and many other AWS services) reject JSON with a BOM because they expect pure ASCII.

**Wrong:**
```powershell
$json = @'
{"Version":"2012-10-17",...}
'@
$json | Out-File -FilePath trust-policy.json -Encoding utf8
```

**Right:**
```powershell
$json = '{"Version":"2012-10-17",...}'
[System.IO.File]::WriteAllText("trust-policy.json", $json, [System.Text.Encoding]::ASCII)
```

---

## Git Binary Safety

- **Auto-downloadable binaries belong in `.gitignore`, never in git tracking** (e.g., `maven-wrapper.jar`, `node_modules`). They can be corrupted by git's line-ending conversion if accidentally tracked.

---

## AWS ECR

- ECR repositories are **region-scoped** — repos in `eu-north-1` are invisible in `eu-central-1`
- `delete-repository --force` is destructive and irreversible. Always run `describe-images` first to confirm the repo is empty (or you're okay losing the images).
- Use `aws ecr describe-images --repository-name <name> --region eu-north-1 --query "imageDetails[*].imageTags[0]"` to verify pushed images

---

## Verification Pattern

Every mutating AWS command should be immediately verified:

| Mutation | Verification |
|----------|-------------|
| `create-role` | `get-role --role-name <name>` |
| `put-role-policy` | `list-role-policies --role-name <name>` |
| `create-repository` | `describe-repositories --repository-name <name> --region <region>` |
| `delete-repository` | `describe-repositories --region <region>` (confirm it's gone) |
| Apply SQL via `ecs-run-sql.sh` | `--verify` flag in the SAME run (e.g. `--verify "\dt"`) — never trust exit 0 alone |

AWS CLI returns empty output on success for many commands — silence does NOT mean it worked. Verify explicitly.

---

## AWS ECS (Fargate)

Learned during staging provisioning (2026-08-02). Full narrative: [AWS_COMMANDS_GUIDE.md Part D](../plans/AUTOMATIC-BUILDS-AND-DEPLOY/AWS_COMMANDS_GUIDE.md).

| Gotcha | Why It Happens | Rule |
|--------|---------------|------|
| `SC service is already used by ...namespace...` | Service Connect maps `portName` → Cloud Map service name, which must be **unique per namespace**. Prod already uses `auth-port`, `items-port`, `gateway-port` | New environments in the same namespace need unique port names (e.g. `auth-staging-port`) in BOTH the container `portMappings[].name` and the SC config |
| `portName(X) does not refer to any named PortMapping` | SC `portName` must exactly match a `portMappings[].name` in the task definition | Rename the portMapping name in the TD, not just the SC config |
| `Specifying both a launch type and capacity provider strategy is not supported` | `--launch-type` and `--capacity-provider-strategy` are mutually exclusive on `create-service`/`update-service` | Pick one. We use `--capacity-provider-strategy "capacityProvider=FARGATE_SPOT,weight=1"` |
| `you must also specify a value for 'executionRoleArn'` | Container `secrets` (Secrets Manager injection) requires an execution role | Always include `executionRoleArn` when the TD has `secrets` |
| Service stops launching tasks after repeated crashes | ECS gives up retrying a failing deployment; `desired:1, running:0`, rollout "COMPLETED" | Fix the root cause, then `update-service --force-new-deployment` |
| Task health stuck `UNKNOWN` for minutes | Container `healthCheck.startPeriod: 180` = no checks for 3 min. This is NORMAL | Don't wait blindly — check `list-tasks --desired-status STOPPED` for crash loops first |
| `describe-tasks` shows no `logStreamName` | That field isn't reliably populated | Construct it: `<awslogs-stream-prefix>/<container-name>/<task-id>` |
| Logs empty right after task stops | CloudWatch ingestion lag (seconds) | Retry a few times, or use `aws logs filter-log-events --start-time ...` for crashed tasks |
| `startedAt: null` on a stopped task | Container died during provisioning/early startup — NOT proof of image-pull or secrets failure | Check the app logs (`filter-log-events`) before theorizing |
| `taskId length should be one of [32,36]` / `Unexpected number of separators` | An empty/`None` task ARN was passed to `describe-tasks` | Guard: `[ "$TASK_ARN" != "None" ] && [ -n "$TASK_ARN" ]` before describing |
| `Invalid control character` parsing `--container-definitions` | Multi-line strings (SQL, JSON) inline in CLI params | Never inline complex JSON: build with python `json.dump` to a temp file, use `--cli-input-json file://` |
| `The Systems Manager parameter name specified for secret ... is invalid` | `secrets[].valueFrom` with the `:json-key::` suffix requires the **full ARN** (name alone is treated as an SSM parameter) | Resolve names via `describe-secret --query ARN` before building TD JSON |

### ECS anti-patterns

- **Blocking poll loops** (`for i in $(seq 1 48); sleep 10; done`) in a single shell call — they burn session time and risk losing everything to a hard timeout. Prefer `aws ecs wait services-stable`, or short bounded loops (<2 min) and re-invoke.
- **Passwords in task definitions** — a plaintext `PGPASSWORD` env var or a password embedded in `command` is visible to anyone with `ecs:DescribeTaskDefinition`. Always inject via `secrets[].valueFrom`. Deregister **and** `delete-task-definitions` one-off helper revisions after use (deregister alone keeps them describable as INACTIVE).

---

## Private RDS Access

The RDS instance has `PubliclyAccessible: No` — **no route from your machine, ever** (private subnets, no IGW route). Do NOT attempt local `psql` (it hangs until timeout) and do NOT make RDS public.

**Only sanctioned pattern:** a one-off Fargate task in the ECS security group:

```bash
scripts/ecs-run-sql.sh --database <db> --file <schema.sql> --verify "\dt"
```

`scripts/ecs-run-sql.sh` handles: TD JSON via `--cli-input-json file://`, base64 SQL transport (zero quoting bugs), `ON_ERROR_STOP=1`, password injection from Secrets Manager (never plaintext), correct log-stream resolution with ingestion-lag retry, and deregister+delete of its own TD revision. Details: [AWS_COMMANDS_GUIDE.md Part D](../plans/AUTOMATIC-BUILDS-AND-DEPLOY/AWS_COMMANDS_GUIDE.md).

### SQL discipline

- **Verify in the same run** — a schema apply that exits 0 may have applied nothing (escaping bugs silently no-op'd a whole schema once). Always pass `--verify`.
- `\c dbname` psql meta-commands work in `-f` files, but prefer one script run per database — clearer logs, clearer failures.
- `CREATE TABLE IF NOT EXISTS` still errors on real problems (missing FK target), but a malformed file can no-op silently — `--verify` is the only proof.
