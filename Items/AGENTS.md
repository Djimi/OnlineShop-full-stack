# Items Service - Service Documentation

## Quick Reference

| Property | Value |
|----------|-------|
| Port | 9000 |
| Language | Java 25 |
| Framework | Spring Boot 4.X.X |
| Database | PostgreSQL |
| Build Tool | Maven |

## Service Overview

The Items service manages product inventory and search:
- Item creation, updates, and deletion
- Item lookup by id
- Item listing and description search

## Build & Test Commands

**Always run from the `Items/` directory:**

```bash
# Navigate to Items directory first
cd Items

# Compile
./mvnw clean compile

# Run unit + integration tests
./mvnw clean test

# Run tests with coverage
./mvnw clean test jacoco:report
# Report at: target/site/jacoco/index.html
```

> **CRITICAL:** Never run `./mvnw -f ../Items/pom.xml` from a sibling directory. Always `cd` into `Items/` first.

## CI/CD

The CI pipeline builds `common`, runs `./mvnw verify`, and then publishes the Items image on branch pushes. Pull-request builds create Docker images without requesting AWS credentials or pushing to ECR.

After a successful `main` build, the immutable Items image is deployed to the
independent staging ECS cluster. Staging uses its own VPC and RDS database; it
does not use the production cluster or production database. Each staging run
creates an empty RDS instance, applies `init-db/01-schema.sql` and
`init-db/02-data.sql`, verifies the restricted Items user, runs E2E, and deletes
the instance without retaining state.

Items images carry OCI/project labels (revision, source, created, title,
`org.onlineshop.component`, `org.onlineshop.build-run`,
`org.onlineshop.producer.*`) plus `org.onlineshop.common-revision` recording the
same monorepo SHA as the included `common` library. The Pass 3
candidate-evidence workflow uses these labels to make `sha-<full-sha>` publishing
idempotent and to prove the three backend images form one canonical producer
set.

The backend ECR repositories (`onlineshop-*`) are defined with desired state
`IMMUTABLE_WITH_EXCLUSION` (see `release/ecr/immutable-repositories.json`):
`sha-*` and `release-*` tags can never be overwritten, only `main-latest` and
`branch-*` may advance, and `latest` is absent for v1. The live repository
mutation is applied in the consolidated Pass 3 verification pass. The
`release-<version>` tag is minted server-side from the candidate bytes by
`release/bin/promote-image-digest.sh` (never a rebuild).

## Docker Compose Build

Run from the repository root:

```bash
docker compose up -d --build items-service
```

`Items/Dockerfile` uses the repository root as its build context, installs `common`, and packages Items inside Docker. A host-side `target/*.jar` is not required. Use `docker compose up -d --build` to rebuild and start the complete stack.

## Dev Mode (Hot Restart with DevTools)

Items has `spring-boot-devtools` for fast development. Edit a Java file, save, and DevTools restarts the application context automatically (~2 seconds).

```bash
# 1. Start infrastructure only (databases, Redis, Kafka)
docker compose up -d items-postgres redis kafka

# 2. Run Items in dev mode
./run-dev.sh
```

The script installs `common` to the local Maven repo, then starts Items via `./mvnw spring-boot:run`. DevTools monitors `target/classes` — when your IDE recompiles a modified `.java` file, the app context reloads without a full JVM restart.

> **Multi-worktree host-run:** Create the worktree with the root
> `scripts/create-worktree.sh` command. Its infrastructure ports are then
> slot-offset; source the exports first:
> ```bash
> source <(scripts/dev-env.sh --exports)
> docker compose up -d items-postgres redis kafka
> SERVER_PORT="$ITEMS_SERVER_PORT" \
> SPRING_DATASOURCE_URL="$ITEMS_DATASOURCE_URL" \
> SPRING_DATASOURCE_USERNAME="$ITEMS_DATASOURCE_USERNAME" \
> SPRING_DATASOURCE_PASSWORD="$ITEMS_DATASOURCE_PASSWORD" \
> ./run-dev.sh
> ```

> **Note:** DevTools is auto-excluded from the fat JAR by `spring-boot-maven-plugin`, so the production Docker image is unaffected.

## Spring Boot 4.X Important Notes

- `@WebMvcTest` and `@AutoConfigureMockMvc` were **removed** in Spring Boot 4.0.2. Use `@SpringBootTest(webEnvironment = RANDOM_PORT)` with `RestTemplate` for controller integration tests, or `MockMvcBuilders.standaloneSetup()` for lightweight controller tests.
- `@AutoConfigureTestDatabase` was **removed** in Spring Boot 4.0.2. Use Testcontainers with `@DynamicPropertySource` instead.
- **Never use H2** — it behaves differently than PostgreSQL. Always use Testcontainers for integration tests.

## Project Structure

