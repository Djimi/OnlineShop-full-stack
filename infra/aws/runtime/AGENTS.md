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

**Local batch implemented; hosted proof pending:** routine locked existing-host
validation now joins cloud/immutable-operation reconciliation, idle-host admission,
tag-only saved-plan/apply, protected S3 input transport and bound report outcome.
Disposal/recreation, owner intervention for stranded locks/detached tests, and the
live final AWS gate remain unfinished. The eligible Free-plan host
now runs the app after owner full-stack/E2E and clean-state reset proofs.
Owner setup and selected Auth/E2E isolation probes now pass, including Docker
restart and actual reboot/firewall retention with host ECR/secret access. Actual
routine workflow/current-candidate validation and recovery need hosted AWS proof.

`scripts/aws-host-setup.py` owns the initial owner-only SSM setup proof. It transfers
only trusted-main host files with chunk bounds/checksums and persists command
intent/IDs to protected storage before polling. Existing/unknown records block
automatic retry. This is not the routine validation/disposal orchestrator.

Its owner-only `--reconcile` mode is read-only: bounded authoritative versioned
record -> unique generation/stage SSM discovery -> document/target/hash/timeout
identity -> actual terminal invocation -> protected create-only report. Missing,
ambiguous, active, cancelling or mismatched operations refuse. It never authorizes
retry, clears runtime unknown state or synthesizes setup/AWS success. Controlled
live lost-command-ID discovery passed with exact original cloud bytes restored
under CAS and version history retained. Routine all-operation reconciliation is
owned by the separate locked controller; its local batch needs hosted proof.

## Host interface

Host replacement for an inspected infrastructure change requires installing these
trusted prerequisites again before any application. A mere attempt-tag update
must not replace the host. Keep remote operation/evidence history in protected
storage, not solely on the disposable disk.

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
The initializer explicitly sets its nonsecret fixture tree to 0755 and SQL to
0644 even under the bootstrap's `umask 077`; private `.runtime`/credential
directories remain 0700 and credential files 0600. PostgreSQL's UID must be able
to read the mounted `init-db` directories without exposing private runtime parents.

DB secrets come only from the named secret through restricted temporary config;
cloud/job credentials never reach candidate containers. Temporary secret/auth
files are removed even on partial creation/test-cleanup failure. The current E2E
source consumes the provided generated `E2E_TEST_PASSWORD`; the verified owner
receipt from publication `37238396197` includes that fix and hardened read-only
E2E. Earlier receipts predate those changes. No historical receipt can substitute
for a freshly authorized current-candidate validation.

## Evidence and recovery

External command output is captured in anonymous private files with an 8 MiB
kernel file-size limit per stream, not unbounded memory pipes checked afterward.
E2E has a read-only root and bounded tmpfs for `/tmp` (128 MiB), `.build` (512 MiB)
and the wrapper cache (64 MiB). Maven runs offline with its build directory set
to `.build/target`, a child of the mount so `clean` can delete it normally.
An isolated keepalive container remains running while Maven executes and reports
are streamed through bounded `docker exec tar`; only then is termination verified.
Accept only fixed uncompressed report tar: check bounded raw headers before
tarfile handles metadata, then use streaming parsing. Reject compressed/PAX/GNU/
sparse headers, links, unknown members, duplicate paths and trailing payload;
compressed capture size alone does not bound pre-member metadata allocation.
Docker `cp` cannot read tmpfs, and stopping the container discards its contents.
The image resolves the provider with `dependency:get` and declares the JUnit
launcher during packaging; `go-offline` alone missed the provider. No runtime
repository writes are allowed.

