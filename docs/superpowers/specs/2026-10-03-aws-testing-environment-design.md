# Manually Started AWS PR Validation

**Status:** Owner authorized inline execution of the [implementation plan](../../../planning/aws-testing-environment-PLAN.md)
on 2026-10-04 and explicitly delegated continuation without further approvals.
Verified cleanup and measured cost/capacity choices are recorded in the runbook;
resource/security constraints and live acceptance proofs remain binding.
Read §1–§3 for the solution. Topic links lead to the [implementation contract](./2026-10-03-aws-testing-environment-requirements.md),
which agents must also read. Actual settings/commands belong in [TESTING-ENVIRONMENT.md](../../TESTING-ENVIRONMENT.md).

## 1. Technical overview

Attempt replacement normally resets owned application data on the existing host.
Keep launch-template contents and resource tags independent of mutable attempt identity; Terraform
updates generation tags on existing infrastructure, including its primary
interface. Host replacement is reserved for inspected real configuration/lifecycle
changes, not an incidental new launch-template version on each validation.

**Owner constraint correction:** the AWS account MUST remain on the Free plan;
paid-plan upgrades are prohibited. The original `c7i.xlarge` capacity selection
is superseded by catalog-verified eligible `m7i-flex.large` in `eu-north-1a`
(two x86_64 vCPUs, 8 GiB). Preserve existing topology and image architecture;
update provisioning/IAM boundaries before retry and prove full-stack timing on
the actual host. No automatic downgrade to inadequate capacity or skipped tests.

**Accepted trust model (owner decision, 2026-10-05):** Authorized repository
maintainers and reviewed workflow definitions are trusted; workflow changes require
review. Normal GitHub Actions App identity and scoped `checks: write` are accepted,
without a dedicated App. Exact candidate binding, manual authorization,
latest-attempt selection and failure enforcement still require live proof.
Hosted runtime/disposal acceptance and final gate activation remain unfinished; see
[operational status](../../TESTING-ENVIRONMENT.md).

### 1.1 Goal and end-to-end flow

**DECIDED:** A PR must deploy and pass API E2E in AWS before merging. E2E reuses
our gateway/Auth/Items tests; it does not add browser coverage.

Local CI remains automatic. AWS starts only when the owner selects
**Actions -> AWS validation -> Run workflow**, chooses trusted workflow `main`,
and enters the PR number. No second approval click is required.

```text
Push/update PR -> automatic local CI -> no automatic AWS run

Owner requests AWS validation
  -> pin PR + current main (the tested candidate) -> verify its local CI passed
  -> build images without AWS credentials -> authenticate/publish privately
  -> wait for shared lock -> create if absent -> reset -> deploy -> readiness
  -> API E2E -> save evidence -> required "AWS validation" result -> unlock
```

