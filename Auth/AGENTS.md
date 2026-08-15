# CLAUDE.md — Auth Service

## Quick Reference

| Property   | Value             |
|------------|-------------------|
| Port       | 9001              |
| Language   | Java 25           |
| Framework  | Spring Boot 4.1.0 |
| Database   | PostgreSQL 18     |
| Build Tool | Maven 3.9.12      |

## Commands

```bash
./mvnw clean install        # Build
./mvnw spring-boot:run      # Run
./mvnw spring-boot:run -Dspring-boot.run.arguments="--spring.profiles.active=db-troubleshooting"  # Run with DB diagnostics
./mvnw test                 # Run all tests
```

## Docker Compose Build

Run from the repository root:

```bash
docker compose up -d --build auth-service
```

`Auth/Dockerfile` is a self-contained multi-stage build. It compiles the current source inside Docker, so a host-side `target/*.jar` is not required. Use `docker compose up -d --build` to rebuild and start the complete stack.

## Service Overview

Handles user authentication and session management: registration, login, session token validation, and session lifecycle. No caching layer — all reads hit the database directly.

Base package: `src/main/java/com/onlineshop/auth/`

Standard Spring Boot layered architecture: `controller/` → `service/` → `repository/` with JPA entities (`User`, `Session`), DTOs for request/response, custom exceptions with a global handler, and security configuration. Database init scripts live in `init-db/` (outside `src`).

## API Endpoints

| Method | Endpoint                   | Description            |
|--------|----------------------------|------------------------|
| POST   | `/api/v1/auth/register`    | Register new user      |
| POST   | `/api/v1/auth/login`       | User login             |
| GET    | `/api/v1/auth/validate`    | Validate session token |

**Validate contract:** invalid tokens return HTTP `200` with `valid=false` in the response body, not a 4xx error. A session is valid only when the current time is between its `createdAt` and `expiresAt` values, inclusive.

## CI/CD

The CI pipeline runs `./mvnw verify` before publishing Auth images. Pull-request builds create Docker images without requesting AWS credentials or pushing to ECR.

After a successful `main` build, the immutable Auth image is deployed to the
independent staging ECS cluster. Staging uses its own VPC and RDS database; it
does not use the production cluster or production database. Each staging run
creates an empty RDS instance, applies `init-db/01-schema.sql` and
`init-db/02-seed-data.sql`, verifies the restricted Auth user, runs E2E, and
deletes the instance without retaining state.

Auth images carry OCI/project labels (revision, source, created, title,
`org.onlineshop.component`, `org.onlineshop.build-run`,
`org.onlineshop.producer.*`) used by the Pass 3 candidate-evidence workflow to
make `sha-<full-sha>` publishing idempotent: a rerun reuses the canonical image
instead of rebuilding it, and anything not produced by a trusted successful
`main` push fails closed.

The backend ECR repositories (`onlineshop-*`) are defined with desired state
`IMMUTABLE_WITH_EXCLUSION` (see `release/ecr/immutable-repositories.json`):
`sha-*` and `release-*` tags can never be overwritten, only `main-latest` and
`branch-*` may advance, and `latest` is absent for v1. The live repository
mutation is applied in the consolidated Pass 3 verification pass. The
`release-<version>` tag is minted server-side from the candidate bytes by
`release/bin/promote-image-digest.sh` (never a rebuild).

Pass 3R.1 keeps the Auth handoff scoped to its component: workflow contexts
reach shell through step `env`, event-specific refs/full SHAs are validated,
and the workflow has `contents: read` by default with job-scoped opt-ins. Pull
requests do not bootstrap AWS credentials or publish ECR images. Promotion
accepts the schema-valid candidate without a task-definition ARN, takes the
current Auth task-definition ARN from the production snapshot, and records the
new ARN only in the deployment manifest; an official manifest is rendered
after production verification. The PR/trusted-job split is Pass 3R.2/3R.3,
the purpose-specific role cutover is Pass 3R.9, and live proof is Pass 3R.10.

## Database

- Schema: [init-db/01-schema.sql](./init-db/01-schema.sql)
- Seed data: [init-db/02-seed-data.sql](./init-db/02-seed-data.sql)