Two recognized suites/four tests are required. XML retention omits arbitrary
properties, environment dumps, system output and candidate-produced failure text.
Failed tests retain sanitized XML where available; zero exit alone cannot pass.
Missing/invalid reports fail. Unknown test termination is recorded `unknown` and
blocks the next run. Routine recovery still refuses active/cancelling SSM or
detached tests; terminal SSM plus fresh host absence can recover-abort only.
The controller's `verify-reports` consumer expects an uncompressed flat tar of
the two `TEST-<suite>.xml` files. It independently validates suite/case counts and
zero failures/errors/skips, emitting only counts/attempt identity, not arbitrary
XML content. It cannot prove execution/transport identity; locked cloud runtime
must establish that separately before success. Do not feed raw Docker tar output
directly: trusted collection must select the two sanitized files in this format.
Before any credentials/reset, runtime now verifies the fixed test container is
absent, regardless of a terminal prior local record. A failed Docker lookup is
not absence; an existing container requires reconciliation, not automatic removal.
This admission guard leaves prior records unchanged and does not clear unknown
state or authorize recovery. The detached-test story was observed RED-to-GREEN.

`python3 run-stack.py --generation run-<id>-attempt-<n> --reconcile` observes
recovery without credentials or reset: acquire host lock -> verify current/prior
record identity -> prove fixed test container absent -> emit bounded JSON. Active
locks, detached tests, failed Docker lookup and foreign/malformed records refuse.
The observation leaves the prior outcome unchanged, even when `unknown`; it
cannot authorize retry or publish success. Routine cloud orchestration must still
reconcile SSM/EC2/Terraform and persist a reviewed recovery decision. This command
does not clear a running/unknown operation just because its process ended.
The controller's `reconcile-cloud` command supplies a separate read-only cloud
observation: operator account, fixed state/pointer/native-lock, live EC2 identity,
and bounded paginated SSM parent/invocation terminal status. Neither observation
alone—or simply combining both booleans—authorizes retry: persistent operation
identity/outcomes and launch gaps must still be reconciled by the locked session.
Cloud lock absence uses exact-prefix bounded S3 listing, not a missing-object HEAD
assumption under a prefix-restricted role. Denied/truncated lookups cannot authorize
retry or establish absence; no IAM widening or automatic unlock is involved.
Actual operator cloud observation/native-plan proof passed `37275392787`; it does
not substitute for host recovery. The controller's `verify-publication` authenticates
the exact trusted publishing attempt/receipt and verifies fixture bytes before
transport. Host inputs must use that authenticated receipt, not a PR-provided
manifest claiming the same candidate or an old owner's historical publication.
An actual owner proof cancelled SSM with a detached isolated test container;
the next runtime invocation refused mutation until owner removal/absence and
host-lock release were verified. This does not implement automatic reconciliation.

Owner proof evidence remains bound to its specific host and historical published
candidate. Replacing the host requires fresh setup and current-host runtime
verification; old host's successful probes cannot publish a new candidate's check.
The migrated host now passed full-stack readiness/four E2E tests and a second
seeded DB/Redis/Kafka clean-state reset/four-test retest. Sanitized XML and removal
of the test container/temporary secret files were verified. These are owner
historical-candidate proofs, not routine workflow or current-candidate success.

Current-host probes also reject IPv4/IPv6 metadata transport from all five
candidate images and verify running services are nonroot, nonprivileged, without
cloud/job credentials or the Docker socket. Candidate roots are read-only; Kafka's
backing-service root remains writable. Frontend IPv6 reports `EADDRNOTAVAIL` (no
source address), not a routed firewall-denial proof. Probe tooling failures must
not be interpreted as metadata rejection; distinguish transport errors explicitly.

## Routine session: local implementation, awaiting AWS proof

```text
validate-preflight: actors -> current/latest candidate -> authentic publisher receipt
locked validate: account/state/native lock -> immutable SSM discovery -> idle host
  -> intent + pointer CAS -> protect incoming digests -> fresh saved tag-only plan
  -> inspect -> exact native-locked apply -> create-only S3 inputs
  -> fixed bounded SSM downloader -> trusted host session + inherited host lock
  -> reset/readiness/E2E/reports -> verified cleanup -> create-only host evidence
  -> independent inert report parsing -> pointer/retention transition
  -> pending-success summary -> artifact upload/retention verification
  -> current/latest candidate recheck -> owned check success
```

Failure-only finalization is idempotent: an already completed owned check returns
success without any PATCH. It must not turn a successful validation into failure
when the always-run outcome job follows the locked validation job.

