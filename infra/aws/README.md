# AWS infrastructure boundaries

```text
Owner: backend -> protected state storage -> bootstrap -> OIDC/roles/images/secret metadata
Routine operator: fixed environment root -> dedicated network/host/disk
Disposal: environment only -> backend/bootstrap and evidence remain
```

**Implementation in progress:** backend is created/migrated and bootstrap's 19
inspected resources are applied. Dedicated generated credentials were initialized
via protected Secrets Manager API, never Terraform. No application host exists.
Do not treat mock tests as proof of real IAM permissions, locking or isolation.
See [operational evidence](../../docs/TESTING-ENVIRONMENT.md) and the
[implementation plan](../../planning/aws-testing-environment-PLAN.md).

## Fixed inputs and storage

| Root | Remote key | Caller |
| --- | --- | --- |
| `backend/` | `state/backend.tfstate` | Owner only |
| `bootstrap/` | `state/bootstrap.tfstate` | Owner only |
| `environment/` | `state/environment.tfstate` | Locked routine operator |

Terraform is pinned to 1.16.5 and AWS provider to 6.67.0. All roots explicitly
select Stockholm and reject accounts other than their verified `account_id`
input. Native S3 locking is enabled; there is no DynamoDB lock table. Use only
the default workspace. Routine access cannot read bootstrap/backend state,
delete environment state, or access state versions.

Local calls use `AWS_PROFILE=dpm-profile`, `AWS_REGION=eu-north-1` and
`AWS_DEFAULT_REGION=eu-north-1`; reverify STS account before any mutation.
Protected local identifiers live in ignored `.runtime/bootstrap-identifiers.json`.
Do not infer ownership from a matching bucket/resource name. Do not print raw
state, plans, secrets, environment dumps or credentials.

The disposable host is On-Demand `c7i.xlarge`, pinned x86_64 AL2023 AMI
`ami-04478a3e21a0d79a7`, 50 GiB encrypted gp3 deleted on termination. No security
group ingress, SSH key, NAT, load balancer or managed DB exists in its definition.
Outbound-only public IPv4 is intentional; inspection will use SSM. IMDSv2/hop
limit alone is not container isolation; runtime firewall/probes are still pending.

## Verify definitions without AWS mutation

Run from the repository root, separately for each root:

```bash
terraform fmt -check -recursive infra/aws
terraform -chdir=infra/aws/backend init -backend=false
terraform -chdir=infra/aws/backend validate
terraform -chdir=infra/aws/backend test
terraform -chdir=infra/aws/bootstrap init -backend=false
terraform -chdir=infra/aws/bootstrap validate
terraform -chdir=infra/aws/bootstrap test
terraform -chdir=infra/aws/environment init -backend=false
terraform -chdir=infra/aws/environment validate
terraform -chdir=infra/aws/environment test
```

Never initialize a live backend implicitly with an inferred bucket. An already
migrated working directory retains its backend even with `-backend=false`;
mock-provider tests use isolated test state and never administer the live stack.

## Owner backend creation and migration

```text
verify account and absent intended bucket/key
 -> protected local creation state (umask 077)
 -> saved plan -> inspect only expected backend changes -> apply exact plan
 -> read back encryption/versioning/private access/TLS policy
 -> add fixed backend config -> native init -migrate-state -force-copy
 -> verify remote version/resources and authoritative state pull
 -> remove only owned temporary local state copies
```

The initial bucket name is derived from verified account, region and repository
hash and then recorded, not reused by guess. Native migration may assign a new
lineage/serial: compare every resource identity and the authoritative remote
state, not merely the original local lineage. Never manually upload edited state
to make migration appear successful. Creation and migration are already verified;
do not repeat them or overwrite the remote key.

## Bootstrap trust and privileges

The GitHub Environment `aws-testing` permits branch `main` only and has no
reviewer gate. Observed audience is `sts.amazonaws.com`; exact observed subject is
`repo:Djimi@8793507/OnlineShop-full-stack@1097550215:environment:aws-testing`.
The immutable IDs are required; a legacy name-only subject will not match.

