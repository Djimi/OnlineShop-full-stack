# AWS Testing Environment

## Purpose and current status

Track what is actually configured for the shared AWS PR testing environment.
The [design spec](./superpowers/specs/2026-10-03-aws-testing-environment-design.md)
is the owner-facing architecture/decision record; its
[detailed contract](./superpowers/specs/2026-10-03-aws-testing-environment-requirements.md)
is the source of truth for exact requirements. This file is the operational record,
not a duplicate specification.

**Current status (2026-10-04):** Owner authorized inline execution of the
[implementation plan](../planning/aws-testing-environment-PLAN.md). Read-only AWS
inventory and local candidate-request foundations are implemented on
`feat/aws-testing-environment` (foundation PR #72 is merged). The owner explicitly
authorized continuing all operations without further approvals. Documented
playground cleanup is verified; live proofs and packaging/infrastructure work
continue on `feat/aws-testing-runtime`. The protected state backend has since
been provisioned/migrated; no application host has been provisioned or deployed.

GitHub plan is Free; repository visibility was verified as public on 2026-10-02.
The agreed AWS trigger is **Run workflow** with a PR number, not automatic
Environment-reviewer approval. Workflow/protection settings are not configured yet.

```text
Intended flow: local CI passes -> owner manually requests PR -> shared lock -> clean data/deployment
  -> AWS API E2E -> required GitHub result -> merge eligible
Optional disposal: confirmation -> same lock -> delete owned testing stack
```

## Find the design details

| Topic | Spec section |
| --- | --- |
| Concise human overview and agreed decisions | Main design §1–§2 |
| Cleanup, validation, queueing, revision changes, and disposal examples | Main design §3 |
| Managed resources, automatic S3 state/locking, saved plans, and deployment identity | Detailed contract §4 |
| Manual request and required merge result | Detailed contract §5 |
| Shared locking and cancellation reconciliation | Detailed contract §6 |
| Reset, readiness, and isolated E2E | Detailed contract §7 |
| Credentials, roles, and runtime isolation | Detailed contract §8 |
| Initial account cleanup versus routine disposal | Detailed contract §9 |
| Failures/evidence and acceptance trials | Detailed contract §10–§11 |

## Configured resources and settings

Caller account was verified through STS using `dpm-profile`, explicitly in
`eu-north-1`; sanitized identity: account ending `6795`. No testing resources
have been created. The inventory below is a scoped observation, not proof that
every possible AWS service is empty.

**Agreed configuration, not yet provisioned:** Terraform; `eu-north-1` (Stockholm);
local AWS profile `dpm-profile`; GitHub OIDC; On-Demand EC2 without Spot. Minimize
practical cost without under-sizing the application/E2E stack; no numeric cap supplied.
Routine disposal retains protected Terraform state storage and bootstrap prerequisites.

Before provisioning, record the approved cost estimate, measured
capacity, ownership boundaries, and bootstrap inventory. After configuration,
record sanitized resource identities/settings and links to verification evidence.
Never record credentials or sensitive configuration values; use placeholders.

## Cleanup record

### Inventory evidence — 2026-10-04

Covered all 17 currently enabled/default regions: `ap-south-1`, `ca-central-1`,
`eu-central-1`, `us-west-1`, `us-west-2`, `eu-north-1`, `eu-west-3`, `eu-west-2`,
`eu-west-1`, `ap-northeast-3`, `ap-northeast-2`, `ap-northeast-1`, `sa-east-1`,
`ap-southeast-1`, `ap-southeast-2`, `us-east-1`, `us-east-2`.

The 739 listed regional/global queries used service pagination where available.
Recent 90-day Cost Explorer usage informed follow-up coverage for Glue, KMS,
SNS, and SQS. Query results and role/key metadata are in protected local storage
at `/tmp/opencode/aws-testing-inventory/` (directory `0700`, files `0600`), outside
Git. Preserve those files for owner review; this temporary path is not durable
remote audit storage.

| Covered resources | Observed result |
| --- | --- |
| EC2 instances, volumes, owned snapshots/AMIs, addresses, ENIs, NAT gateways, VPC endpoints | None returned |
| CloudFormation stacks, ECS/EKS, ECR, RDS instances/clusters/manual snapshots, ElastiCache, Lambda, both ELB APIs, EFS, DynamoDB, API Gateway v1/v2 | None returned |
| Secrets Manager metadata, SSM parameter metadata, CloudWatch log groups, CloudTrail trails, Glue databases/jobs/crawlers, SNS topics, SQS queues | None returned |
| Global S3 buckets, Route 53 hosted zones, CloudFront distributions, customer IAM policies, OIDC providers, instance profiles | None returned |
| Networks | 17 default VPCs and associated default network resources; one extra Stockholm security group |
| SSH key registrations | `DPM-test-key-pair` in Stockholm and `us-east-1`; private key material was not retrieved |
| IAM | One `admin` user, six AWS service-linked roles, one custom RDS monitoring role |
| KMS | Two AWS-managed Stockholm keys for RDS and Secrets Manager; preserve |
| Resource Explorer | Two existing local indexes: Stockholm and `us-east-1`; global listing repeated across clients, not 34 separate indexes |

**Gaps:** MSK listing failed in all 17 regions with `SubscriptionRequiredException`;
the service was not enabled to fill that gap. Other unlisted AWS services,
opt-in-disabled regions, deleted-resource histories, account/billing/organization
settings, and IAM credential/access internals are not exhaustively audited.
Cost Explorer is a discovery aid, not proof of resource absence. Initial cleanup
and routine managed-stack disposal remain separate operations.

### Authorized deletion/preservation list — executed 2026-10-04

| Resource | Proposed handling | Ownership/evidence |
| --- | --- | --- |
| Stockholm `launch-wizard-1`, `sg-08f329388aa888bf8` | Deleted; absence verified | Rechecked no ENIs/instances immediately before deletion |
| Stockholm SSH registration `key-0615e90fa708a0471` | Deleted; absence verified | Named `DPM-test-key-pair`; no instances immediately before deletion |
| `us-east-1` SSH registration `key-012d5a2838dce5c1b` | Deleted; absence verified | Same name; no instances immediately before deletion |
| Custom `rds-monitoring-role` | Monitoring policy detached; role deleted; absence verified | Rechecked monitoring-only trust/policy, no profiles/inline policies, and no RDS instances/clusters across all 17 regions |
| Default networks, `admin` user, service-linked roles, AWS-managed KMS keys, Resource Explorer indexes | Preserve baseline | Access/security/discovery or default-account resources; not assigned to the new environment |
| Account, billing, organization, account-level access/security settings | Preserve | Contract D2 |

The owner authorized the documented scope by instructing continuation without
further approvals. STS identity and dependencies were rechecked immediately
before mutation, and every deletion was read back. The covered inventory was
repeated after cleanup: 739 queries, with only the same 17 MSK subscription gaps.
The admin user and six service-linked roles remain. Protected receipts are at
`/tmp/opencode/aws-testing-inventory/{cleanup-receipt.json,after-cleanup/}`.

### GitHub candidate evidence

Observed existing PR CI run [36768627309](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/36768627309)
for PR #71: the run and its jobs report PR **head** `5d6f076…`, whereas the merged
candidate is `397ed28…`. Do not equate `workflow_run.head_sha` with checkout merge SHA.

The branch adds a separate credential-free `Candidate identity` CI job. It checks
the checkout and both merge parents against the triggering PR event and uploads
`ci-candidate-RUN_ID-ATTEMPT/candidate.json` for 14 days. The request controller
requires the latest successful CI run/attempt, all six named jobs successful,
and an unchanged CI workflow compared with pinned trusted controller code.
Candidate edits to the evidence producer are unsupported; archive content is
validated in memory without extraction or execution.

Local request stories pass, including unsupported PRs/actors/refs, rerun actor
authorization, changed identities, skipped CI jobs, duplicate attempts, unsafe
archives, mismatched/expired evidence, and token-safe failures. These stories
are not GitHub check-selection or branch-protection proof.

Hosted foundation PR [#72](https://github.com/Djimi/OnlineShop-full-stack/pull/72)
passed [PR CI run 37210137790](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/37210137790):
all six jobs succeeded. Artifact `ci-candidate-37210137790-1` (ID `11306321487`)
was read through the controller's inert archive reader; its repository/PR,
merge/head/base SHAs, and run/attempt matched the live API identities. The
downloaded archive SHA-256 matched GitHub's artifact digest. This verifies hosted
identity packaging, not trusted-main request/check or branch-protection behavior.

The report artifact (ID `11306400770`) also passed digest verification. XML showed:

| Suite | Executed | Failures/errors/skipped |
| --- | --- | --- |
| `com.onlineshop.e2e.ItemsE2ETest` | 3 API tests | 0/0/0 |
| `com.onlineshop.e2e.RestAssuredLoggingTest` | 1 logging-redaction regression | 0/0/0 |

Future report validation must recognize both suites: four total tests, including
three API journeys. Counting all XML tests as only three would reject valid runs.

### Trusted-main request trials

Disposable [PR #73](https://github.com/Djimi/OnlineShop-full-stack/pull/73) is not
intended to merge. Run [37211489404](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/37211489404)
rejected pending exact CI immediately, without AWS access or a successful
result. After CI passed, run
[37211810814](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/37211810814)
accepted merge candidate `12af6968ce9ab7d2c021810d309dca937c8b2ade` and created
GitHub Actions App check `111464385702`, external ID `aws-validation:37211810814:1`.
The candidate check completed as failure, not success. Artifact `11306916858`
retains the record. A separate synthetic context will exercise newest pending/
failure versus delayed old success; it never succeeds as **AWS validation**.

Run [37215526810](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/37215526810)
passed separate synthetic latest-check assertions: newest pending and failure
remained selected despite delayed older success. Actual **AWS validation** check
`111475163185` remained failure. This proves selection API behavior, not branch
protection or real AWS success. Artifact `11307989558` recorded only safe OIDC
claims, with actual subject:
`repo:Djimi@8793507/OnlineShop-full-stack@1097550215:environment:aws-testing`.
Do not substitute the legacy name-only subject. The Environment permits only
branch `main`, with no reviewer gate. Trust must match exact observed subject
and audience `sts.amazonaws.com` before role creation.

### Protected backend bootstrap

Terraform 1.16.5/provider 6.67.0 created only the five inspected S3 backend
resources, then native `init -migrate-state -force-copy` migrated creation state
to `state/backend.tfstate`. Private access, SSE-S3, TLS-only policy and versioning
were read back. Remote resources and authoritative `state pull` match the five
created identities. Native migration assigned a new lineage/serial; verify
resource identities and authoritative remote lineage rather than assume local
lineage is preserved. Owned local copies were removed after remote verification.
Protected identifiers/receipts remain outside Git in `infra/aws/.runtime/` and
`/tmp/opencode/aws-testing-inventory/`.

Bootstrap's exact inspected saved plan subsequently applied 19 resources:
one OIDC provider, three roles/policies, one host profile, five repositories and
five lifecycle policies, and one secret's metadata. Exact observed trust, private
repositories/immutable candidate tags, host profile and versioned bootstrap state
were read back. Dedicated credentials were generated in-memory and initialized
only through Secrets Manager, never Terraform/local files/output. No host exists.
Read-only Access Analyzer found no errors; its name-format recommendation does
not recognize the observed immutable-ID subject. Eleven custom-policy simulations
passed resource-specific launch and access-isolation cases; actual assumed-role
proofs remain pending. See [root contracts/recovery](../infra/aws/README.md).

### Packaging-only workflow increment

`.github/workflows/aws-validation.yml` is being implemented as a manual-only
packaging trial: request -> credential-free exact build -> isolated publisher
OIDC -> owned **failure-only** finalization. It does not provision/reset/test an
AWS host and must never satisfy the required AWS gate. Publication verifies the
trusted main run/attempt/job, GitHub artifact SHA-256, source manifest and archive
checksums before fixed ECR pushes; digest receipts are not AWS test evidence.
Only approved nonsensitive account/role/bucket/profile/secret identifiers are
configured as `aws-testing` Environment variables. No AWS keys are stored there.
Hosted publication, actual role boundaries, runtime/disposal and protection are
still pending; local publication stories do not prove those live properties.

Live packaging trial
[37218248383](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/37218248383)
then passed request/build/publisher/outcome. Publisher OIDC used the observed
subject successfully. Trusted build artifact `11309510499` and digest receipt
`11308474386` were verified; all five images were independently read back by digest
from ECR. The candidate's exact check completed as **failure**, as required for
packaging-only work. This does not prove operator permissions, runtime isolation,
reset, E2E in AWS, disposal, or branch protection. PR #75 is merged at `06cbb0c`.

Runtime implementation has started locally. Process-boundary stories exercise
generation/process locks, metadata-rule prerequisites, scoped reset, readiness,
executed report checks and sanitized failure evidence. Unknown test termination
is persisted as unknown and blocks a subsequent run; temporary credential files
are still removed. No runtime/Compose/reset/isolation story has yet been proved
on an AWS host; no host has been provisioned.

The actual **local** Compose reset story passed after adding Vite's missing
`.vite-temp` tmpfs: verified candidate digests -> gateway/frontend readiness ->
four E2E tests -> markers in both DBs/Redis/Kafka -> scoped volume recreation ->
markers absent -> four E2E tests again (85.921 seconds). Scoped cleanup completed.
This is not AWS-host evidence. Runtime also checks fixture transport hashes,
removed SQL and forwarding-chain attachment. Host setup/hooks are defined but
not applied on EC2. Generated DB credentials are wired; the current E2E journey
does not consume the provided generated registration-test password yet. See
[runtime contract](../infra/aws/runtime/AGENTS.md) for pending boundaries.

The separate manual `AWS role boundary proof` workflow uses
trusted-main Environment OIDC for publisher/operator and read-only live APIs,
requires exact authorization denials (not network/missing-resource errors), and
retains only operation labels. It cannot publish AWS success or prove mutation
permissions. Local command stories and hosted read-boundary proof pass.

PR #76 merged at `a612860` after hosted CI passed. Read-only OIDC role proof
[37221612286](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/37221612286)
then passed both roles; artifact digests `11310580872` (publisher) and `11310516124`
(operator) were independently verified. Publisher can read its fixed ECR images
but cannot discover hosts or read backend/bootstrap state or dedicated secrets.
Operator can discover hosts/read the exact profile/list the environment-state
prefix, but cannot read backend/bootstrap state, list the backend-state prefix,
read the host role or dedicated secrets. These are selected actual read boundaries,
not mutation/cancellation/host-runtime proof; no successful AWS result was published.

The generated registration-credential assertion initially failed with HTTP 401
against the old E2E image. Candidate E2E source now consumes `E2E_TEST_PASSWORD`
and uses a random per-journey fallback locally; its module-root wrapper passed.
The rebuilt-image/reset/login regression passed (92.635 seconds): generated
password login succeeded for the E2E-created user both before and after full
DB/cache/broker reset. The local image was resolved by immutable Docker image ID;
the other four app images retained their verified ECR digests. This fix has not
yet been republished through the trusted candidate workflow or run on AWS.

Generated-credential candidate publication
[37222500228](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/37222500228)
has since passed; source artifact `11310428701` and digest receipt `11311460203`
were verified, and all five ECR digests read back independently. Its exact
candidate check remains **failure**, not AWS test success. PR #77 merged at
`4a41b87`; newer local read-only build-directory changes are not in that receipt.

Two simultaneous owner Terraform plans against the fixed environment backend
proved native S3 exclusion: one planned nine approved resource creations and the
other failed acquiring the lock. Both terminated; the lock was released and no
environment state or host was created. Saved plans/logs stay protected locally in
`infra/aws/.runtime/`; they are never uploaded as artifacts. Saved-plan staleness,
drift/missing-state recovery and operator mutation proofs remain pending.

Local hardening now bounds command output at the kernel/file level and runs E2E
with a read-only root/bounded tmpfs. Actual integration exposed two configuration
defects: mounting `target` prevents normal Maven `clean`, and `go-offline` alone
missed its dynamically selected Surefire provider. The POM now supports a child
build directory and explicitly cached provider/launcher. Real read-only/offline
integration passed (85.825 seconds), including generated-password login, clean
DB/cache/Kafka state and four tests before/after reset. Report transport uses
bounded `docker exec tar`: Docker documents that `cp` cannot read tmpfs. Explicit
provider `dependency:get`, not `go-offline` alone, made offline read-only Maven
succeed. Fresh module-root `clean test` also passed; no EC2 host exists.

### Initial environment apply: account-plan blocker

PR #78 merged at `26ec8de` after hosted CI passed. The owner-authorized initial
environment apply then created eight network/template resources but failed at
EC2 `RunInstances`: `InvalidParameterCombination`, selected instance type not
eligible for Free Tier. Read-only `GetAccountPlanState` confirms `FREE`/`ACTIVE`.
No account-plan upgrade was performed. The original `c7i.xlarge` selection is
superseded by the Free-plan-compatible selection below.

Reconciliation verified eight persisted resources, matching remote resource/
output/lineage/serial identity, versioned environment state, empty ingress and a
released Terraform lock. No host, disk or ENI exists for the recorded generation.
The operation intent conservatively remains `unknown`; protected reconciliation
evidence records `blocked-account-plan`. No application or AWS success exists.
Resolve the capacity configuration before a refreshed, inspected apply; never
retry the original creation plan blindly. See [recovery](../infra/aws/README.md).

**Mandatory Free-plan constraint (owner correction):** never upgrade this account.
Read-only EC2 `DescribeInstanceTypes` with `free-tier-eligible=true` and
`DescribeInstanceTypeOfferings` verified `m7i-flex.large` in `eu-north-1a`.
Select it: two x86_64 vCPUs and 8 GiB RAM preserve image compatibility and memory
headroom. Other offered eligible x86_64 options are `c7i-flex.large` (4 GiB),
`t3.small` (2 GiB) and `t3.micro` (1 GiB); the eligible T4g options require ARM
images. Prefer the 8 GiB option for the full stack, not a tiny instance chosen
merely for eligibility. Two vCPUs may lengthen the measured CPU-intensive E2E;
real capacity/timing proof remains required. Catalog eligibility/offering is not
proof of a successful launch or unlimited zero-cost usage: Free-plan resources
can consume account credits. Terraform/IAM/test updates and a fresh inspected
partial-state plan are still pending; no host has launched.

### Free-plan host launch: verified infrastructure only

PR #79 merged at `79ff571` after hosted CI. Both host/type-policy assertions were
observed RED against the old selection, then GREEN; 79 automation stories passed.
An inspected bootstrap plan changed only the operator launch type condition;
actual IAM policy was read back. After partial-state reconciliation, a refreshed
environment plan created only the missing host; it preserved all eight prior
resources. Actual `m7i-flex.large`, pinned AMI/profile, On-Demand mode, IMDSv2/hop
limit 1, empty ingress and encrypted 50 GiB delete-on-termination gp3 were verified.
`GetAccountPlanState` still reports `FREE`; no upgrade occurred.

Protected foundation intent is `provisioned`, not application validation. SSM
reports the managed host online. Owner-only bounded setup command is being tested
and run; no application/E2E/isolation/reboot/cancellation/disposal proof exists yet.
Raw host/state/operation identifiers stay in ignored `infra/aws/.runtime/`.

The actual initial setup transferred/verified all trusted files, then failed in
AL2023 dependency installation: the pinned image already supplies `curl-minimal`,
which conflicts with installing full `curl`. Setup now requests `curl-minimal`,
preserving that package rather than using broad `--allowerasing`/`--skip-broken`.
The failed command is terminal; its operation remains `unknown` pending owner
reconciliation. No candidate application has started.

Owner reconciliation subsequently verified all preceding transfers successful
and all recorded SSM commands terminal. Corrected trusted setup from merged
PR #80 (`60d37d6`) completed successfully under bounded SSM/flock. The original
failure record remains retained; a separate protected recovery record is
`Success`. Host prerequisites are installed, not application deployment.

Live owner proofs now detect a temporary managed-VPC tag drift and reject an old
saved plan after a refresh-only state update (`Saved plan is stale`). Fresh
inspected tag-only restoration removed the probe; no host was replaced. An
initial proof parser assumed no-op JSON always contained `resource_changes`;
its partial drift was reconciled before the corrected proof. Raw logs/plans
remain protected. These proofs do not prove routine-role mutation or full-session
workflow locking.

Packaging trial `37237605326` built successfully but publication rejected its
now-unsupported candidate while trusted main advanced. Failure-only finalization
ran; no AWS success was published. Trial #73 was refreshed with trusted main
again; a new exact-CI/manual publication is required, not reuse of that attempt.

Actual Auth and E2E digest-image probes passed on the Free-plan EC2 host: neither
obtained IPv4/IPv6 IMDS tokens, saw job/cloud credential variables or had the Docker
socket mounted. Required IPv4/IPv6 firewall/FORWARD rules survived Docker restart.
Host-role ECR login/pull and named-secret access still succeeded. A real EC2 reboot
was then verified by changed boot identity; Docker/SSM and firewall hooks returned,
host ECR/secret access worked and both images still failed metadata-token probes.
Private auth/probe containers/network were scoped and removed. These are selected
image-isolation proofs, not full application runtime/E2E, all-image probes, remote
cancellation recovery or trustworthy merge-gate success.

Actual cancellation proof also passed: a bounded SSM command launched an isolated
detached test container, then was cancelled. A new trusted runtime invocation was
blocked by its process lock/unknown-operation record rather than resetting data.
Owner reconciliation removed the exact test container, verified absence and lock
release, then wrote a terminal cancellation record. This proves the runtime guard
and documented owner recovery, not an implemented automatic workflow reconciler.

Current Stockholm Price List compute equivalent for `m7i-flex.large` is
**USD 0.10175/hour**. Reusing the recorded 730-hour/month, 50 GiB gp3, public IPv4,
50 GiB ECR/1 GiB S3/secret assumptions gives **USD 87.53/month live equivalent**
and **USD 5.42/month retained/disposed equivalent**, excluding traffic/requests/
taxes. On this unchanged Free plan these are pricing/credit-consumption estimates,
not a paid-account bill or a guarantee of unlimited zero-cost use.

### Generation lifecycle correction: not applied yet

A refreshed generation plan unexpectedly replaces the EC2 host because changing
launch-time generation tags produces a new launch-template version. That plan was
not applied. The environment definition now separates stable launch ownership
from mutable attempt tags; an explicit primary-interface tag tracks generation.
Assertions observed RED on dynamic launch tags, then GREEN after correction.
The initial migration requires one reviewed replacement of the empty host;
subsequent generation-tag changes must not replace it. No lifecycle drift-ignore
or account-plan upgrade is introduced. Live migration/future-plan proof is pending.

PR #81 merged at `9679efc` after hosted CI. The inspected migration replaced only
the empty host, preserved network identities and verified removal of old disk/
interface. Ten environment resources and the new host/disk/metadata/interface
generation were read back; Free plan unchanged. Trusted setup was reinstalled.
The subsequent hypothetical generation plan still proposed replacement because
even template **resource** tag changes make computed `latest_version` unknown.
That plan was not applied. A second RED-to-GREEN assertion now requires stable
resource tags too; desired-state tag alignment/live no-churn proof remains pending.

Publication `37238396197` succeeded before that integration; artifact
`11316557973` (build) and `11316623025` (receipt) checksums, five ECR digests and
fixture identity were independently verified. Inert bounded SSM fixture transport
passed. Five incoming images have verified protected active-prefix tags. The
actual host role denied protected state, EC2 discovery and unrelated-secret reads.
An owner full-runtime proof using that historical published candidate is now
running on the migrated Free-plan host. It cannot publish or substitute for a
current-candidate AWS result: main has since advanced. No successful gate claimed.

That owner runtime proof passed reset/readiness and all four report-backed tests.
A second actual AWS reset seeded both databases, Redis and Kafka; the reset
removed the old markers/topic and all four tests passed again. Sanitized XML was
retrieved independently; test container and temporary secret/auth files were
verified absent. The app is retained. These historical-candidate owner proofs do
not satisfy current-candidate identity, full workflow recovery or the final gate.

PR #82 merged at `7c22ba0` after all applicable hosted checks passed. Guarded owner
recovery removed only the template-resource `Generation` tag; template version
remained unchanged. Current-generation saved plan/apply had no resource changes.
A fresh hypothetical next-attempt plan had updates only, no host/disk/interface
replacement; it was not applied. The running app and Free plan were preserved.

Current-host all-five-candidate metadata transport probes and eight-service
user/capability/mount/cloud-credential/loopback checks passed. Initial probe
assertions failed because frontend IPv6 has no source address (`EADDRNOTAVAIL`)
and Kafka's backing-service root is writable. Those observations are explicit:
candidate roots remain read-only; IPv6 transport unavailability is not proof of
a routed firewall rejection. The corrected probe requires known transport errors,
not arbitrary tooling failure. Protected failure/diagnostic/retry records remain.

An additional actual reset/readiness/E2E capacity run passed in **74.859 seconds**.
Fourteen periodic samples saw up to **2780.55 MiB** host RAM used, at least
**5003.09 MiB** available and **45.28 GiB** disk available, with nine simultaneous
containers during E2E. All eight retained service containers remained running
without Docker OOM flags; E2E was removed afterward. These are sampled observations,
not absolute peaks or sustained-load guarantees. Summed Docker CPU samples reached
284.57%; container sampling intervals are not aligned, so this is not a reliable
whole-host CPU peak or percentage-of-two-vCPU claim. Protected capacity summary
and host sample record are retained; sustained CPU/capacity acceptance remains
distinct from this successful learning-workload runtime proof.

### Owner setup launch-gap reconciliation

`scripts/aws-host-setup.py --reconcile` adds read-only discovery of exact recorded
setup commands without sending/cancelling operations or rewriting protected cloud
records. It size-checks the fixed versioned setup key before conditional download,
rejects duplicate JSON fields, validates generation/host, then discovers a unique
stage comment and verifies document/target/body hash/timeout/terminal invocation.
Absent/ambiguous/active/cancelling or mismatched evidence refuses. The create-only
report explicitly forbids automatic retry and AWS success; reconciliation is not
proof that every setup stage ran or that detached runtime processes stopped.

Live current-host reconciliation verified all 16 recorded commands. A controlled
crash-gap trial versioned only the already-terminal setup record with its final
ID removed; real SSM discovery recovered the exact ID and terminal invocation.
Original bytes were restored under ETag CAS, preserving original/gap/restore
versions. No host/app/Terraform state/generation changed. The bounded-read variant
also passed an actual read-only reconciliation. This is owner setup recovery,
not implemented routine validation/disposal or a current-candidate AWS result.

### Open merge-gate provenance issue

GitHub's expected status-check source binds an **App**, not a workflow. The shared
GitHub Actions App does not distinguish trusted-main AWS validation from another
same-repository workflow requesting `checks: write`. This public/personal
repository's read-only default token setting is not a workflow-specific identity.
Current controller authorization/CI-workflow comparison protect our code path,
not arbitrary check writers. Do not enable or claim a trustworthy final AWS gate
until this is resolved/proved. No alternative App/secret/migration was created.
See [expected-source semantics](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
and [permission overrides](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions).

### Capacity and pricing — before provisioning

**Historical paid-capacity comparison, superseded:** the owner now requires the
Free plan and selects eligible `m7i-flex.large` as described above. The following
`c7i.xlarge` rate/total is not a current estimate for the replacement host.

Local Compose plus containerized E2E passed with Auth's existing 100-connection
pool. A brief cached E2E measurement (two samples) observed combined peak
1,753 MiB RAM and 259.33% CPU; this is not worst-case stress-test evidence.
Select On-Demand `c7i.xlarge`, four x86_64 vCPUs and 8 GiB RAM, to cover observed
CPU demand with substantial startup/memory margin, without burstable credits.
Pin AL2023 AMI `ami-04478a3e21a0d79a7` (2023.12.20260930.0, kernel 6.1) and a
50 GiB encrypted delete-on-termination gp3 disk. Stockholm public pricing was
queried on 2026-10-04 through Price List's required global `us-east-1` endpoint.
Service codes must be discovered, not inferred from Cost Explorer labels;
standard private ECR is `AmazonECR` and excludes its separate archive tiers.

| Item | Stockholm USD rate |
| --- | --- |
| `m7i.large`, 2 cores/8 GiB | 0.1071/hour; below observed CPU demand |
| `m7i.xlarge`, 4 cores/16 GiB | 0.2142/hour; unnecessary extra memory |
| Selected `c7i.xlarge`, 4 cores/8 GiB | 0.1911/hour |
| Public IPv4 | 0.005/hour |
| gp3 | 0.0836/GiB-month; 50 GiB = 4.18/month |
| Standard ECR | 0.10/GiB-month |
| S3 Standard | 0.023/GiB-month plus requests |
| Dedicated secret | 0.40/month plus 0.05/10,000 API requests |

Decimal calculations at 730 hours/month, assuming 50 GiB retained ECR and 1 GiB
S3: **live 0.2093/hour or 152.76/month; disposed 5.42/month**. Requests, traffic,
taxes and excess retained storage are additional. Selection proceeds under the
owner's delegated no-further-approval instruction. No Spot, commitments, NAT,
load balancer or managed database is introduced. Bootstrap/state survive disposal.

For CI retries, rerun **all jobs** so the new attempt has both candidate evidence
and successful required jobs. A partial failed-job rerun without fresh complete
attempt evidence is rejected; older artifacts do not satisfy a new attempt.

## Operational flows

Reset commands, inspection prerequisites/port forwarding, disposal inputs,
recreation behavior, and failure-recovery commands are not configured yet.
Populate this section from verified implementation, not assumptions in the draft.

After this foundation is reviewed and reaches trusted `main`, use **Actions ->
AWS candidate check proof -> Run workflow** on `main`, entering a reviewed PR
whose exact candidate has passed the new CI. The harness creates a pending
**AWS validation** check against that candidate and completes it as **failure**
because it has not deployed or run AWS E2E. It has no OIDC/AWS permissions.
The first harness proved pending/failure association; later synthetic latest-
attempt/delayed-old-completion tests also passed. Actual successful AWS-result
selection and real merge enforcement remain unproved. Do not enable the gate yet.

## Incremental delivery checklist

- [x] Record agreed requirements and verify repository visibility/approval support.
- [x] Draft progressively disclosed design and concrete acceptance scenarios.
- [x] Record Terraform, explicit manual execution, profile/region, and cost/reliability choices.
- [x] Owner requests implementation planning from the revised spec.
- [x] Write the implementation plan with requirement coverage and issues.
- [x] Owner authorizes implementation and selects inline execution (2026-10-04).
- [x] Scope inventory and review cleanup targets; explicit MSK subscription gaps remain.
- [x] Clean authorized playground resources and record the preserved baseline.
- [ ] Document managed resources, bootstrap prerequisites, settings, and costs here.
- [ ] Provision environment and verify reset, isolation, and recreation.
- [ ] Automate authorized manual request, publication, deployment, locking, E2E, and evidence.
- [ ] Verify and configure required merge checks, including negative/stale cases.
- [ ] Automate confirmed disposal and verify its ownership/race protections.
- [ ] Record hosted acceptance evidence and operational/recovery flows.