Success makes merging eligible only if other required checks pass. Missing/failed
AWS validation blocks merge. New commits require another manual run, not automatic AWS tests.
Details: [§5 Revision and required result](./2026-10-03-aws-testing-environment-requirements.md#5-revision-manual-request-and-required-result-contract).

### 1.2 Application and network

One **EC2** host (an AWS virtual machine) runs the shared testing environment.
**Docker Compose** starts our containers and backing services, reusing the local stack:

```text
API Gateway -> Auth  -> Auth PostgreSQL
            -> Items -> Items PostgreSQL
frontend + Redis + Kafka + short-lived, isolated API E2E runner
```

The runner is test packaging, not a business service. `common` remains a library;
pgAdmin/Kafka UI are unnecessary here. Application/test containers have no AWS credentials.

Our **VPC** is a dedicated AWS network. Its **security group** is a firewall allowing
outbound connectivity but no incoming app/database/broker/SSH connections; there is no public URL.
Details: [§4 Resources](./2026-10-03-aws-testing-environment-requirements.md#4-implementation-contract-resources-and-identities)
and [§7 Reset/readiness/E2E](./2026-10-03-aws-testing-environment-requirements.md#7-clean-state-application-and-e2e-contract).

### 1.3 Terraform, state, and ownership

**Terraform** manages the machine, disk, and network from versioned definitions.
We choose it for transferable tooling skills; AWS-specific definitions remain AWS-specific.
Its **state** maps those definitions to actual resource IDs, rather than discovering ownership by name.

```text
Git repo: desired infrastructure -> Terraform state: managed IDs -> AWS: actual resources
```

Configure the **S3 backend** (state-storage integration) once: Terraform automatically stores state in S3 (AWS
object storage), with private access, encryption, version history, and native locking.
We do not manually edit/upload state or put it in Git. Names/paths are chosen in the plan.

```text
Retained bootstrap                   Disposable environment
  state bucket + access roles          machine + disk + network
  image storage + test secrets         application + all test data
```

Bootstrap means recreation prerequisites. Separate configurations/state protect it
from routine disposal. Terraform owns infrastructure settings; deployment commands
own containers/test data. CLI/console inspection is fine; emergency infrastructure
changes require reconciliation to avoid Terraform reverting intentional changes.
Details: [§4.3 State, saved plans, and recovery](./2026-10-03-aws-testing-environment-requirements.md#43-terraform-state-and-ownership).

### 1.4 Access and trusted code

**OIDC** lets GitHub obtain temporary AWS credentials. An **IAM role** limits their
permissions to testing resources; no permanent AWS keys are stored in GitHub.
A **GitHub Environment** is a named deployment target; ours restricts AWS jobs to
trusted `main` code, without an automatic approval queue.

**SSM (AWS Systems Manager)** runs authenticated commands without opening SSH.
Its port forwarding lets the owner inspect the private gateway/frontend locally.
**ECR** privately stores images. Deploy by digest (a fixed content identifier),
not a tag that could point to different code later.

PR code is built/tested separately from controller credentials. Infrastructure,
permissions, and runtime definitions come from trusted `main`, not the PR being tested.
Details: [§8 Security boundaries](./2026-10-03-aws-testing-environment-requirements.md#8-security-boundaries).

### 1.5 Locks, reset, and disposal

GitHub gives deployment/disposal sessions one shared lock; another request waits:

```text
A: lock -> reset -> deploy -> E2E -> evidence -> unlock
B:         waits ................................ -> lock -> ...
```

Terraform separately locks each infrastructure operation. That does not protect
E2E or reserve the plan/apply gap: our GitHub lock spans the whole session.
Terraform releases its lock normally; a crash can leave one requiring reviewed recovery.

Reset stops old writers and removes only dedicated DB/cache/broker/application data,
then initializes fresh state. Services keep independent schemas; the machine can be reused.
After validation, inspection is available but unreserved: the next run may replace it.

**Dispose testing environment -> Run workflow** takes a deployment ID and confirmation.
It shares the lock and refuses deletion if a newer deployment replaced the selected one.
Terraform removes disposable resources; bootstrap/state survive for automatic recreation.
Details: [§6 Races/recovery](./2026-10-03-aws-testing-environment-requirements.md#6-concurrency-and-race-conditions)
and [§9 Cleanup/disposal](./2026-10-03-aws-testing-environment-requirements.md#9-account-cleanup-inspection-and-disposal-boundaries).

### 1.6 Cost, trade-offs, and changes

Use local `dpm-profile` and `eu-north-1` (Stockholm); CI uses OIDC. Confirm the account
before cleanup. Minimize practical cost with measured capacity and bounded storage retention.
Use **On-Demand EC2**, never Spot: AWS may reclaim Spot capacity, independently of our traffic.
On-Demand avoids reclamation, not overload/memory exhaustion/outages. Estimate live and retained costs before provisioning;
no numeric cap was supplied. Disposal reduces running costs but retained storage still costs money.

| Choice | Benefit | Trade-off |
| --- | --- | --- |
| EC2 + Compose | Reuses current stack | Host maintenance; one failure domain; less production fidelity |
| Terraform | Transferable tooling | State protection/recovery responsibility |
| Shared/private environment | Small footprint; no public ingress | Serial testing; inspection tunnels |
| On-Demand + manual disposal | No Spot reclamation; inspection afterward | Charges continue until disposal |

We add Terraform, two manual workflows, image publication, and the required AWS result.
Keep local CI/service boundaries and today's development-mode frontend. Only reviewed
same-repository PRs deploy. Merge queues, forks, automatic expiry, per-PR environments,
browser E2E, and production/high-availability rollout are deferred. No unnecessary
managed services, NAT gateways, or load balancers.

## 2. Decisions and review boundary

**DECIDED:** The architecture and behavior above are agreed. One-time old-playground
cleanup is included before new provisioning, with account/settings/access preserved.

**IMPLEMENTATION DETAIL:** Resource/bucket names, host size, packaging, and policy
syntax are chosen in the implementation plan within the contract. Include capacity
evidence and running/retained cost estimates; record actual settings in the runbook.

**DECIDED:** The owner selected inline execution on 2026-10-04. Material
architecture/security/cost changes return for review. Inventory findings and
cost/capacity measurements retain their explicit review boundaries.

## 3. Concrete walkthroughs

Illustrative examples—not observed inventory or existing workflow evidence:

### 3.1 Old-playground cleanup

`dpm-profile` inventory finds an old machine, snapshot, and unfamiliar role. Review
the role and deletion list; cover other regions/global resources and flag gaps.
Preserve account settings/access, verify deletion, then establish bootstrap.
Details: [§9.1 Cleanup](./2026-10-03-aws-testing-environment-requirements.md#91-one-time-playground-cleanup).

### 3.2 PR #42 passes

Local CI passes without AWS execution. The owner requests PR `42` on workflow `main`.
Its pinned candidate is built, published, reset/deployed under lock, and E2E-tested.
Evidence and required success attach to that candidate; merging becomes eligible.

### 3.3 PR #43 waits; #42 fails

#43 is requested during #42's E2E and waits without replacing its code/data. #42 fails,
blocking merge; reports are saved before unlock. #43 then resets/deploys. Inspection
of #42 is possible only until replacement. Details: [§10 Failures](./2026-10-03-aws-testing-environment-requirements.md#10-failures-evidence-and-edge-cases).

### 3.4 New code invalidates old validation

A commit after #42's success needs fresh local CI and another manual AWS request.
A changed/closed queued PR stops without deployment. If `main` advances, update the
outdated PR and validate its new candidate; no merge queue does that automatically.

### 3.5 Inspect, dispose, recreate

Inspect `run-42-1` through SSM and request its disposal. Bootstrap/state stay; the
next validation recreates clean resources. If #43 replaced it while disposal waited,
the ID mismatch refuses deletion rather than destroying #43.

### 3.6 Cancellation or stale plan

#42 is canceled during a remote operation: #43 must prove it ended before proceeding;
otherwise it stops. A stranded lock is not automatically removed. If state changed
after a saved Terraform plan, applying it fails as stale, without silently replanning.
Console changes may not change state; fresh plans inspect AWS, and manual mutations
must be coordinated. Details: [§4.3](./2026-10-03-aws-testing-environment-requirements.md#43-terraform-state-and-ownership)
and [§6](./2026-10-03-aws-testing-environment-requirements.md#6-concurrency-and-race-conditions).

**Next:** Review [acceptance scenarios](./2026-10-03-aws-testing-environment-requirements.md#11-acceptance-scenarios)
or read the full linked contract for implementation.