Publisher can push only five fixed repositories, not operate the environment.
Operator can use only the exact host role/profile and environment state/lock
plus operation records; host can pull fixed images, read the named dedicated
secret and use SSM channels. Broad discovery is read-only. IAM policy support
and real denied/allowed operations must be verified before routine deployment.
RunInstances conditions are resource-specific: `ec2:InstanceType` applies only
to its instance authorization, not volume/interface authorizations. Required
conditions on unsupported/missing keys can deny an otherwise valid deployment;
split statements by supported resource conditions and test each boundary.
Subnet/route-table/security-group creation also separately authorizes the
existing VPC via its ownership resource tag; new-resource request tags are not
available on that existing parent-resource authorization.
Generated secret values must be initialized through protected runtime APIs,
never Terraform resources/outputs/user-data or command-line values.

ECR candidate tags are immutable; only `active-*` retention pointers are mutable.
A highest-priority active-tag rule protects a bounded previous/incoming/current
set from lower-priority candidate/untagged expiration. Moving and releasing
those pointers belongs to the locked runtime controller, not publisher jobs.
That controller and live retention proof are not yet implemented.

Read-only Access Analyzer validation found no policy errors. Its GitHub-specific
recommendation currently does not recognize the observed immutable-ID subject;
do not weaken exact trust to silence that warning. Eleven custom-policy
simulations passed launch-resource and isolation scenarios. These are not actual
OIDC assumed-role deny proofs; live role tests still precede routine deployment.

The [trusted runtime](runtime/AGENTS.md) has process-boundary coverage and an
actual local Compose reset proof. Host setup/hooks are defined, not applied on
EC2. SSM orchestration, host isolation and final merge gate remain unfinished.
The `AWS role boundary proof` workflow separately verifies selected
publisher/operator allowed and denied reads using actual OIDC credentials.
It never mutates AWS or reads data into retained artifacts; only operation labels
survive; actual selected read boundaries are verified below.

Live run 37221612286 passed selected publisher/operator OIDC allowed/denied read
boundaries, with verified artifact hashes. Both roles were denied backend/bootstrap
state and dedicated secrets. Operator's fixed-profile/environment-prefix discovery
and publisher's ECR discovery passed. This does not prove mutation permissions,
Terraform locks, pass-role denies or container/SSM isolation.

Native S3 lock contention is now verified with two concurrent owner plans on the
fixed environment root/key: one acquired the lock and inspected nine expected
creations; the other was blocked. The completed plans released the lock and did
not create state or resources. This does not prove operator mutation permissions,
stale-plan rejection or real host deployment. Keep saved plans protected locally;
do not reuse proof-run plans as candidate deployment plans.

## Saved plans and recovery

Every mutation uses the same trusted root, recorded backend/inputs and provider
lock: refreshed saved plan -> inspect allowed resource actions -> apply **that
file**. Keep plan/state files mode 0600 in `.runtime/`; never upload them as GitHub
artifacts. State changes invalidate saved plans; fail rather than silently replan.
Fresh refresh-enabled planning detects AWS drift not visible in state serial.

```text
Interrupted operation -> freeze new mutations -> identify GitHub/SSM/Terraform work
 -> prove terminal operation/host state -> compare recorded IDs and AWS/state
 -> reviewed recovery -> fresh manual validation (never synthetic success)
```

- **Stale lock:** inspect exact lock identity privately. Check all relevant jobs,
  remote commands and processes; age/cancellation alone proves nothing. Only after
  owner-reviewed proof of inactivity may `terraform force-unlock <verified-lock-id>`
  run against the fixed root/backend. Never automatically delete `.tflock` or use
  `-lock=false`.
- **Missing/corrupt state:** stop even if the host looks absent. Compare retained
  state versions, inventory and operation IDs. Recover a verified intact version
  through owner-only Terraform state recovery, or reviewed imports against proven
  resource ownership. Never replace it with empty state or create duplicates.
- **Partial apply/destroy:** keep operation/generation evidence; refresh and
  reconcile recorded identities. Apply only an inspected new recovery plan, then
  verify actual resource presence/deletion, not just an API exit code.
- **Manual drift:** coordinate intervention, record it, and align desired config
  and state before routine runs resume. No automatic targeted destroy/account sweep.

These are recovery constraints, not yet a fully verified automated runbook.
Live lock contention, stale-plan, role-denial and recreation trials remain pending.
