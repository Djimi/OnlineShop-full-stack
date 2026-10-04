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

**Unfinished:** routine cloud-side SSM transport/reconciliation, cancellation
proofs, diagnostics and final AWS gate. The eligible Free-plan host now exists;
host prerequisites are installed, not application deployment.
Owner setup and selected Auth/E2E isolation probes now pass, including Docker
restart and actual reboot/firewall retention with host ECR/secret access. Full
application/E2E, all-image probes and cancellation recovery remain unfinished.

`scripts/aws-host-setup.py` owns the initial owner-only SSM setup proof. It transfers
only trusted-main host files with chunk bounds/checksums and persists command
intent/IDs to protected storage before polling. Existing/unknown records block
automatic retry. This is not the routine validation/disposal orchestrator.

## Host interface

The pinned AL2023 image supplies `curl-minimal`; request that package during
setup, not full `curl` which conflicts with it. Do not mask dependency failures
with broad package removal or `--skip-broken`. The first real SSM setup proved
this failure; corrected trusted setup completed successfully after owner
reconciliation, with original failure/recovery records retained separately.

Install trusted root-owned files at `/opt/onlineshop-test`: `host-setup.sh`,
`run-stack.py`, `compose.yml` and restricted `bootstrap.json` containing only
verified `account_id`, `region`, `secret_arn`. Root-owned `.runtime/` (0700)
contains current-generation record, verified fixtures, process lock and operation
record. Candidate processes must not edit these or access the Docker socket.

`bash host-setup.sh --setup` requires AL2023 x86_64/root; it installs host
dependencies, checksum-pinned Compose v5.5.1 and Docker pre/post-start firewall
hooks. `--firewall` rejects forwarded IPv4/IPv6 IMDS access without flushing rules
or blocking host OUTPUT needed by SSM/ECR/Secrets Manager. Selected Auth/E2E
restart/reboot probes passed. Runtime verifies both rules and forwarding attachment
before credentials or reset.

`python3 run-stack.py --generation run-<id>-attempt-<n> --images <receipt>`
requires the current generation, fixed-account/repository digests and fixture
size/hash. Only owned project resources are reset. Old fixture files are removed
after stopping writers so removed SQL cannot run in the next generation.

DB secrets come only from the named secret through restricted temporary config;
cloud/job credentials never reach candidate containers. Temporary secret/auth
files are removed even on partial creation/test-cleanup failure. The current E2E
source now consumes the provided generated `E2E_TEST_PASSWORD`; historical
published images predate that fix and must not be treated as current candidates.

## Evidence and recovery

External command output is captured in anonymous private files with an 8 MiB
kernel file-size limit per stream, not unbounded memory pipes checked afterward.
E2E has a read-only root and bounded tmpfs for `/tmp` (128 MiB), `.build` (512 MiB)
and the wrapper cache (64 MiB). Maven runs offline with its build directory set
to `.build/target`, a child of the mount so `clean` can delete it normally.
An isolated keepalive container remains running while Maven executes and reports
are streamed through bounded `docker exec tar`; only then is termination verified.
Docker `cp` cannot read tmpfs, and stopping the container discards its contents.
The image resolves the provider with `dependency:get` and declares the JUnit
launcher during packaging; `go-offline` alone missed the provider. No runtime
repository writes are allowed.

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
For verification of a freshly changed E2E module before republishing, build
`onlineshop-test-e2e:credential-proof` and set `E2E_LOCAL_IMAGE` to that exact tag.
The opt-in integration test resolves its immutable local image ID; this is solely
a local-test override, never accepted by the AWS host runtime or publisher receipt.
