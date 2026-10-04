# Trusted AWS host runtime

This module owns host setup, AWS-only Compose and bounded reset/test evidence.
It consumes verified image/fixture receipts, never service schemas or GitHub
success publication. See [boundaries](../README.md) and
[operational status](../../../docs/TESTING-ENVIRONMENT.md).

```text
Trusted generation/bootstrap/digests/fixtures -> host lock
 -> identities/checksums/firewall -> project ownership -> pull
 -> scoped down/volumes -> fresh service-owned fixtures -> readiness
 -> nonprivileged E2E -> sanitized XML -> verified test removal
 -> terminal operation; app retained for inspection
```

**Unfinished:** cloud-side SSM transport/reconciliation, actual EC2 metadata,
restart/reboot/cancellation proofs, diagnostics and final AWS gate. No host exists.

## Host interface

Install trusted root-owned files at `/opt/onlineshop-test`: `host-setup.sh`,
`run-stack.py`, `compose.yml` and restricted `bootstrap.json` containing only
verified `account_id`, `region`, `secret_arn`. Root-owned `.runtime/` (0700)
contains current-generation record, verified fixtures, process lock and operation
record. Candidate processes must not edit these or access the Docker socket.

`bash host-setup.sh --setup` requires AL2023 x86_64/root; it installs host
dependencies, checksum-pinned Compose v5.5.1 and Docker pre/post-start firewall
hooks. `--firewall` rejects forwarded IPv4/IPv6 IMDS access without flushing rules
or blocking host OUTPUT needed by SSM/ECR/Secrets Manager. Actual restart/reboot
proof remains mandatory. Runtime verifies both rules and forwarding attachment
before credentials or reset.

`python3 run-stack.py --generation run-<id>-attempt-<n> --images <receipt>`
requires the current generation, fixed-account/repository digests and fixture
size/hash. Only owned project resources are reset. Old fixture files are removed
after stopping writers so removed SQL cannot run in the next generation.

DB secrets come only from the named secret through restricted temporary config;
cloud/job credentials never reach candidate containers. Temporary secret/auth
files are removed even on partial creation/test-cleanup failure. The current E2E
image does not yet consume the provided generated `E2E_TEST_PASSWORD`; support
remains pending before AWS acceptance.

## Evidence and recovery

Two recognized suites/four tests are required. XML retention omits arbitrary
properties, environment dumps, system output and candidate-produced failure text.
Failed tests retain sanitized XML where available; zero exit alone cannot pass.
Missing/invalid reports fail. Unknown test termination is recorded `unknown` and
blocks the next run. Cloud cancellation/timeout reconciliation is unfinished.

## Verification

From repository root:

```bash
python3 -m unittest -v tests/scripts/aws_runtime_test.py
bash -n infra/aws/runtime/host-setup.sh
shellcheck infra/aws/runtime/host-setup.sh
AWS_IMAGE_RECEIPT=<protected-images-receipt> python3 -m unittest -v tests/integration/aws_compose_reset_test.py
```

The opt-in integration story uses read-only owner-profile ECR access and a fresh
local project, checks actual digest images/readiness/E2E, seeds both DBs/cache/
Kafka, recreates owned volumes, verifies markers absent and reruns E2E. It always
attempts scoped cleanup; no AWS provisioning or host firewall mutation occurs.
Read-only Vite needs both `.vite` and `.vite-temp` writable temporary mounts.