## Configuration

Main config: [src/main/resources/application.yml](./src/main/resources/application.yml)

### DB Troubleshooting Profile

Activate with `--spring.profiles.active=db-troubleshooting`. Enables:

- Hibernate statistics and session event logging
- Slow query logging (configurable via `auth.troubleshooting.hibernate.slow-query-threshold-ms`, default unset)
- Verbose Hibernate SQL and statistics log levels
- HikariCP pool diagnostics at `TRACE` (`com.zaxxer.hikari.*`)
- Datasource acquisition timing with pool state (active/idle/waiting/total), sub-ms precision
- Console log format includes log level and logger name

Optional threshold overrides:

```bash
--auth.troubleshooting.hibernate.slow-query-threshold-ms=100
--auth.troubleshooting.datasource.acquire-slow-threshold-ms=2
```

## Multi-Worktree Ports

Create non-main worktrees with the root `scripts/create-worktree.py` command.
It writes the Auth and database host ports used by Docker Compose to the root
`.env`. Automatic translation of those Compose values into host-run Spring
variables is deliberately outside the worktree-creation command.

Repository automation must follow [Script Guidelines](../docs/SCRIPT_GUIDELINES.md).

## AWS CLI Conventions

For AWS CLI commands (infrastructure queries, deployments, etc.), see the root [AGENTS.md](../AGENTS.md) — all AWS commands MUST include `--profile dpm-profile --region eu-north-1`.

Repository pause/resume scripts log UTC timestamped steps, typical durations,
resource-level AWS progress, and actual total runtime. Treat duration values as
operational estimates, not timeout guarantees.

## Pass 3.5 — Production hardening

The production Auth release target is **defined** by the hardening contract (see
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/explanations/PRODUCTION-HARDENING-DECISIONS.md`).
This documents what a production release task definition/service **must** look
like before it may be registered or promoted. **It is not a claim about the
current live production state** — no live task-definition/service mutation has
happened; the live read-back and any required tightening run in the
consolidated Pass 3 verification pass:

- The production task definition **must be** **digest-pinned** (`@sha256:`),
  use `awsvpc`, Fargate, a named Service Connect port (`auth-port`), `awslogs`,
  a container health check, a positive `stopTimeout`, and
  `versionConsistency=enabled`. `release/bin/validate-task-definition.sh` and
  the `release_contract.ecs_config` fixture suite enforce this before any
  registration; it also enforces that the execution role and task role (when
  present) stay distinct.
- Credentials **must be** injected only through `secrets[].valueFrom` with
  **full** `arn:aws:secretsmanager:...` ARNs (`onlineshop/auth/db`); never as
  plaintext in `environment`/`command`. `release/bin/sanitize-task-definition.sh`
  proves a digest-pin changes only the `image` field and keeps secrets in
  `valueFrom`.
- The Auth ECS service **must be** configured with the deployment circuit
  breaker with rollback, `minimumHealthyPercent=100`, `maximumPercent=200`, and
  a capacity-provider strategy. Fargate Spot with desired count 1 is the
  explicit v1 cost tradeoff and is not an HA SLA.
- The explicit non-secret production identifiers (log group `/ecs/onlineshop-auth`,
  secret name, Service Connect namespace, execution role, ECR repository) live
  in `scripts/config/production.env` and are verified read-only by
  `scripts/inventory-production.sh` and
  `scripts/verify-production-staging-separation.sh`.

## Pass 3.7 — Release traceability

The read-only release traceability queries (`release/bin/trace.sh` +
`release_contract.traceability`) resolve the Auth component in both directions:
`commit --sha`/`digest --digest` map the Auth ECR `sha-*`/`release-*` tags and
digests, and `running` reports the **running** Auth container digest from
`tasks[].containers[].imageDigest` (never only the task-definition image URI),
plus the release identity and approver from the matched official manifest.
`audit` cross-checks the Auth manifest digests against ECR and the running
container. The offline gate is `bash tests/scripts/release_traceability_test.sh`;
live lookups against real AWS are deferred to the consolidated verification pass.
