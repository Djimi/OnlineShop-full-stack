# AWS infrastructure boundaries

```text
Owner: backend -> protected state storage -> bootstrap -> OIDC/roles/images/secret metadata
Routine operator: fixed environment root -> dedicated network/host/disk
Disposal: environment only -> backend/bootstrap and evidence remain
```

**Implementation in progress:** backend is created/migrated and bootstrap's 19
inspected resources are applied. Dedicated generated credentials were initialized
via protected Secrets Manager API, never Terraform. Initial environment apply
created eight network/template resources but EC2 rejected the original host.
After reconciliation, an inspected policy-only bootstrap update and host-only
environment plan launched Free-plan-eligible `m7i-flex.large`. Actual host/disk/
metadata/profile/no-ingress settings and unchanged Free plan were verified.
The migrated host now runs the app after owner full-stack/four-test E2E and seeded
clean-state reset/retest proofs. Local Tasks 5–6 implement the locked validation
orchestrator and trusted host envelope; deployed transport/recovery, hosted
current-candidate validation, disposal/recreation and final merge validation remain
unfinished. Host runtime-input/evidence S3 grants are defined narrowly, pending an
inspected bootstrap apply.
Do not treat mock tests as proof of real IAM permissions, locking or isolation.
See [operational evidence](../../docs/TESTING-ENVIRONMENT.md) and the
[implementation plan](../../planning/aws-testing-environment-PLAN.md).

The owner accepts normal Actions App identity/scoped check writes, trusting
authorized repository maintainers and reviewed workflows; workflow changes require
review. No dedicated App is required. Exact candidate/manual authorization,
latest-attempt/failure enforcement and live gate proofs remain mandatory; this
decision does not complete runtime, disposal or merge protection.

