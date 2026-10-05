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

For AWS candidate-request foundations, run from the repository root:

```bash
python3 -m unittest -v tests/scripts/aws_validation_test.py
python3 -m unittest discover -s tests/scripts -p '*_test.py'
actionlint .github/workflows/ci.yml .github/workflows/aws-check-proof.yml
actionlint .github/workflows/aws-validation.yml
```

For the AWS E2E image, run `hadolint e2e-tests/Dockerfile`. When the native
binary is unavailable, use the pinned container without installing globally:
`docker run --rm -i hadolint/hadolint:v2.14.0 < e2e-tests/Dockerfile`.
Pin Alpine package versions in new Dockerfiles; query the selected base's
repositories rather than guessing a version from a different Alpine release.

Focused stories substitute external GitHub/Git/Docker process boundaries and
assert observable request/build records, owned failure finalization and rejected
archives without AWS calls. Real containerized E2E separately verifies packaging.
They do not
prove GitHub merge/check selection. The manual association harness always leaves
**AWS validation** unsuccessful; live positive/latest-attempt protection proof
is still required before deployment workflows or merge-gate activation.

Terraform definitions have plan-only mock tests in all three roots; run the
root-specific init/validate/test commands from [infra/aws/README.md](../infra/aws/README.md).
Mock tests and read-only IAM simulations are not actual OIDC/role/isolation proof.
Publisher stories additionally substitute the AWS/Docker process boundaries,
reject source-artifact/candidate/account mismatches and compare pushed digests.
The manual validation workflow is currently packaging-only and always concludes
its candidate check as failure; actual runtime validation remains unfinished.

Runtime stories: `python3 -m unittest -v tests/scripts/aws_runtime_test.py`.
Opt-in actual local reset:
`AWS_IMAGE_RECEIPT=<protected-images-receipt> python3 -m unittest -v tests/integration/aws_compose_reset_test.py`.
See [runtime contract](../infra/aws/runtime/AGENTS.md) for prerequisites/scope.
This verifies clean DB/cache/broker state and E2E, not EC2 metadata/reboot isolation.

Read-only role proof stories:
`python3 -m unittest -v tests/scripts/aws_boundary_proof_test.py`.
Lint `.github/workflows/aws-boundary-proof.yml` with actionlint. Actual OIDC
allowed/denied reads are proved only by a trusted-main hosted manual run;
process-boundary tests do not substitute for that run or prove mutations.

Read-only E2E integration must exercise the trusted runtime's actual execution
helper, including offline Maven, writable bounded build-parent tmpfs and report
copy before container termination. A passing writable `docker run` does not prove
the read-only configuration. Cache dynamically selected test providers explicitly
during packaging; never bypass Maven `clean` failures by silently skipping it.

## GitHub Actions Checks

Guarded operator-plan proof stories:
`python3 -m unittest -v tests/scripts/aws_operator_plan_test.py`.
Run actionlint on `.github/workflows/aws-operator-plan-proof.yml`; resolve a
provisioned binary when it is not on `PATH`. Current reviewed upstream lacks
GitHub's documented `concurrency.queue` schema: only its exact diagnostic may be
excluded with `-ignore '^unexpected key "queue" for "concurrency" section\.'`,
provided parsed YAML independently verifies the shared group, `queue: max` and
`cancel-in-progress: false`. Do not ignore other diagnostics or claim full schema
support; hosted manual syntax proof remains required.
These stories prove authorization before AWS,
wrong-role refusal, missing/empty/mismatched-state refusal before Terraform,
locked no-change planning, nonempty expected plan coverage and no apply/raw
publication. Real operator OIDC/native-lock/plan permissions require a separate
trusted-main manual run. A no-change plan does not prove EC2 mutation permissions.
Modified trusted controller/config inputs refuse before AWS; only six fixed
Terraform inputs, never arbitrary extra files, enter the private planning root.
Process doubles must preserve actual empty-output semantics: a no-change
`git diff` emits zero bytes, not a blank line. Otherwise an honest clean-checkout
guard is tested against a fictitious difference and positive stories fail.
Failed proof attempts must retain sanitized, actionable evidence: fixed stage/
reason and allowlisted API error/action labels only. Raw process output, resource
identifiers and credentials remain private. A red proof must not be described as
passing merely because authorization/OIDC succeeded.
Capture regressions exercise both a legitimate 20 MiB provider file (must
succeed) and excessive private logs (must stop, stay bounded and emit no raw
content). Global child file-size limits are not selective log limits. Start with
these focused stories before the full automation/hosted verification.
Detached-test runtime stories additionally require absence before credentials or
reset even when the local record is terminal; failed lookup must not mean absence.
Host recovery-observation stories additionally exercise current-generation/prior
identity, active lock and detached-test refusal, and unchanged unknown outcomes.
The positive command story was observed RED-to-GREEN; these process-boundary
stories are not actual cloud cancellation recovery or retry authorization.
Workflow action inputs must match metadata at their exact pinned SHA. Live run
`37245192463` proved operator locked no-change planning with the previous pin;
the updated v6.3.0 credential action passed separate hosted proof `37246888048`.

