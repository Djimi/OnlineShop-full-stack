# Testing Strategy

> Important 
>
>For all cases where code can be improved to be more testable, that should be the proposed approach instead of work around the problems!

> Important 
>
> In tests always use version for 3th party technologies listed in the docker compose file in the root dir - Postgres, Redis, etc.

## When to Run Tests

**Run tests after EVERY code change — BEFORE committing.** This is a hard requirement:

1. `./mvnw clean verify` from the affected service directory (e.g., `Items/`, `Auth/`). This runs Surefire unit tests and Failsafe integration tests.
2. If E2E tests apply to the change, also run `./mvnw clean test` from `e2e-tests/`
3. Only commit if ALL tests pass — never commit failing tests or skip testing

For changes to the worktree-creation command, run its focused black-box suite
from the repository root:

```bash
python3 -m unittest -v tests/scripts/worktree_creation_test.py
```

Tests for repository automation must also follow the story-oriented guidance in
[SCRIPT_GUIDELINES.md](./SCRIPT_GUIDELINES.md): test observable command
behavior, keep scenario setup explicit, and avoid mirroring implementation
helpers or retaining coverage for removed modes.

## GitHub Actions Checks

Every push and pull request runs independent Java, frontend, and image/PR-E2E
jobs. The Java matrix uses Temurin 25: Auth and API Gateway each run
`./mvnw --batch-mode clean verify` at their module root, while one runner runs
`common` `clean install` before Items `clean verify`. Frontend uses Node 24 and
runs `npm ci`, `npm run lint`, and `npm run build` from `frontend/`. There is no
current frontend unit-test script.

The image job runs `docker compose build auth-service items-service api-gateway
frontend` on both events. This packages the four deployable applications only;
`common` is a library. Packaging is not verification: each Java Dockerfile uses
`-DskipTests`, and the frontend Dockerfile runs Vite development mode instead
of the production-build command. Images are not published or deployed, and the
image job has no dependency on the Java or frontend jobs. CI job caches and
Docker build cache are not shared with the PR E2E Maven invocation.

Only a pull request starts the full default Compose stack after image building:

```text
Java 25 without Maven cache -> docker compose up -d --no-build --wait --wait-timeout 300
-> bounded gateway health and frontend-root probes -> API E2E -> reports/diagnostics
-> always: docker compose down -v --remove-orphans
```

The E2E command is run from `e2e-tests/` with
`E2E_BASE_URL=http://localhost:10000` and is limited to the current three API
tests through the gateway, Auth, and Items. It does not browser-test the
frontend or directly test every infrastructure service or administrative UI.
PR runs retain narrowly scoped Surefire/Failsafe report artifacts, a bounded
Compose status, and bounded redacted application logs on failure; cleanup is
always attempted. No hosted run or artifact inspection should be inferred from
these documented commands.

Aggregate frontend content checksums must be path-independent: hash
newline-terminated per-file SHA-256 hex values sorted by canonical relative
path, never tool output containing environment-specific path prefixes.

**Test output must show clean (zero failures).** Warnings from libraries (Mockito self-attach, Jansi, etc.) are expected and can be ignored.

## Testing Philosophy