Execution follows coherent batches -> local proof -> AWS trial, with fresh scoped
workstreams and independent subagent review/preparation when authorized. Observe CI
fail-fast at the first failed step; see
[automation execution feedback](../../docs/TESTING_STRATEGY.md#automation-execution-feedback).

## Fixed inputs and storage

Attempt generation must not alter the launch-template body: a numeric version
change forces EC2 replacement. Launch-time ownership tags are stable
`ManagedBy`/`Repository`; Terraform separately manages the primary interface's
mutable `Generation` tag, alongside instance/disk/network generation tags.
No lifecycle ignore suppresses template/configuration drift. The initial empty-
host migration is applied; subsequent tag-only attempts must not replace it.
Live update-only replanning is verified. The environment root includes
the interface tag resource as well as its nine original infrastructure resources.

The initial migration is applied: ten resources, same dedicated network, old
host/disk/interface removal verified and new eligible host bootstrapped. Live
follow-up exposed a second cause: changing the template **resource** tags makes
computed `latest_version` unknown, also forcing replacement. Its tags must be
stable too. The obsolete destructive hypothetical plan must never be applied;
tag-only owner alignment/fresh-plan proof is complete. No lifecycle ignore is introduced.

PR #82 merged at `7c22ba0` after CI. Owner recovery removed only the obsolete
template-resource `Generation` tag; launch-template version was read back
unchanged. The current saved plan/apply had zero resource changes; a fresh
hypothetical next-generation plan contained updates only, including the primary
interface tag, with no host/disk/interface replacement. That hypothetical plan
was not applied. The populated host/app and unchanged Free plan were preserved.

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

The disposable host is On-Demand Free-plan-eligible `m7i-flex.large`, pinned x86_64 AL2023 AMI
`ami-04478a3e21a0d79a7`, 50 GiB encrypted gp3 deleted on termination. No security
group ingress, SSH key, NAT, load balancer or managed DB exists in its definition.
Outbound-only public IPv4 is intentional; inspection will use SSM. IMDSv2/hop
limit alone is not container isolation. Explicit firewall/FORWARD rules and Auth/
E2E digest-image metadata probes passed after Docker restart and real reboot,
with host-role ECR/secret access retained on the initial host. Current-host full-
stack/E2E, clean-state and all-candidate metadata probes also passed. Frontend
IPv6 has no source address; that is not a routed firewall-denial proof.

## Verify definitions without AWS mutation

The manual **AWS operator plan proof** workflow is a separate guarded current-
environment trial, not validation/deployment. It authorizes both dispatch/rerun
actors before OIDC, takes the shared session concurrency group, verifies the
operator account/role, reads bounded current state/pointer under version/ETag
checks and refuses missing/empty/unexpected or mismatched state before init.
It copies only trusted Terraform inputs to a private runner directory and plans
with native S3 locking. Expected resource coverage and zero actions are mandatory;
it never applies, resets data or publishes AWS success. Only sanitized proof JSON
is an artifact; snapshots/plans/logs are private and must never be uploaded.
Timeout/failure requires lock reconciliation before retry. Missing-state stories
do not prove live recovery refusal. Actual operator no-change/native-lock proof
passed in trusted-main run `37245192463`; its sanitized report confirms no apply
or AWS validation success, and an owner read-only post-check found no remaining lock.
The first hosted operator trial passed authorization/OIDC but failed planning.
Failure evidence now exposes only fixed stages and allowlisted error/action
labels; raw snapshots/logs are never artifacts. Diagnose/reconcile before retry,
not by widening IAM permissions speculatively or exposing raw Terraform logs.
Focused reproduction found that the initial global 16 MiB file-size limit also
capped provider downloads. Capture now streams bounded stdout/stderr separately
and terminates the owned process group on overflow/deadline, without capping
legitimate provider artifacts. Large-provider/excessive-log stories and actual
credential-free backend-disabled init pass. The actual hosted retry passed without
IAM widening. Credential action metadata also exposed an ignored account-allowlist
input; reviewed v6.3.0 supports it. Hosted verification passed in `37246888048`,
again with zero resource changes/no apply/no AWS success.

### Account-plan preflight and partial apply

Before provisioning paid-only capacity, read the verified account's Free Tier
`GetAccountPlanState` (in `us-east-1`). An active Free plan can reject the chosen
instance even when the AMI, IAM and regional offerings are valid. This account
MUST remain on the Free plan. Never upgrade it. Check `DescribeInstanceTypes`
with `free-tier-eligible=true` and offerings for the configured AZ before selecting
capacity; regional availability alone does not prove account-plan eligibility.
The replacement selection is `m7i-flex.large` (two x86_64 vCPUs, 8 GiB), verified
eligible/offered in `eu-north-1a`. Environment validation and operator launch
policy now pin this selection; both plan assertions were observed RED then GREEN.
Live policy application/host launch and a sampled full-stack capacity run are
verified; sustained-load guarantees are not. Eligibility is not unlimited free usage.

Owner-only host setup proof command:
`python3 scripts/aws-host-setup.py --identifiers infra/aws/.runtime/bootstrap-identifiers.json --output infra/aws/.runtime/host-setup-operation.json`.
It verifies STS/host tags/profile/pinned size/AMI, SSM online/idle and unchanged
trusted-main runtime files; transfers only checksummed controller files in finite
chunks, not candidate scripts; runs bounded host setup under a host lock. Intent
and command IDs persist locally and in protected S3. Conditional writes refuse
replacing another cloud record. Unknown outcomes require reconciliation, not a
new output path. This owner proof is not shared-lock routine validation/disposal
or a successful AWS check; automated launch-gap/cancellation recovery is pending.

Read-only setup recovery:
`python3 scripts/aws-host-setup.py --reconcile --identifiers infra/aws/.runtime/bootstrap-identifiers.json --output infra/aws/.runtime/setup-command-reconciliation.json`.
Use a new protected output path: reports are create-only. It reads the exact
versioned setup record after a size preflight/conditional download. Missing IDs
are discovered by unique generation/stage comment, then verified against target,
document, command hash/timeout and actual terminal invocation. Missing/ambiguous/
active/cancelling commands refuse reconciliation. It never sends/cancels commands,
rewrites cloud records, clears runtime unknown state, authorizes retry or publishes
AWS success. A controlled live lost-ID version/discovery/restoration proof passed;
routine validation/disposal reconciliation must additionally handle Terraform,
EC2 transitions and detached tests before any mutation.

The first environment apply failed at EC2 `RunInstances` with
`InvalidParameterCombination: The specified instance type is not eligible for Free Tier`.
At that failed attempt, eight resources remained in versioned environment state
and host/disk/ENI were absent. The later eligible launch/migration has reconciled
that partial state; the current root has ten resources and a populated host.
Protected reconciliation receipt: `.runtime/partial-environment-reconciliation.json`.
Do not rerun the initial-absence checks or reuse the failed saved plan. After the
account prerequisite is resolved, reconcile recorded ownership/live resources,
refresh and inspect a new saved plan against this existing state, then apply only
that plan. Do not remove state or recreate the network to hide partial progress.

When comparing authoritative S3 state with `terraform state pull`, compare
resource/output identities, lineage, serial and tool version explicitly: pull may
normalize `check_results` without changing resource state. A whole-JSON mismatch
alone is not evidence of resource drift. Keep both raw records protected.

Terraform plan JSON may omit `resource_changes` for a no-op/refresh-only plan;
inspect it as an empty list when absent, and inspect `resource_drift` separately.
If a proof introduces drift before failing, reconcile/remove only its recorded
probe through a fresh inspected plan before attempting another proof.

Run from the repository root, separately for each root:

```bash
terraform fmt -check -recursive infra/aws
export TF_DATA_DIR="$(mktemp -d /tmp/opencode/aws-backend-definitions.XXXXXX)"
terraform -chdir=infra/aws/backend init -backend=false
terraform -chdir=infra/aws/backend validate
terraform -chdir=infra/aws/backend test
export TF_DATA_DIR="$(mktemp -d /tmp/opencode/aws-bootstrap-definitions.XXXXXX)"
terraform -chdir=infra/aws/bootstrap init -backend=false
terraform -chdir=infra/aws/bootstrap validate
terraform -chdir=infra/aws/bootstrap test
export TF_DATA_DIR="$(mktemp -d /tmp/opencode/aws-environment-definitions.XXXXXX)"
terraform -chdir=infra/aws/environment init -backend=false
terraform -chdir=infra/aws/environment validate
terraform -chdir=infra/aws/environment test
unset TF_DATA_DIR
```

Never initialize a live backend implicitly with an inferred bucket. An already
migrated working directory retains its backend even with `-backend=false`.
Fresh per-root `TF_DATA_DIR` directories above avoid cached live-backend metadata;
without them even initialization can require live credentials. Keep these scratch
directories separate from protected live state; never remove live `.terraform/`
to run definition checks. Mock-provider tests never administer the live stack.

## Owner backend creation and migration

Host recovery observations use the trusted runtime's `--reconcile` command under
its process lock. They can prove test absence but never clear unknown outcomes,
authorize retry, or replace cloud-side SSM/EC2/state reconciliation. See the
[runtime contract](runtime/AGENTS.md) for the command and refusal boundaries.
The controller's separate `reconcile-cloud` observation verifies fixed operator
state/lock/EC2/SSM reads and rechecks candidate/snapshot identities, never authorizing
mutation. It requires both host observation and persistent-ledger reconciliation.
The guarded operator proof now runs this cloud half before planning; actual updated
hosted proof passed `37275392787`. No new IAM permissions or arbitrary state roots are introduced.
The first cloud increment's live trial failed its lock HEAD lookup. Missing-object
HEAD can be forbidden under prefix-restricted bucket listing; it is not an absence
proof. The corrected check lists only the authorized exact lock prefix, at most
two keys, and refuses denial/truncation. No IAM widening is introduced. Updated
actual role retry passed; never treat HTTP 403 as absence or force-unlock.
Corrected actual operator trial `37275392787` passed scoped absence and bounded
state/EC2/SSM observations plus a no-change native-lock plan. It still requires
host/persistent-ledger reconciliation and cannot authorize mutation or AWS success.
Controller `verify-publication` independently authenticates the exact trusted
publisher receipt artifact and fixture bytes without AWS or code execution;
historical input verification is not current-candidate deployment evidence.

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
The local controller implements routine pointer protection/release; actual hosted
retention proof remains pending.

Read-only Access Analyzer validation found no policy errors. Its GitHub-specific
recommendation currently does not recognize the observed immutable-ID subject;
do not weaken exact trust to silence that warning. Eleven custom-policy
simulations passed launch-resource and isolation scenarios. These are not actual
OIDC assumed-role deny proofs; live role tests still precede routine deployment.

The [trusted runtime](runtime/AGENTS.md) has process-boundary coverage and an
actual local Compose reset proof. The routine controller only admits inspected
tag-only generation changes on the existing host; missing, disposed or partial
unexpected state fails closed (Task 7 recreation is not implemented). Immutable
intent/launched/terminal ledger events, predecessor-aware crash gaps and a CAS
generation pointer prevent lost-ID ambiguity from becoming an automatic retry.
Recovered-aborted requires terminal SSM invocation and fresh host lock/process/test/
temporary-credential absence; there is no automatic unlock. Host inputs use
create-only `operations/runtime-input/` objects and evidence uses the host-only
`operations/runtime-evidence/` boundary through a fixed hash-verified SSM bootstrap.
The exact nonsensitive `AWS_TESTING_SECRET_ARN` Environment variable is configured
and matches the protected bootstrap identity by read-only verification. See the
runtime module contract for bounds and refusal details.
Verified host predecessor identity survives repeated cloud tag-apply/admission
gaps. Durable exact admission transitions reconcile local write/launch
interruptions without overwriting predecessor history. Runtime produces
pending-success only; owned success follows independent uploaded-artifact/retention
verification and current/latest candidate recheck inside the same locked job.
The shared operator job holds `aws-testing-environment` with `queue: max`, no
cancellation, a 65-minute job bound and 7200-second credentials. Hosted
current-candidate success, deployed transport/recovery and final merge gate remain
unproved; sanitized evidence retention is at least 14 days. Prior/incoming image
retention is protected across failure; unknown owner tags remain, and failed history
is capped at 20 predecessors pending reviewed recovery.
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
