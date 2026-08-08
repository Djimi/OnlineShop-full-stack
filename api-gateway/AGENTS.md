# API Gateway - Service Documentation

## Overview

The API Gateway is the entry point to OnlineShop services. It routes traffic, enforces auth, and adds resilience.

| Property   | Value                                  |
|------------|----------------------------------------|
| Port       | 10000                                  |
| Tech Stack | Spring Cloud Gateway, Redis, Caffeine  |
| Location   | `/api-gateway`                         |
| Database   | None (uses Redis for caching/limits)   |

## Docker Compose Build

Run from the repository root:

```bash
docker compose up -d --build api-gateway
```

`api-gateway/Dockerfile` is a self-contained multi-stage build. It compiles the current source inside Docker, so a host-side `target/*.jar` is not required. Use `docker compose up -d --build` to rebuild and start the complete stack.

## CI/CD

The CI pipeline runs `./mvnw verify` before publishing the API Gateway image. Pull-request builds create Docker images without requesting AWS credentials or pushing to ECR.

Pull requests also run blocking E2E tests against an isolated Docker Compose
candidate. Successful `main` builds deploy to the independent staging VPC and
run the same E2E suite through the staging ALB and a freshly bootstrapped
database. Failure diagnostics are uploaded before staging is always torn down.

The API Gateway image carries OCI/project labels (revision, source, created,
title, `org.onlineshop.component`, `org.onlineshop.build-run`,
`org.onlineshop.producer.*`) used by the Pass 3 candidate-evidence workflow to
make `sha-<full-sha>` publishing idempotent and to verify the canonical
producer set across all three backends.

The backend ECR repositories (`onlineshop-*`) are defined with desired state
`IMMUTABLE_WITH_EXCLUSION` (see `release/ecr/immutable-repositories.json`):
`sha-*` and `release-*` tags can never be overwritten, only `main-latest` and
`branch-*` may advance, and `latest` is absent for v1. The live repository
mutation is applied in the consolidated Pass 3 verification pass. The
`release-<version>` tag is minted server-side from the candidate bytes by
`release/bin/promote-image-digest.sh` (never a rebuild).

## Responsibilities

- **Routing**: `/auth/**` → Auth (rewritten to `/api/v1/auth/**`), `/items/**` → Items (rewritten to `/api/v1/items/**`).
- **Authentication**: Bearer token required for `/items/**`.
- **Public info endpoint**: `/api/product-info` → static product info (no auth, no downstream call).
- **Token caching**: L1 Caffeine + L2 Redis for validation results.
- **Rate limiting**: Bucket4j + Redis.
- **Resilience/observability**: Resilience4j + Micrometer/Prometheus.

## Contracts (Examples)

- Public: `/auth/**`
- Public: `/api/product-info` (static info endpoint; unversioned by design)
- Protected: `/items/**`

Example:
```bash
curl -H "Authorization: Bearer <token>" \
  http://localhost:10000/items
```

## Authentication Flow (Example)

```
1. Request hits gateway
2. /auth/** → forward without auth
3. /items/** → require Authorization header
4. Validate token: L1 → L2 → Auth service
5. On success → add X-User-Id, X-Username headers → forward
6. On failure → 401 Unauthorized
```

## Token Validation Caching

- **L1 (Caffeine)**: Nanosecond local hits.
- **L2 (Redis)**: Shared cache across instances.

Example metric (tag-based):
```
gateway.cache.operations.total{layer="l1", service="auth", result="hit"}
```

## Technology Stack

- **Spring Boot 4.X.X** with Java 25
- **Spring Cloud Gateway (Web MVC)** - Non-reactive, supports virtual threads
- **RestClient** - Modern synchronous HTTP client
- **Caffeine** - L1 local in-memory cache
- **Redis** - L2 distributed cache + rate limiting store
- **Bucket4j** - Rate limiting
- **Resilience4j** - Circuit breakers, retries, timeouts
- **Micrometer** - Metrics collection with Prometheus export
- **Lombok** - Compile-time annotations (provided scope)

## Project Structure