The initial legacy `{generation, host_id, status: provisioned}` pointer is an
explicit admission case. Routine schema 1 records the request, predecessor,
images and known retained-image history. Running/failed crash gaps permit the
recorded predecessor's cloud generation; completed state must match exactly.
Cloud generation and actual host predecessor are observed separately: a crash
after tag apply can leave the host's local generation unchanged across multiple
attempts; verified host identity is carried independently of the cloud predecessor.
Durable exact old/new admission transitions and unchanged predecessor archives
reconcile interrupted local writes/launch. Conflicting history or foreign
generations refuse. Pointer ETags
are held by the controller, never embedded as if they were the current ETag.

Immutable `*-intent`, `*-launched` and `*-terminal` events live under
`operations/<generation>/`. A lost send response is never resent. Complete bounded
SSM discovery must find exactly one matching comment/document/host/body hash and
timeout; parent and actual invocation must be terminal. Zero/duplicate matches,
active/cancelling SSM, native locks, unknown state or detached tests refuse.
Only a fresh host-lock/process/test/temporary-credential absence proof permits
`recovered-aborted`; old reports or exit zero cannot synthesize recovered success.

Host-role transport is limited to `GetObject` on `operations/runtime-input/*`
and create-only `PutObject` on `operations/runtime-evidence/*`. The narrow policy
change needs an inspected owner bootstrap apply before a hosted trial; routine
operator permissions were not widened. A single finite bundle carries trusted
runtime files, receipt, independent fixtures, nonsensitive bootstrap and binding;
the fixed SSM bootstrap verifies its exact size/SHA before flat restricted writes.
Do not inline large fixtures into thousands of SSM chunks.

`host-session.py --generation <generation>` holds the same lock through admission,
the actual `run-stack.py` executable, evidence and cleanup. The child inherits a
descriptor verified against the trusted lock inode. Host evidence is per-generation
and binds the full current request, host/operation/generation, image/fixture/runtime
hashes, recognized stages, report hash and cleanup. Prior local operation bytes
are archived unchanged. Candidate reports are sanitized again before public
artifacts; failures preserve available safe reports and fixed-stage summaries.
Successful runtime leaves the required check pending. `finalize-success` consumes
the uploaded artifact through GitHub's API, verifies the exact trusted active job,
successful upload step, artifact identity/digest, bounded content and at least
14-day retention, then rechecks current candidate/latest attempt before success.
Upload failure, pre-upload cancellation or summary-write failure stays non-success.

Previous and incoming digests stay protected during replacement. Only known
routine `active-<generation>` tags are released after the new recorded deployment
and report proof; owner/unknown tags remain. Failed-generation retention is carried
forward and capped at 20 predecessors, then fails for reviewed recovery. A missing
or disposed environment explicitly refuses recreation in this batch; Task 7 owns
verified disposal/recreation. No automatic force-unlock or detached-test removal.

## Verification

From repository root:

```bash
python3 -m unittest -v tests/scripts/aws_runtime_test.py
python3 -m unittest -v tests/scripts/aws_host_session_test.py tests/scripts/aws_orchestration_test.py tests/scripts/aws_validation_workflow_test.py
bash -n infra/aws/runtime/host-setup.sh
shellcheck infra/aws/runtime/host-setup.sh
AWS_IMAGE_RECEIPT=<protected-images-receipt> python3 -m unittest -v tests/integration/aws_compose_reset_test.py
```

The opt-in integration story uses read-only owner-profile ECR access and a fresh
local project, checks actual digest images/readiness/E2E, seeds both DBs/cache/
Kafka, recreates owned volumes, verifies markers absent and reruns E2E. It consumes
receipt-verified sibling `fixtures.tar` through the production initializer under
restrictive umask and checks actual PostgreSQL UID access. Scoped cleanup is always
attempted; no AWS provisioning or host firewall mutation occurs.
Read-only Vite needs both `.vite` and `.vite-temp` writable temporary mounts.
For verification of a freshly changed E2E module before republishing, build
`onlineshop-test-e2e:credential-proof` and set `E2E_LOCAL_IMAGE` to that exact tag.
The opt-in integration test resolves its immutable local image ID; this is solely
a local-test override, never accepted by the AWS host runtime or publisher receipt.
