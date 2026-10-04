# E2E test module

This module owns gateway-facing API journeys and the logging-redaction regression;
it does not own service schemas, deployment controller code or infrastructure.
See [testing strategy](../docs/TESTING_STRATEGY.md) and
[AWS operational status](../docs/TESTING-ENVIRONMENT.md).

```text
Healthy deployed gateway -> E2E_BASE_URL -> candidate tests -> Surefire reports
```

Run `./mvnw --batch-mode clean test` from this directory, never its parent.
Current reports contain `ItemsE2ETest` (three API journeys) and
`RestAssuredLoggingTest` (one logging regression). No browser/frontend coverage.

## Container packaging

From the repository root: `docker build -t onlineshop-test-e2e:local e2e-tests`.
The image pins Java 25/Alpine and curl, compiles tests/caches Maven dependencies
without running tests, then runs as UID 10001 at `/workspace/e2e-tests`.
Default command is `./mvnw --batch-mode clean test`; default gateway URL is
`http://api-gateway:10000`. Override `E2E_BASE_URL` for another isolated network.

Packaging is not test success. Run the image on the deployed stack's private
network without credentials, Docker socket, host networking or privileged mode;
drop capabilities and enable no-new-privileges. Reports must be collected and
verified independently of its exit code. Runtime metadata-blocking and AWS
report transport remain unfinished; local packaging does not prove isolation.

Lint changed Dockerfiles with hadolint as described in the testing strategy.
Never bake credentials into this image or add candidate-test execution to a
credential-bearing publisher/controller job.

Local runtime reset passed the four existing tests before and after dedicated
DB/cache/broker volume recreation. The registration journey consumes generated
`E2E_TEST_PASSWORD` when provided, otherwise a fresh random per-journey password.
Never reuse a committed registration credential. The reset integration assertion
logs in as the journey-created user using the generated password to prove it was
actually consumed; four recognized report tests remain unchanged.