```
api-gateway/
├── src/main/java/com/onlineshop/gateway/
│   ├── cache/              # Token caching (L1/L2)
│   ├── config/             # Spring configuration
│   ├── dto/                # Data transfer objects
│   ├── exception/          # Custom exceptions
│   ├── filter/             # Security and validation filters
│   ├── metrics/            # Metrics instrumentation
│   ├── ratelimit/          # Rate limiting filter
│   ├── service/            # Business logic services
│   └── validation/         # Token sanitization
└── src/test/java/          # Unit and integration tests
```

## Resilience Notes

- Service boots even if Redis is down (cache is optional).
- Cached tokens continue to work during Auth outages; uncached tokens fail fast.
- Rate limiting can be disabled via `gateway.ratelimit.enabled=false` (useful for tests).

## Development Notes

- In this workspace, VS Code Spring Boot live-information auto-JMX is disabled via `.vscode/settings.json` so IDE launches do not compete with the gateway's HTTP port `10000`.

## CORS Configuration

### AWS Deployment

For production deployment behind CloudFront (or any non-localhost origin), CORS must allow the frontend origin:

- **`CorsConfig.java`**: `allowedOrigins("*")` with `allowCredentials(false)` for CloudFront integration
- **`application.yml`**: `allowed-origins: ["*"]` with `allow-credentials: false`

**Note:** `allow-credentials: true` is incompatible with `allowed-origins: "*"` in browsers. Since this service uses Bearer tokens (not cookies), `allow-credentials: false` is safe.

For tighter security in production, replace `"*"` with the explicit CloudFront domain (e.g., `https://d2akuwv5pxgajc.cloudfront.net`).

## AWS CLI Conventions

For AWS CLI commands (infrastructure queries, deployments, etc.), see the root [AGENTS.md](../AGENTS.md) — all AWS commands MUST include `--profile dpm-profile --region eu-north-1`.

Repository pause/resume scripts log UTC timestamped steps, typical durations,
resource-level AWS progress, and actual total runtime. Treat duration values as
operational estimates, not timeout guarantees.

## Host-Run Dev Mode (Multi-Worktree)

In a non-main worktree, run the gateway on the host with `--exports` to pick up worktree-specific ports:

```bash
source <(scripts/dev-env.sh --exports)
./mvnw spring-boot:run
```

## Pass 3.5 — Production hardening

The production API gateway release target is **defined** by the hardening
contract (see
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/explanations/PRODUCTION-HARDENING-DECISIONS.md`).
This documents what a production release task definition/service **must** look
like before it may be registered or promoted. **It is not a claim about the
current live production state** — no live task-definition/service mutation has
happened; the live read-back and any required tightening run in the
consolidated Pass 3 verification pass:

- The production task definition **must be** **digest-pinned** (`@sha256:`),
  use `awsvpc`, Fargate, a named Service Connect port (`gateway-port`),
  `awslogs`, a container health check, a positive `stopTimeout`, and
  `versionConsistency=enabled`. `release/bin/validate-task-definition.sh` and
  the `release_contract.ecs_config` fixture suite enforce this before any
  registration; it also enforces that the execution role and task role (when
  present) stay distinct.
- The gateway service **must be** wired to the ALB target group and configured
  with the deployment circuit breaker with rollback,
  `minimumHealthyPercent=100`, `maximumPercent=200`, and a capacity-provider
  strategy. Fargate Spot with desired count 1 is the explicit v1 cost tradeoff
  and is not an HA SLA.
- The explicit non-secret production identifiers (log group
  `/ecs/onlineshop-api-gateway`, Service Connect namespace, ALB/TG, execution
  role, ECR repository) live in `scripts/config/production.env` and are
  verified read-only by `scripts/inventory-production.sh` and
  `scripts/verify-production-staging-separation.sh`.

## Pass 3.7 — Release traceability

The read-only release traceability queries (`release/bin/trace.sh` +
`release_contract.traceability`) resolve the API gateway component in both
directions: `commit --sha`/`digest --digest` map the gateway ECR `sha-*`/
`release-*` tags and digests, and `running` reports the **running** gateway
container digest from `tasks[].containers[].imageDigest` (never only the
task-definition image URI), plus the release identity and approver from the
matched official manifest. `audit` cross-checks the gateway manifest digests
against ECR and the running container. The offline gate is
`bash tests/scripts/release_traceability_test.sh`; live lookups against real
AWS are deferred to the consolidated verification pass.