[//]: # (We follow **Test-Driven Development &#40;TDD&#41;** when writing new code when writing new code: write a failing test first, implement the minimal code to pass, then refactor. Tests are not an afterthought—they drive design decisions and serve as living documentation.)

[//]: # ()
[//]: # (Tests provide confidence to refactor, deploy, and evolve the system. We optimize for **fast feedback loops**: unit tests run in milliseconds, integration tests in seconds, E2E tests in minutes. The testing pyramid reflects this—many fast tests at the bottom, few slow tests at the top.)

## Testing Pyramid

```
                ▲
               / \           E2E Tests
              /   \          (Few, Slow, High Confidence)
             /─────\
            /       \        Integration Tests
           /         \       (Some, Medium Speed)
          /───────────\
         /             \     Unit Tests
        /               \    (Many, Fast, Focused)
       ─────────────────────
```

## Coverage Targets

| Test Type   | Target | Scope                          | Tools                        |
|-------------|--------|--------------------------------|------------------------------|
| Unit        | >90%   | Single class/function          | JUnit 5, Mockito, AssertJ    |
| Integration | —      | Multiple components, real DB   | Spring Test, Testcontainers  |
| Contract    | —      | API contracts between services | Spring Cloud Contract (future) |
| E2E         | —      | Critical user journeys         | REST Assured                 |

## Test Categories

### Unit Tests
Test business logic in isolation. Mock all dependencies. These are your primary safety net—fast, focused, and numerous. Test edge cases, validation rules, and error handling. Don't test simple getters/setters or framework code.

**Domain events:** When testing code that emits domain events, assert event properties — never only the event type. The right event type with wrong data is still a bug.

### Integration Tests
Verify components work together with real dependencies. Use Testcontainers for PostgreSQL and Redis—never H2 or in-memory substitutes. Test repository queries, controller request handling, and database constraints. These catch issues unit tests miss.

**Integration test requirements:**
- Check ALL side effects, not just API responses: verify DB state (entities persisted/deleted), domain events, any file system changes
- Test both happy path AND error scenarios (404, 400, etc.)
- Use `@SpringBootTest(webEnvironment = RANDOM_PORT)` with `@DynamicPropertySource` for Testcontainers in Spring Boot 4.X
- `@AutoConfigureTestDatabase` and `@WebMvcTest` were removed in Spring Boot 4.0 — do not use them

### Contract Tests (Future)
When services multiply, contract tests prevent breaking changes between API consumers and producers. The consumer defines expectations; the producer verifies compliance. Critical for microservices independence.

### E2E Tests
Validate complete user journeys through the running system. Expensive to write and maintain—reserve for critical paths only: authentication flows, core business transactions, payment processing. Run against `docker compose up`.

## Key Decisions

### Why 90% Coverage Target (Not 100%)
100% creates perverse incentives—testing trivial code to hit a number. 90% ensures meaningful coverage while allowing pragmatic exclusions (DTOs, configuration classes, Spring Boot main classes).

### Why Testcontainers Over H2
H2 lies. It behaves differently than PostgreSQL for JSON columns, array types, and query edge cases. Testcontainers runs the real database—if tests pass, production will work. The few seconds of startup time prevent hours of debugging.

### Why REST Assured for E2E
Fluent API reads like documentation. Given/When/Then structure mirrors BDD. Built-in JSON path assertions. No browser overhead for API testing.

### Coverage Exclusions
Excluded from coverage measurement (see `pom.xml` JaCoCo config):
- `**/config/**` — Spring configuration classes
- `**/*Application.*` — Main class bootstrap
- `**/dto/**` — Data transfer objects (no logic)
- `**/entity/**` — JPA entities only (infrastructure persistence layer). Domain entities in `domain.aggregateroots` ARE tested.
- `**/command/**` — Command objects (pure records, no logic)
- `**/query/**` — Query objects (pure records, no logic)
- `**/web/**` — Controllers, exception handlers (require integration tests with Spring context)
- `**/infrastructure/**` — JPA adapters, ID generators, mappers (require integration tests with real DB)

## Test Naming Convention

```
methodName_stateUnderTest_expectedBehavior

Examples:
- findById_whenItemExists_returnsItem
- findById_whenItemNotFound_throwsException
- calculateTotal_withDiscount_appliesCorrectPercentage
```

File naming:
- `*Test.java` — Unit tests
- `*IntegrationTest.java` — Integration tests
- `*E2ETest.java` — End-to-end tests

## TDD Workflow

1. **Red** — Write a failing test for the next requirement
2. **Green** — Write minimal code to make it pass
3. **Refactor** — Improve design while keeping tests green
4. **Repeat**

Resist the urge to write production code without a failing test first. The discipline pays dividends in design quality and regression safety.

## Running Tests

> **Important:** Always run from the target service directory — NOT from a parent or sibling directory. Do NOT use `-f ../Service/pom.xml` patterns.

```bash
# Unit + Integration (per service), run from the respective service folder
cd Items/ && ./mvnw clean verify
cd Auth/ && ./mvnw clean verify

# E2E tests (from e2e-tests/, requires docker compose up first)
cd e2e-tests/ && ./mvnw clean test

# With coverage report
./mvnw clean verify jacoco:report
# Report at: target/site/jacoco/index.html
```

### Local CI Reproduction

Run the following commands from the repository root. Each Maven wrapper remains
inside its module root, matching CI:

```bash
(cd Auth && ./mvnw --batch-mode clean verify)
(cd api-gateway && ./mvnw --batch-mode clean verify)
(cd common && ./mvnw --batch-mode clean install)
(cd Items && ./mvnw --batch-mode clean verify)
(cd frontend && npm ci && npm run lint && npm run build)
docker compose build auth-service items-service api-gateway frontend
docker compose up -d --no-build --wait --wait-timeout 300
gateway_port="$(docker compose port api-gateway 10000 | awk -F: '{print $NF}')"
frontend_port="$(docker compose port frontend 5173 | awk -F: '{print $NF}')"
gateway_url="http://127.0.0.1:${gateway_port}"
frontend_url="http://127.0.0.1:${frontend_port}"
curl --fail --silent --show-error "${gateway_url}/actuator/health"
curl --fail --silent --show-error "${frontend_url}/"
(cd e2e-tests && E2E_BASE_URL="${gateway_url}" ./mvnw --batch-mode clean test)
docker compose down -v --remove-orphans
```

The commands from Compose startup onward reproduce the PR-only full-stack path.
Run the cleanup command after any local Compose attempt, including a failed
readiness or E2E check. GitHub-hosted CI uses canonical `localhost:10000` and
`localhost:5173` on a clean runner. Local reproduction queries the active
Compose mappings, so the same commands honor generated worktree ports and
default ports without sourcing `.env` into the shell.