Controller report-content stories:
`python3 -m unittest -v tests/scripts/aws_reports_test.py`. Exercise actual tar/XML
parsing with GitHub substituted only at its process boundary. Require exactly the
two recognized suites/four cases with matching counters and no skips/failures;
reject compressed/linked/traversing/extra/duplicate/excessive/malformed input.
Verify candidate changes before evidence output refuse and arbitrary testcase
properties/output never enter evidence. Report content is not execution provenance
or AWS success; the future locked runtime must establish both stage and transport
identity before any gate success.

Cloud-observation stories:
`python3 -m unittest -v tests/scripts/aws_cloud_reconciliation_test.py`. Cover
fixed read-only operations, wrong-role/state/lock/transition refusal, active later
SSM pages and nonterminal actual invocations, private output bounds and denied
lock lookup not being absence. The operator-plan stories also refuse active
remote operations before Terraform. Mocked process boundaries and owner schema
reads do not replace actual operator OIDC proof; passing observations never
authorize mutation without host and persistent-ledger reconciliation.
Lock absence stories must model prefix-restricted roles accurately: missing HEAD
can be 403, not the owner's 404. Use exact-prefix bounded listing and assert that
denial/truncation cannot become absence. Policy simulation and owner reads remain
quick diagnostic feedback, not live role proof.
The corrected actual operator cloud/native-plan proof passed `37275392787`; host
and persistent operation recovery still remain separate required evidence.

Publication-input stories:
`python3 -m unittest -v tests/scripts/aws_publication_evidence_test.py`. Authenticate
the exact trusted-main publishing run/attempt/prerequisite jobs and artifact
digest, verify a bounded inert receipt/fixture, and recheck candidate before output.
Reject missing/duplicate/expired/corrupt artifacts, skipped publisher, forged run,
wrong candidate/mutable target, unsafe ZIP and fixture mismatch. Historical owner
GitHub reads can verify schema/provenance quickly but do not replace the command's
current pending-attempt authorization or current-candidate runtime evidence.

Owner host-setup command stories:
`python3 -m unittest -v tests/scripts/aws_host_setup_test.py`.
These substitute only the AWS process boundary and verify no mutation for foreign
generation/active remote operation, finite checksummed transfer, no raw output,
visibility-delay polling, conditional persistent intent, and read-only lost-ID
discovery against document/target/body/timeout/terminal invocation. Missing,
ambiguous, active/cancelling, duplicate-field, excessive or foreign records refuse
reconciliation; failed terminal commands remain failed, not setup success.
Positive discovery and pre-download size bounds were observed RED-to-GREEN;
the remaining boundaries are explicit regression stories. They do not prove
host installation/isolation or routine OIDC orchestration; record those separately.

Every push and pull request runs independent Java, frontend, and image/PR-E2E
jobs. Three explicit Java jobs use Temurin 25: Auth and API Gateway each run
`./mvnw --batch-mode clean verify` at their module root, while one runner runs
`common` `clean install` before Items `clean verify`. Frontend uses Node 24 and
runs `npm ci`, `npm run lint`, and `npm run build` from `frontend/`. There is no
current frontend unit-test script.

PR CI additionally runs a credential-free `Candidate identity` job without
executing candidate application code. It records the exact checked-out merge SHA,
head/base parents, PR and run/attempt identity as a 14-day artifact. GitHub's run
and job `head_sha` fields report PR head and are not proof of this merge checkout.
AWS request verification rejects skipped required CI jobs and candidate changes
to the trusted CI workflow.

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
The Maven suite also runs one `RestAssuredLoggingTest` regression, so current
reports contain four total tests: three in `ItemsE2ETest` and one logging guard.
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
deadline=$((SECONDS + 120))
until curl --fail --silent --show-error "${gateway_url}/actuator/health"; do
  (( SECONDS >= deadline )) && exit 1
  sleep 5
done
deadline=$((SECONDS + 120))
until curl --fail --silent --show-error "${frontend_url}/"; do
  (( SECONDS >= deadline )) && exit 1
  sleep 5
done
(cd e2e-tests && E2E_BASE_URL="${gateway_url}" ./mvnw --batch-mode clean test)
docker compose down -v --remove-orphans
```

The commands from Compose startup onward reproduce the PR-only full-stack path.
Run the cleanup command after any local Compose attempt, including a failed
readiness or E2E check. GitHub-hosted CI uses canonical `localhost:10000` and
`localhost:5173` on a clean runner. Local reproduction queries the active
Compose mappings, so the same commands honor generated worktree ports and
default ports without sourcing `.env` into the shell.