```
Items/
├── src/main/java/com/onlineshop/items/
│   ├── application/     # Use cases, commands, queries, DTOs, events, mappers
│   ├── domain/          # Domain models, value objects, exceptions, and interfaces
│   │   ├── aggregateroots/
│   │   ├── event/
│   │   ├── exception/   # Domain exceptions (ItemNotFoundException, etc.)
│   │   ├── repository/
│   │   ├── service/
│   │   └── valueobject/
│   ├── infrastructure/  # Persistence and integrations
│   ├── web/             # REST controllers and request/response DTOs
│   │   ├── controller/
│   │   ├── dto/         # Web-layer DTOs (never expose application DTOs through HTTP)
│   │   └── exception/   # Global exception handler (maps domain exceptions to HTTP errors)
│   └── ItemsApplication.java
├── src/test/java/com/onlineshop/items/
│   ├── application/usecase/   # Use case integration tests
│   ├── domain/                # Domain unit tests (ItemTest, ValueObjectTest)
│   └── web/controller/        # Controller integration tests
├── init-db/             # Database initialization scripts
└── pom.xml
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/items` | List all items |
| GET | `/api/v1/items/{id}` | Get item by id |
| GET | `/api/v1/items/search?description=...` | Search items by description |
| POST | `/api/v1/items` | Create new item |
| PUT | `/api/v1/items/{id}` | Update existing item |
| DELETE | `/api/v1/items/{id}` | Delete item |

## Testing Guidelines

### Before committing any changes:
1. Run unit + integration tests: `./mvnw clean test` from `Items/` directory
2. Run E2E tests if available: `./mvnw clean test` from `e2e-tests/` directory
3. Only commit if ALL tests pass

### JaCoCo Coverage Exclusions

Packages excluded from coverage (no unit-testable logic):
- `**/config/**`, `**/*Application.*` — Spring config and bootstrap
- `**/dto/**`, `**/command/**`, `**/query/**` — Data records
- `**/entity/**` — JPA entities only (infrastructure layer). Domain entities ARE tested.
- `**/web/**` — Controllers, exception handlers (integration-test territory)
- `**/infrastructure/**` — Adapters, mappers (integration-test territory)

### Unit Test Patterns
- Assert event properties, not just types. `isInstanceOf` alone is insufficient — the right event type with wrong data is still a bug.

### Integration test requirements:
- **Use Testcontainers** with PostgreSQL (version matching `docker-compose.yml`). Never use H2.
- **Check ALL side effects** when testing CRUD operations:
  - Verify HTTP response status and body from the endpoint
  - Verify the database state (entity persisted/updated/deleted correctly)
  - Verify any domain events were published if applicable
- Test both happy path and error scenarios (404, 400, etc.)

### Test file naming:
- `*Test.java` — Unit tests (no Spring context)
- `*IntegrationTest.java` — Integration tests (Spring context + DB)
- `*E2ETest.java` — Controller tests with full Spring context and real HTTP calls

## Database

- Schema: [init-db/01-schema.sql](./init-db/01-schema.sql)
- Seed data: [init-db/02-data.sql](./init-db/02-data.sql)

## Configuration

Main configuration: [src/main/resources/application.yml](./src/main/resources/application.yml)

## AWS CLI Conventions

For AWS CLI commands (infrastructure queries, deployments, etc.), see the root [AGENTS.md](../AGENTS.md) — all AWS commands MUST include `--profile dpm-profile --region eu-north-1`.

Repository pause/resume scripts log UTC timestamped steps, typical durations,
resource-level AWS progress, and actual total runtime. Treat duration values as
operational estimates, not timeout guarantees.

## Pass 3.5 — Production hardening

The production Items release target is **defined** by the hardening contract (see
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/explanations/PRODUCTION-HARDENING-DECISIONS.md`).
This documents what a production release task definition/service **must** look
like before it may be registered or promoted. **It is not a claim about the
current live production state** — no live task-definition/service mutation has
happened; the live read-back and any required tightening run in the
consolidated Pass 3 verification pass:

- The production task definition **must be** **digest-pinned** (`@sha256:`),
  use `awsvpc`, Fargate, a named Service Connect port (`items-port`), `awslogs`,
  a container health check, a positive `stopTimeout`, and
  `versionConsistency=enabled`. `release/bin/validate-task-definition.sh` and
  the `release_contract.ecs_config` fixture suite enforce this before any
  registration; it also enforces that the execution role and task role (when
  present) stay distinct.
- Credentials **must be** injected only through `secrets[].valueFrom` with
  **full** `arn:aws:secretsmanager:...` ARNs (`onlineshop/items/db`); never as
  plaintext in `environment`/`command`. `release/bin/sanitize-task-definition.sh`
  proves a digest-pin changes only the `image` field and keeps secrets in
  `valueFrom`.
- The Items ECS service **must be** configured with the deployment circuit
  breaker with rollback, `minimumHealthyPercent=100`, `maximumPercent=200`, and
  a capacity-provider strategy. Fargate Spot with desired count 1 is the
  explicit v1 cost tradeoff and is not an HA SLA.
- The explicit non-secret production identifiers (log group `/ecs/onlineshop-items`,
  secret name, Service Connect namespace, execution role, ECR repository) live
  in `scripts/config/production.env` and are verified read-only by
  `scripts/inventory-production.sh` and
  `scripts/verify-production-staging-separation.sh`.

## Pass 3.7 — Release traceability

The read-only release traceability queries (`release/bin/trace.sh` +
`release_contract.traceability`) resolve the Items component in both directions:
`commit --sha`/`digest --digest` map the Items ECR `sha-*`/`release-*` tags and
digests, and `running` reports the **running** Items container digest from
`tasks[].containers[].imageDigest` (never only the task-definition image URI),
plus the release identity and approver from the matched official manifest.
`audit` cross-checks the Items manifest digests against ECR and the running
container. The offline gate is `bash tests/scripts/release_traceability_test.sh`;
live lookups against real AWS are deferred to the consolidated verification pass.
