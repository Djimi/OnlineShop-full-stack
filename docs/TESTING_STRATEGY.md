# Testing Strategy

> Important 
>
>For all cases where code can be improved to be more testable, that should be the proposed approach instead of work around the problems!

> Important 
>
> In tests always use version for 3th party technologies listed in the docker compose file in the root dir - Postgres, Redis, etc.

## When to Run Tests

**Run tests after EVERY code change — BEFORE committing.** This is a hard requirement:

1. `./mvnw clean test` from the affected service directory (e.g., `Items/`, `Auth/`)
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

**Real time:** Unit tests must never burn real time in retry/backoff loops. Bounded retries keep their attempt budgets in tests, but the sleep must be injectable or disabled (e.g., the `delivery/tests/conftest.py` autouse fixture no-ops `ecr._sleep`; the production delay stays 5s/6 attempts). Test fakes must also mirror the real AWS response shape — `batch_get_image` responses carry `imageTag` alongside `imageDigest` — or retry logic keyed on those fields never converges and tests fail after minutes of wall time.

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

## References

<!-- | What | Where |
|------|-------|
| Running tests | [CLAUDE.md](../../CLAUDE.md) — Essential Commands |
| Unit test example | `Items/src/test/java/**/ItemServiceTest.java` |
| Integration test example | `Items/src/test/java/**/*IntegrationTest.java` |
| E2E tests | `e2e-tests/src/test/java/` |
| JaCoCo configuration | Service `pom.xml` files — search for `jacoco-maven-plugin` |
| Test data utilities | `*/src/test/java/**/testutil/` | -->

## Release Contract, Candidate Evidence, ECR Release Tagging, Promotion, Production Hardening & Traceability Gates (Pass 3)

The release tooling has six independent offline verification gates (read
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/README.md` and
`docs/CI_CD_GOTCHAS.md` for full context):

```bash
# 3.1 manifest contract: validator, fixtures, checksums, input helpers
bash tests/scripts/release_contract_test.sh

# 3.2 candidate evidence: Python suites, workflow static checks,
# reproducible frontend packaging, safe extraction, publish/reuse/fail-closed,
# SBOM stub, evidence→manifest flow, artifact identity/digest recording
bash tests/scripts/candidate_evidence_test.sh

# 3.3 ECR release tagging, immutability, least privilege: immutable-repo
# apply/read-back with drift fail-closed, server-side digest-preserving
# mint/reuse/conflict/dry-run promotion, release-identity proceed/resume/
# collision, IAM + OIDC trust policy validation, workflow job-permission and
# tag-family static checks, mandatory profile/region + read-back scan
bash tests/scripts/ecr_release_tagging_test.sh

# 3.4 controlled staging-to-production promotion: the release_contract.promotion
# decision layer (dispatch/run/ancestry/preflight/snapshot/plan/waiter/
# frontend/verify/finalize/compensate), promote-release.yml static checks
# (production Environment, shared non-cancelling production-mutation
# concurrency, no rebuild, preflight repeated post-approval, compensate on
# failure, SHA-pinned Actions), and stateful AWS + gh stub runs of
# promotion-preflight/snapshot-production/verify-production/finalize-release/
# compensate-production/deploy-production dry-run (fail-closed on unreviewed
# schema changes, run/ancestry drift, digest/marker/ALB drift, publication
# before verification, release-tag conflicts), plus a mandatory profile/region
# + mutation read-back + no-secrets static scan
bash tests/scripts/promotion_test.sh

# 3.5 production hardening: task-definition + service-config fixtures
# (CPU/memory, awsvpc, named Service Connect ports, logs, health, graceful
# termination, digest-only images, versionConsistency, circuit breaker +
# safe rolling), sanitized task-definition transforms (image-only diff, full-
# ARN secrets[].valueFrom, no plaintext leaks), stateful AWS-stub runs of the
# read-only production inventory, production/staging separation (identity +
# topology), frontend S3 REST + OAC verify, CloudTrail coverage, lifecycle
# environment guards, and the OAC migration tool with per-step read-back
bash tests/scripts/production_hardening_test.sh

# 3.7 release traceability: the four read-only lookups (commit/release/
# running/digest) + the manifest<->ECR<->ECS<->frontend consistency audit via
# release/bin/trace.sh and release_contract.traceability, offline fixture
# coverage of consistent/paused/drift state (ECR tag digest, running digest,
# frontend marker), newest-first ordering independent of index order, mixed/
# incomplete running digest sets, sha-tag digest mismatch, candidate-run
# conflicts, by-version immutable prefix-marker verification, malformed-marker
# and partial describe-services failures closing as OBSERVED_READ_ERROR, a
# stateful AWS-stub run of the live gather path proving the mandatory identity
# preflight and read-only behavior, the read-only GitHub Releases index auto-
# fetch (exact release-manifest.json asset selection), and missing/ambiguous/
# contradictory fail-closed
bash tests/scripts/release_traceability_test.sh
```

All six require Python 3.10+ with `pip install -r
plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/requirements.txt`
(`jsonschema==4.26.0`, `PyYAML==6.0.3`) and optionally `ruff` + `shellcheck`.
Live AWS/GitHub evidence (ECR repository settings read-back, real
put-image behavior, the real OIDC environment subject, IAM Access Analyzer,
the real production inventory read-back, the live frontend OAC migration, real
CloudTrail read-back, the real traceability lookups/audit against live
production, and the real owner-approved promotion — the `production`
Environment approval, ECR/ECS/S3/CloudFront mutations and read-backs, and the
GitHub Release publication) is verified in the consolidated Pass 3 verification
pass, not by these offline gates.

## Pass 3R.1 CI security and promotion handoff gate (offline)

The 3R.1 gate covers the workflow security boundary and the exact candidate →
snapshot → deployment → official handoff. It executes only local static checks
and stateful AWS/GitHub stubs; it does not start staging or contact live AWS,
GitHub, or production. The checks prove that GitHub contexts reach shell only
through step `env`, hostile values stay inert, permissions are job-scoped,
candidate evidence is bound to the exact run/attempt and optional `source_sha`,
the real GitHub `head_branch: "main"` / attempt-jobs API shape is handled, the
snapshot binds the actual live release/tag/immutable frontend prefix and
full-object SHA-256 checksum, and publication/restore/compensation request
SHA-256 object checksums.

```bash
bash tests/scripts/ci_security_contract_test.sh
bash tests/scripts/promotion_handoff_test.sh
bash tests/scripts/promotion_test.sh
bash tests/scripts/rollback_test.sh
```

`promotion_handoff_test.sh` runs the real shell wrappers against a stateful
offline stub and checks that the candidate remains unchanged, the snapshot
provides current task-definition ARNs, deployment emits final ARNs, only the
deployment manifest becomes official, verification precedes finalization, and
the finalization decision is dry-run/idempotent. The structural PR/trusted-job
split is deferred to 3R.2/3R.3, the live role cutover to 3R.9, and environment
approval, AWS mutations, and GitHub Release publication to 3R.10.

### Running Tests

> **Important:** Always run from the target service directory — NOT from a parent or sibling directory. Do NOT use `-f ../Service/pom.xml` patterns.

```bash
# Unit + Integration (per service), run from the respective service folder
cd Items/ && ./mvnw clean test
cd Auth/ && ./mvnw clean test

# E2E tests (from e2e-tests/, requires docker compose up first)
cd e2e-tests/ && ./mvnw clean test

# With coverage report
./mvnw clean test jacoco:report
# Report at: target/site/jacoco/index.html
```
