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

## Dev Mode (Hot Restart with DevTools)

Items has `spring-boot-devtools` for fast development. Edit a Java file, save, and DevTools restarts the application context automatically (~2 seconds).

```bash
# 1. Start infrastructure only (databases, Redis, Kafka)
docker compose up -d items-postgres redis kafka

# 2. Run Items in dev mode
./run-dev.sh
```

The script installs `common` to the local Maven repo, then starts Items via `./mvnw spring-boot:run`. DevTools monitors `target/classes` — when your IDE recompiles a modified `.java` file, the app context reloads without a full JVM restart.

> **Multi-worktree host-run:** In a non-main worktree, infra ports are slot-offset. Source the exports first:
> ```bash
> source <(scripts/dev-env.sh --exports)
> docker compose up -d items-postgres redis kafka
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
