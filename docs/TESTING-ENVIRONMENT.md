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
`feat/aws-testing-environment`. Cleanup-list review and live GitHub association
proof block completion of Tasks 1–2. No AWS deletion, provisioning, deployment,
or branch-protection change has been performed.

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

### Explicit proposed deletion/preservation list — awaiting review

| Resource | Proposed handling | Ownership/evidence |
| --- | --- | --- |
| Stockholm `launch-wizard-1`, `sg-08f329388aa888bf8` | Delete after list review | No ENIs/instances returned; permits public HTTP/SSH; no testing stack depends on it |
| Stockholm SSH registration `key-0615e90fa708a0471` | Clarify access ownership before deletion | Named `DPM-test-key-pair`; no instances returned |
| `us-east-1` SSH registration `key-012d5a2838dce5c1b` | Clarify access ownership before deletion | Same name; no instances returned |
| Custom `rds-monitoring-role` | Clarify before deletion | Trusts `monitoring.rds.amazonaws.com`, attached `AmazonRDSEnhancedMonitoringRole`; no covered RDS instances returned |
| Default networks, `admin` user, service-linked roles, AWS-managed KMS keys, Resource Explorer indexes | Preserve baseline | Access/security/discovery or default-account resources; not assigned to the new environment |
| Account, billing, organization, account-level access/security settings | Preserve | Contract D2 |

No deletion list has been approved or executed yet. Re-inventory approved
resources immediately before deletion and verify absence afterward. Task 1
remains incomplete until that evidence exists.

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
This first harness proves only pending/failure association; newest-attempt,
delayed-old-completion, positive-result selection, and real merge enforcement
remain unproved. Do not enable the required AWS gate yet.

## Incremental delivery checklist

- [x] Record agreed requirements and verify repository visibility/approval support.
- [x] Draft progressively disclosed design and concrete acceptance scenarios.
- [x] Record Terraform, explicit manual execution, profile/region, and cost/reliability choices.
- [x] Owner requests implementation planning from the revised spec.
- [x] Write the implementation plan with requirement coverage and issues.
- [x] Owner authorizes implementation and selects inline execution (2026-10-04).
- [ ] Inventory AWS and review suspicious resources before cleanup.
- [ ] Clean previous playground resources and record the remaining baseline.
- [ ] Document managed resources, bootstrap prerequisites, settings, and costs here.
- [ ] Provision environment and verify reset, isolation, and recreation.
- [ ] Automate authorized manual request, publication, deployment, locking, E2E, and evidence.
- [ ] Verify and configure required merge checks, including negative/stale cases.
- [ ] Automate confirmed disposal and verify its ownership/race protections.
- [ ] Record hosted acceptance evidence and operational/recovery flows.
